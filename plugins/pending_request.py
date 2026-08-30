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

UTC = datetime.timezone.utc


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
                "⚡ <b>Mass Approval (/approveall)</b>\n\n"
                "Approve pending join requests across your channels.\n\n"
                "👉 <b>Usage:</b>\n"
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
            await msg.reply_text("⚠️ <b>Invalid chat ID.</b> Please provide a numeric Telegram chat/channel ID.")
            return

    # Check caller permissions
    has_perm = await can_manage_chat(user.id, target_chat_id, client)
    if not has_perm:
        await msg.reply_text("❌ <b>Access Denied:</b> You must be an Administrator of this chat to run /approveall.")
        return

    if queue_manager.is_running(target_chat_id):
        await msg.reply_text(
            f"⚠️ An approval job is already running for chat <code>{target_chat_id}</code>.\n"
            f"Use /queue <code>{target_chat_id}</code> to inspect progress."
        )
        return

    cfg = await db.get_chat(target_chat_id)
    chat_title = cfg.get("title", f"Chat {target_chat_id}") if cfg else f"Chat {target_chat_id}"

    limit_label = f"<b>{limit:,}</b>" if limit is not None else "<b>Unlimited ♾️</b>"
    status_msg = await msg.reply_text(
        f"⚡ <b>Initiating Approval Queue...</b>\n\n"
        f"📢 <b>Target:</b> {chat_title} (<code>{target_chat_id}</code>)\n"
        f"🎯 <b>Limit:</b> {limit_label}\n"
        f"⏳ <i>Connecting to backlog...</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 Cancel Queue", callback_data=f"cancel_queue:{target_chat_id}")]
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
                f"⚡ <b>Approval Queue in Progress...</b>\n\n"
                f"📢 <b>Target:</b> {chat_title}\n"
                f"[{bar}] <b>{job.approved:,}</b> / {f'{job.limit:,}' if job.limit else '♾️'}\n\n"
                f"✅ <b>Approved:</b> <code>{job.approved:,}</code>\n"
                f"⏳ <b>Processed:</b> <code>{job.processed:,}</code>\n"
                f"⏱️ <b>ETA:</b> <code>{eta_str}</code>"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🛑 Cancel Queue", callback_data=f"cancel_queue:{job.chat_id}")]
            ])
        elif job.status == "completed":
            text = (
                f"🎉 <b>Approval Queue Complete!</b>\n\n"
                f"📢 <b>Target:</b> {chat_title}\n"
                f"✅ <b>Successfully Approved:</b> <b>{job.approved:,}</b> requests\n"
                f"⏳ <b>Total Processed:</b> <b>{job.processed:,}</b>\n"
                f"⏱️ <b>Duration:</b> {int((datetime.datetime.now(UTC) - (job.started_at or job.created_at)).total_seconds())}s"
            )
            markup = None
        elif job.status == "cancelled":
            text = (
                f"🛑 <b>Approval Queue Cancelled</b>\n\n"
                f"📢 <b>Target:</b> {chat_title}\n"
                f"✅ <b>Approved before stop:</b> <b>{job.approved:,}</b>\n"
                f"⏳ <b>Processed:</b> <b>{job.processed:,}</b>"
            )
            markup = None
        else:
            text = (
                f"⚠️ <b>Approval Queue Finished ({job.status})</b>\n\n"
                f"✅ <b>Approved:</b> <b>{job.approved:,}</b>\n"
                f"⚠️ <b>Error detail:</b> <code>{job.error or 'N/A'}</code>"
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
                f"ℹ️ <b>No active approval queue</b> for chat <code>{target_chat_id}</code>.\n\n"
                f"Start an approval job with <code>/approveall {target_chat_id}</code>."
            )
            return

        cfg = await db.get_chat(target_chat_id)
        title = cfg.get("title", f"Chat {target_chat_id}") if cfg else f"Chat {target_chat_id}"

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
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"queue_refresh:{target_chat_id}")],
            [InlineKeyboardButton("🛑 Cancel Job", callback_data=f"cancel_queue:{target_chat_id}")],
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

