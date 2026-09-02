"""
plugins/pending_request.py — Rebuilt /approveall and /queue command suite.

Implements rate-limited backlog join request processing, live progress tracking,
exact limit enforcement, and queue status inspection.
"""

import datetime
from typing import Optional
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType
import config
from config import LOGGER
from database import db
from core.permissions import can_manage_chat
from core.queue_manager import queue_manager, ApprovalJob
from helpers import style

UTC = datetime.timezone.utc


def _queue_status_text(title: str, status: dict) -> str:
    pos = status["position"]
    pos_str = f"{style.sc('Active')} (#1)" if pos == 1 else f"{style.sc('Queued')} (#{pos})"
    started_str = status["started_at"].strftime("%H:%M:%S UTC") if status.get("started_at") else "Pending"
    limit_str = f"{status['limit']:,}" if status.get("limit") else style.sc("Unlimited")
    bar = _format_progress_bar(status["approved"], status.get("limit"))
    job_id = status["job_id"]
    waiting = status["waiting"]
    eta = status["eta_seconds"]
    approved = status["approved"]
    processed = status["processed"]
    return (
        f"{style.h('Queue Status')} — <b>{title}</b>\n\n"
        f"• {style.kv('Job ID', f'<code>{job_id}</code>')}\n"
        f"• {style.kv('Position', f'<b>{pos_str}</b>')}\n"
        f"• {style.kv('Jobs Ahead', f'<b>{waiting}</b>')}\n"
        f"• {style.kv('Started At', f'<code>{started_str}</code>')}\n"
        f"• {style.kv('ETA', f'<code>{eta}s</code>')}\n\n"
        f"[{bar}]\n"
        f"• {style.kv('Approved', f'<b>{approved:,}</b> / {limit_str}')}\n"
        f"• {style.kv('Processed', f'<b>{processed:,}</b>')}"
    )


def _format_progress_bar(current: int, total: Optional[int], length: int = 10) -> str:
    """Generate a clean visual progress bar."""
    if not total or total <= 0:
        filled = min(length, (current % (length * 2)) // 2 + 1)
        return "█" * filled + "░" * (length - filled)
    pct = min(1.0, current / total)
    filled = int(pct * length)
    return "█" * filled + "░" * (length - filled)


# ─── /approveall Command ───────────────────────────────────────────────────
@Client.on_message(filters.command(["approveall", "bulk_approve"]))
async def cmd_approveall(client: Client, msg: Message):
    user = msg.from_user
    args = msg.command[1:] if len(msg.command) > 1 else []

    target_chat_id: Optional[int] = None
    limit: Optional[int] = None

    # Parse arguments: /approveall [chat_id] [limit] OR /approveall [limit] in groups
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        target_chat_id = msg.chat.id
        if args:
            raw_limit = args[0].lower()
            if raw_limit not in ("unlimited", "all", "inf", "0"):
                if raw_limit.isdigit() and int(raw_limit) > 0:
                    limit = int(raw_limit)
    else:
        # Private chat
        if not args:
            await msg.reply_text(
                f"{style.h('Mass Approval')}  <code>/approveall</code>\n\n"
                "Approve pending join requests across your channels.\n\n"
                f"{style.l('Usage')}\n"
                "• <code>/approveall &lt;chat_id&gt; [limit]</code>\n"
                "• <code>/approveall -1001234567890 50</code> (approve 50)\n"
                "• <code>/approveall -1001234567890 unlimited</code> (approve all)\n\n"
                "<i>You can find your Channel IDs in /admin.</i>"
            )
            return

        # First arg is chat_id or limit?
        arg0 = args[0]
        if arg0.startswith("-") and arg0[1:].isdigit():
            target_chat_id = int(arg0)
            if len(args) > 1:
                raw_lim = args[1].lower()
                if raw_lim not in ("unlimited", "all", "inf", "0") and raw_lim.isdigit():
                    limit = int(raw_lim)
        elif arg0.isdigit():
            target_chat_id = int(f"-100{arg0}" if len(arg0) == 10 else arg0)
            if len(args) > 1 and args[1].isdigit():
                limit = int(args[1])
        else:
            await msg.reply_text(f"{style.h('Invalid chat ID')}. Provide a numeric Telegram chat/channel ID.")
            return

    # Check caller permissions
    has_perm = await can_manage_chat(user.id, target_chat_id, client)
    if not has_perm:
        await msg.reply_text(f"{style.h('Access Denied')}: You must be an administrator of this chat to run /approveall.")
        return

    if queue_manager.is_running(target_chat_id):
        await msg.reply_text(
            f"{style.h('Job already running')} for chat <code>{target_chat_id}</code>.\n"
            f"Use /queue <code>{target_chat_id}</code> to inspect progress."
        )
        return

    cfg = await db.get_chat(target_chat_id)
    chat_title = cfg.get("title", f"Chat {target_chat_id}") if cfg else f"Chat {target_chat_id}"

    limit_label = f"<b>{limit:,}</b>" if limit is not None else f"<b>{style.sc('Unlimited')}</b>"
    status_msg = await msg.reply_text(
        f"{style.h('Initiating Approval Queue')}\n\n"
        f"{style.kv('Target', f'{chat_title} (<code>{target_chat_id}</code>)')}\n"
        f"{style.kv('Limit', limit_label)}\n"
        f"<i>Connecting to backlog...</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(style.btn("Cancel Queue"), callback_data=f"cancel_queue:{target_chat_id}")]
        ]),
    )

    last_edit_time = [0.0]

    async def live_progress_callback(job: ApprovalJob):
        now_ts = datetime.datetime.now(UTC).timestamp()
        if now_ts - last_edit_time[0] < 2.5 and job.status == "running" and not (job.limit and job.approved >= job.limit):
            return
        last_edit_time[0] = now_ts

        bar = _format_progress_bar(job.approved, job.limit)
        eta_str = f"{job.to_dict().get('eta_seconds', 0)}s" if job.limit else "Calculating..."

        if job.status == "running":
            text = (
                f"{style.h('Approval Queue in Progress')}\n\n"
                f"{style.kv('Target', chat_title)}\n"
                f"[{bar}] <b>{job.approved:,}</b> / {f'{job.limit:,}' if job.limit else style.sc('Unlimited')}\n\n"
                f"{style.kv('Approved', f'<code>{job.approved:,}</code>')}\n"
                f"{style.kv('Processed', f'<code>{job.processed:,}</code>')}\n"
                f"{style.kv('ETA', f'<code>{eta_str}</code>')}"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(style.btn("Cancel Queue"), callback_data=f"cancel_queue:{job.chat_id}")]
            ])
        elif job.status == "completed":
            text = (
                f"{style.h('Approval Queue Complete')}\n\n"
                f"{style.kv('Target', chat_title)}\n"
                f"{style.kv('Successfully Approved', f'<b>{job.approved:,}</b> requests')}\n"
                f"{style.kv('Total Processed', f'<b>{job.processed:,}</b>')}\n"
                f"{style.kv('Duration', str(int((datetime.datetime.now(UTC) - (job.started_at or job.created_at)).total_seconds())) + 's')}"
            )
            markup = None
        elif job.status == "cancelled":
            text = (
                f"{style.h('Approval Queue Cancelled')}\n\n"
                f"{style.kv('Target', chat_title)}\n"
                f"{style.kv('Approved before stop', f'<b>{job.approved:,}</b>')}\n"
                f"{style.kv('Processed', f'<b>{job.processed:,}</b>')}"
            )
            markup = None
        else:
            err = job.error or "N/A"
            text = (
                f"{style.h(f'Approval Queue Finished ({job.status})')}\n\n"
                f"{style.kv('Approved', f'<b>{job.approved:,}</b>')}\n"
                f"{style.kv('Error detail', f'<code>{err}</code>')}"
            )
            markup = None

        try:
            await status_msg.edit_text(text, reply_markup=markup)
        except Exception:
            pass

    # Launch in queue manager
    await queue_manager.enqueue(
        chat_id=target_chat_id,
        limit=limit,
        requested_by=user.id,
        client=client,
        progress_callback=live_progress_callback,
    )


# ─── /queue Command ─────────────────────────────────────────────────────────
@Client.on_message(filters.command(["queue", "queue_status", "q"]))
async def cmd_queue(client: Client, msg: Message):
    user = msg.from_user
    args = msg.command[1:] if len(msg.command) > 1 else []

    target_chat_id: Optional[int] = None
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        target_chat_id = msg.chat.id
    elif args and (args[0].startswith("-") or args[0].isdigit()):
        target_chat_id = int(args[0])

    if target_chat_id:
        status = queue_manager.get_status(target_chat_id)
        if not status:
            await msg.reply_text(
                f"{style.h('No active approval queue')} for chat <code>{target_chat_id}</code>.\n\n"
                f"Start an approval job with <code>/approveall {target_chat_id}</code>."
            )
            return

        cfg = await db.get_chat(target_chat_id)
        title = cfg.get("title", f"Chat {target_chat_id}") if cfg else f"Chat {target_chat_id}"

        pos = status["position"]
        pos_str = f"{style.sc('Active')} (#1)" if pos == 1 else f"{style.sc('Queued')} (#{pos})"
        started_str = status["started_at"].strftime("%H:%M:%S UTC") if status.get("started_at") else "Pending"
        limit_str = f"{status['limit']:,}" if status.get("limit") else style.sc("Unlimited")
        bar = _format_progress_bar(status["approved"], status.get("limit"))

        text = (
            f"{style.h('Queue Status')} — <b>{title}</b>\n\n"
            f"• {style.kv('Job ID', f'<code>{status[\"job_id\"]}</code>')}\n"
            f"• {style.kv('Position', f'<b>{pos_str}</b>')}\n"
            f"• {style.kv('Jobs Ahead', f'<b>{status[\"waiting\"]}</b>')}\n"
            f"• {style.kv('Started At', f'<code>{started_str}</code>')}\n"
            f"• {style.kv('ETA', f'<code>{status[\"eta_seconds\"]}s</code>')}\n\n"
            f"[{bar}]\n"
            f"• {style.kv('Approved', f'<b>{status[\"approved\"]:,}</b> / {limit_str}')}\n"
            f"• {style.kv('Processed', f'<b>{status[\"processed\"]:,}</b>')}"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(style.btn("Refresh"), callback_data=f"queue_refresh:{target_chat_id}")],
            [InlineKeyboardButton(style.btn("Cancel Job"), callback_data=f"cancel_queue:{target_chat_id}")],
        ])
        await msg.reply_text(text, reply_markup=markup)
        return

    # List all active queues for the user
    active_jobs = queue_manager.get_all_active_jobs()
    if not active_jobs:
        await msg.reply_text(
            "📊 <b>Global Approval Queue</b>\n\n"
            "ℹ️ No active approval jobs are currently running.\n\n"
            "Use <code>/approveall &lt;chat_id&gt; [limit]</code> to initiate approvals."
        )
        return

    lines = ["📊 <b>Active Approval Queues</b>\n"]
    for j in active_jobs:
        cid = j["chat_id"]
        cfg = await db.get_chat(cid)
        title = cfg.get("title", f"Chat {cid}") if cfg else f"Chat {cid}"
        lines.append(
            f"• <b>#{j['position']} {title}</b> (<code>{cid}</code>)\n"
            f"  ✅ Approved: {j['approved']:,} | ETA: {j['eta_seconds']}s | Status: {j['status']}"
        )

    lines.append("\n<i>Send /queue &lt;chat_id&gt; for full details on a specific chat.</i>")
    await msg.reply_text("\n".join(lines))


# ─── Callbacks ──────────────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^cancel_queue:(-?\d+)$"))
async def cb_cancel_queue(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    has_perm = await can_manage_chat(q.from_user.id, chat_id, client)
    if not has_perm:
        await q.answer("❌ You lack admin permission to cancel this queue.", show_alert=True)
        return

    cancelled = queue_manager.cancel_job(chat_id)
    if cancelled:
        await q.answer("Approval queue cancellation signaled!", show_alert=True)
    else:
        await q.answer("Job already completed or not active.", show_alert=False)


@Client.on_callback_query(filters.regex(r"^queue_refresh:(-?\d+)$"))
async def cb_queue_refresh(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    status = queue_manager.get_status(chat_id)
    if not status:
        await q.answer("Queue job finished!", show_alert=True)
        await q.message.edit_text(f"ℹ️ Approval queue for chat <code>{chat_id}</code> is no longer active.")
        return

    await q.answer("Queue refreshed.")
    cfg = await db.get_chat(chat_id)
    title = cfg.get("title", f"Chat {chat_id}") if cfg else f"Chat {chat_id}"
    pos = status["position"]
    pos_str = "🟢 Active (#1)" if pos == 1 else f"⏳ Queued (#{pos})"
    started_str = status["started_at"].strftime("%H:%M:%S UTC") if status.get("started_at") else "Pending"
    limit_str = f"{status['limit']:,}" if status.get("limit") else "Unlimited ♾️"
    bar = _format_progress_bar(status["approved"], status.get("limit"))

    text = (
        f"📊 <b>Queue Status — {title}</b>\n\n"
        f"• <b>Job ID:</b> <code>{status['job_id']}</code>\n"
        f"• <b>Position:</b> <b>{pos_str}</b>\n"
        f"• <b>Jobs Ahead:</b> <b>{status['waiting']}</b>\n"
        f"• <b>Started At:</b> <code>{started_str}</code>\n"
        f"• <b>ETA:</b> <code>{status['eta_seconds']}s</code>\n\n"
        f"[{bar}]\n"
        f"• <b>Approved:</b> <b>{status['approved']:,}</b> / {limit_str}\n"
        f"• <b>Processed:</b> <b>{status['processed']:,}</b>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"queue_refresh:{chat_id}")],
        [InlineKeyboardButton("🛑 Cancel Job", callback_data=f"cancel_queue:{chat_id}")],
    ])
    try:
        await q.message.edit_text(text, reply_markup=markup)
    except Exception:
        pass

