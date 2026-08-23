"""
plugins/stats.py — Global statistics and channel-level metrics.
"""

import platform
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from database import db
from helpers import fmt


@Client.on_message(filters.command("stats") & filters.private)
async def cmd_stats(client: Client, msg: Message):
    s = await db.global_stats()
    text = (
        "📊 <b>Bot Global Analytics</b>\n\n"
        f"👥 <b>Total Users:</b> {s.get('users', 0):,}\n"
        f"📢 <b>Managed Chats:</b> {s.get('chats', 0):,}\n"
        f"✅ <b>Requests Approved:</b> {s.get('approved', 0):,}\n"
        f"🚫 <b>Spam Rejected:</b> {s.get('rejected', 0):,}\n\n"
        f"🐍 <b>Python:</b> {platform.python_version()} | <b>OS:</b> {platform.system()}"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Stats", callback_data="global_stats")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
    ])
    await msg.reply_text(text, reply_markup=markup)


@Client.on_callback_query(filters.regex("^global_stats$"))
async def cb_global_stats(client: Client, q: CallbackQuery):
    s = await db.global_stats()
    text = (
        "📊 <b>Bot Global Analytics</b>\n\n"
        f"👥 <b>Total Users:</b> {s.get('users', 0):,}\n"
        f"📢 <b>Managed Chats:</b> {s.get('chats', 0):,}\n"
        f"✅ <b>Requests Approved:</b> {s.get('approved', 0):,}\n"
        f"🚫 <b>Spam Rejected:</b> {s.get('rejected', 0):,}\n\n"
        f"🐍 <b>Python:</b> {platform.python_version()} | <b>OS:</b> {platform.system()}"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Stats", callback_data="global_stats")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
    ])
    await q.message.edit_text(text, reply_markup=markup)
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
        f"📊 <b>Analytics — {fmt.escape(title)}</b>\n"
        f"🆔 <code>{chat_id}</code>\n\n"
        f"✅ <b>Approved:</b> {approved:,}\n"
        f"🚫 <b>Rejected:</b> {rejected:,}\n"
        f"📈 <b>Approval Rate:</b> {rate:.1f}%\n"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Chat", callback_data=f"chat:{chat_id}")]
    ])
    await q.message.edit_text(text, reply_markup=markup)
    await q.answer()

