"""
plugins/start.py — /start, /help, /ping commands and navigation callbacks.
"""

import time
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message
import config
from database import db
from helpers import kb, fmt

_start_time = time.time()


@Client.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_start(client: Client, msg: Message):
    user = msg.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_premium=getattr(user, "is_premium", False) or False,
    )

    me = await client.get_me()
    is_admin_user = config.is_admin(user.id)

    # Check deep link
    if len(msg.command) > 1 and msg.command[1] == "botstart":
        await msg.reply_text(
            "🎉 <b>Bot added to chat successfully!</b>\n\n"
            "Ensure the bot has <b>'Invite Users via Link'</b> admin permission.\n"
            "Use /admin to open the settings control center.",
            reply_markup=kb.main(me.username, is_admin_user),
        )
        return

    text = (
        f"👋 <b>Hello {fmt.escape(user.first_name)}!</b>\n\n"
        "🤖 I am <b>Auto-Approve Bot</b>, the high-speed join request manager for Telegram Channels and Groups.\n\n"
        "<b>✨ Features:</b>\n"
        "• ⚡ <b>Instant / Delayed Approval:</b> Flood-protected token-bucket queue.\n"
        "• 🛡️ <b>Anti-Spam & Captcha:</b> 1-Click, Math & Emoji verification.\n"
        "• 🎨 <b>Rich Custom Welcome:</b> Media attachments, variables & inline buttons.\n"
        "• ⚡ <b>Mass Actions:</b> Bulk backlog approvals, declines & CSV export.\n"
        "• 📡 <b>Broadcast Suite:</b> Message all users with real-time progress.\n\n"
        "👉 Add me to your channel/group as admin, then send <b>/admin</b> to configure!"
    )
    await msg.reply_text(text, reply_markup=kb.main(me.username, is_admin_user))


@Client.on_message(filters.command("ping"))
async def cmd_ping(client: Client, msg: Message):
    start = time.monotonic()
    m = await msg.reply_text("🏓 <i>Pinging Telegram API...</i>")
    latency = (time.monotonic() - start) * 1000
    uptime_sec = int(time.time() - _start_time)
    hours, rem = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(rem, 60)

    await m.edit_text(
        f"🏓 <b>Pong!</b>\n"
        f"⚡ <b>API Latency:</b> <code>{latency:.1f}ms</code>\n"
        f"⏱️ <b>Uptime:</b> <code>{hours}h {minutes}m {seconds}s</code>"
    )


@Client.on_message(filters.command(["admin", "settings", "channels"]) & filters.private)
async def cmd_admin(client: Client, msg: Message):
    uid = msg.from_user.id
    owner_filter = uid if not config.is_admin(uid) else None
    chats = await db.all_chats(owner_id=owner_filter)

    if not chats:
        await msg.reply_text(
            "📢 <b>No channels or groups found.</b>\n\n"
            "1. Add the bot to your channel/group as an <b>Administrator</b>.\n"
            "2. Grant the <b>'Invite Users via Link'</b> permission.\n"
            "3. Once a join request arrives, your chat will appear here automatically!",
            reply_markup=kb.main((await client.get_me()).username, config.is_admin(uid)),
        )
        return

    await msg.reply_text(
        f"📢 <b>Managed Chats ({len(chats)} Total)</b>\n\nSelect a chat to configure:",
        reply_markup=kb.chat_list(chats, page=1),
    )


# ─── Navigation Callbacks ───────────────────────────────────────────────────
@Client.on_callback_query(filters.regex("^main$"))
async def cb_main(client: Client, q: CallbackQuery):
    me = await client.get_me()
    await q.message.edit_text(
        "🤖 <b>Auto-Approve Bot Control Center</b>\n\nManage channels, configure rules, and check stats below:",
        reply_markup=kb.main(me.username, config.is_admin(q.from_user.id)),
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^chats:(\d+)$"))
async def cb_chats(client: Client, q: CallbackQuery):
    page = int(q.matches[0].group(1))
    uid = q.from_user.id
    chats = await db.all_chats(owner_id=uid if not config.is_admin(uid) else None)

    if not chats:
        await q.message.edit_text(
            "📢 <b>No chats found.</b>\n\nAdd bot as admin with invite permissions to get started.",
            reply_markup=kb.back("main"),
        )
        await q.answer()
        return

    await q.message.edit_text(
        f"📢 <b>Managed Chats ({len(chats)} Total)</b>\n\nSelect a chat to manage:",
        reply_markup=kb.chat_list(chats, page),
    )
    await q.answer()


@Client.on_callback_query(filters.regex("^help$"))
async def cb_help(client: Client, q: CallbackQuery):
    text = (
        "📖 <b>How to Use Auto-Approve Bot</b>\n\n"
        "1️⃣ <b>Add bot as Admin:</b> In your channel or supergroup with <i>Invite Users via Link</i> permission.\n"
        "2️⃣ <b>Create Join Request Link:</b> Channel Settings ➔ Invite Links ➔ Create Link with <i>Request Admin Approval</i> enabled.\n"
        "3️⃣ <b>Configure Settings:</b> Send /admin in private chat to toggle Auto-Approval, Captcha, Delays, and Custom Welcome Messages.\n\n"
        "<b>Commands Index:</b>\n"
        "• /start — Open main menu\n"
        "• /admin — Open channel control center\n"
        "• /stats — View global approval metrics\n"
        "• /ping — Test latency & uptime\n"
        "• /broadcast — Broadcast message to all users"
    )
    await q.message.edit_text(text, reply_markup=kb.back("main"))
    await q.answer()


@Client.on_callback_query(filters.regex("^noop$"))
async def cb_noop(client: Client, q: CallbackQuery):
    await q.answer()
