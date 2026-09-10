"""
plugins/stats.py — Global statistics, system health ping, and channel-level metrics.

Implements /ping and /stats commands reporting DB latency, queue jobs, scheduler health,
and approval history.
"""

import platform
import time
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from database import db
from core.recovery import get_db_latency_ms, get_last_approval_timestamp
from core.queue_manager import queue_manager
from helpers import fmt, style, ui


def _global_stats_text(s: dict, db_latency: float) -> str:
    users = f"{s.get('users', 0):,}"
    chats = f"{s.get('chats', 0):,}"
    approved = f"{s.get('approved', 0):,}"
    rejected = f"{s.get('rejected', 0):,}"
    db_lat_str = f"{db_latency}ms" if db_latency >= 0 else "Disconnected"

    last_appr = get_last_approval_timestamp()
    last_appr_str = last_appr.strftime("%H:%M:%S UTC") if last_appr else "N/A"

    return (
        f"{style.h('Bot Global Analytics')}\n\n"
        f"{style.kv('Total Users', users)}\n"
        f"{style.kv('Managed Chats', chats)}\n"
        f"{style.kv('Requests Approved', approved)}\n"
        f"{style.kv('Spam Rejected', rejected)}\n\n"
        f"{style.kv('Database Latency', f'<code>{db_lat_str}</code>')}\n"
        f"{style.kv('Last Approval', f'<code>{last_appr_str}</code>')}\n"
        f"{style.kv('Python', platform.python_version())}  ·  {style.kv('OS', platform.system())}"
    )


def _global_stats_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(style.btn("Refresh Stats"), callback_data="global_stats")],
        [InlineKeyboardButton(style.btn("Main Menu"), callback_data="main")],
    ])


@Client.on_message(filters.command("stats") & filters.private)
async def cmd_stats(client: Client, msg: Message):
    s = await db.global_stats()
    db_lat = await get_db_latency_ms()
    await msg.reply_text(_global_stats_text(s, db_lat), reply_markup=_global_stats_markup())


@Client.on_callback_query(filters.regex("^global_stats$"))
async def cb_global_stats(client: Client, q: CallbackQuery):
    s = await db.global_stats()
    db_lat = await get_db_latency_ms()
    await ui.edit(q.message, _global_stats_text(s, db_lat), reply_markup=_global_stats_markup())
    await q.answer()


# ─── /ping Command (Enhanced Reliability Diagnostics) ──────────────────────
@Client.on_message(filters.command(["ping", "health", "system"]))
async def cmd_ping(client: Client, msg: Message):
    start_t = time.monotonic()
    db_latency = await get_db_latency_ms()
    telegram_latency = round((time.monotonic() - start_t) * 1000.0, 2)

    active_jobs = queue_manager.get_all_active_jobs()
    active_count = len(active_jobs)
    queued_count = sum(1 for j in active_jobs if j.get("position", 1) > 1)

    from core.scheduler import scheduler
    sched_status = "Running 🟢" if getattr(scheduler, "_running", False) else "Stopped 🛑"

    last_appr = get_last_approval_timestamp()
    last_appr_str = last_appr.strftime("%Y-%m-%d %H:%M:%S UTC") if last_appr else "No approvals yet"

    text = (
        f"{style.h('System Diagnostics & Latency')}\n\n"
        f"• {style.kv('Telegram Latency', f'<code>{telegram_latency}ms</code> ⚡')}\n"
        f"• {style.kv('MongoDB Latency', f'<code>{db_latency}ms</code> 🗄️' if db_latency >= 0 else '<code>Disconnected</code> ❌')}\n"
        f"• {style.kv('Active Queue Jobs', f'<b>{active_count}</b> active (<b>{queued_count}</b> queued)')}\n"
        f"• {style.kv('Scheduler Engine', sched_status)}\n"
        f"• {style.kv('Last Successful Approval', f'<code>{last_appr_str}</code>')}"
    )
    await msg.reply_text(text)


@Client.on_callback_query(filters.regex(r"^chat_stats:(-?\d+)$"))
async def cb_chat_stats(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    cfg = await db.get_chat(chat_id)
    if not cfg:
        await q.answer("Chat not found!", show_alert=True)
        return

    title = cfg.get("title", f"Chat {chat_id}")
    stats = cfg.get("stats", {})
    approved = stats.get("approved", 0)
    rejected = stats.get("rejected", 0)
    total = approved + rejected
    rate = (approved / total * 100) if total > 0 else 100.0

    text = (
        f"{style.h('Analytics')} — <b>{fmt.escape(title)}</b>\n"
        f"<code>{chat_id}</code>\n\n"
        f"{style.kv('Approved', f'{approved:,}')}\n"
        f"{style.kv('Rejected', f'{rejected:,}')}\n"
        f"{style.kv('Approval Rate', f'{rate:.1f}%')}"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(style.btn("Back to Chat"), callback_data=f"mchnls_chat:{chat_id}")]
    ])
    await ui.edit(q.message, text, reply_markup=markup)
    await q.answer()