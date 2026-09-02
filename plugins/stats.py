"""
plugins/stats.py — Global statistics and channel-level metrics.
"""

import platform
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from database import db
from helpers import fmt, style, ui


def _global_stats_text(s: dict) -> str:
    users = f"{s.get('users', 0):,}"
    chats = f"{s.get('chats', 0):,}"
    approved = f"{s.get('approved', 0):,}"
    rejected = f"{s.get('rejected', 0):,}"
    return (
        f"{style.h('Bot Global Analytics')}\n\n"
        f"{style.kv('Total Users', users)}\n"
        f"{style.kv('Managed Chats', chats)}\n"
        f"{style.kv('Requests Approved', approved)}\n"
        f"{style.kv('Spam Rejected', rejected)}\n\n"
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
    await msg.reply_text(_global_stats_text(s), reply_markup=_global_stats_markup())


@Client.on_callback_query(filters.regex("^global_stats$"))
async def cb_global_stats(client: Client, q: CallbackQuery):
    s = await db.global_stats()
    await ui.edit(q.message, _global_stats_text(s), reply_markup=_global_stats_markup())
    await q.answer()


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
        [InlineKeyboardButton(style.btn("Back to Chat"), callback_data=f"chat:{chat_id}")]
    ])
    await ui.edit(q.message, text, reply_markup=markup)
    await q.answer()
