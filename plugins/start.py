"""
plugins/start.py — /start, /help, /ping commands and navigation callbacks.
"""

import random
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message
import config
from database import db
from helpers import kb, fmt, style, ui


def _start_caption(first_name: str) -> str:
    return (
        f"{style.h(f'Hello, {first_name}')}\n\n"
        f"I am {style.l('Auto-Approve Bot')} — the high-speed join request manager "
        f"for Telegram channels and groups.\n\n"
        f"{style.h('Features')}\n"
        f"• {style.l('Instant / Delayed Approval')} — Flood-protected token-bucket queue.\n"
        f"• {style.l('Anti-Spam & Captcha')} — 1-Click, math and emoji verification.\n"
        f"• {style.l('Custom Welcome')} — Media, variables and inline buttons.\n"
        f"• {style.l('Mass Actions')} — Bulk backlog approvals, declines and CSV export.\n"
        f"• {style.l('Broadcast Suite')} — Message all users with live progress.\n\n"
        f"Add me to your channel or group as admin, then send {style.l('/admin')} to configure."
    )


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

    if len(msg.command) > 1 and msg.command[1] == "botstart":
        await ui.reply(
            msg,
            f"{style.h('Bot added successfully')}\n\n"
            f"Ensure the bot has {style.l('Invite Users via Link')} admin permission.\n"
            f"Use /admin to open the settings control center.",
            reply_markup=kb.main(me.username, is_admin_user),
            photo=random.choice(config.START_PICS) if config.START_PICS else None,
        )
        return

    await ui.reply(
        msg,
        _start_caption(user.first_name or "there"),
        reply_markup=kb.main(me.username, is_admin_user),
        photo=random.choice(config.START_PICS) if config.START_PICS else None,
    )


# NOTE: /ping used to be registered here too, duplicating stats.py's
# ["ping", "health", "system"] handler. Since both matched plain /ping and
# ran in the same handler group, this one always shadowed the richer
# diagnostics version in stats.py. Removed — stats.py is now the single
# owner of /ping, /health, /system.


@Client.on_message(filters.command(["admin", "settings"]) & filters.private)
async def cmd_admin(client: Client, msg: Message):
    uid = msg.from_user.id
    owner_filter = uid if not config.is_admin(uid) else None
    chats = await db.all_chats(owner_id=owner_filter)

    if not chats:
        await msg.reply_text(
            f"{style.h('No channels or groups found')}\n\n"
            f"1. Add the bot to your channel or group as an {style.l('Administrator')}.\n"
            f"2. Grant the {style.l('Invite Users via Link')} permission.\n"
            f"3. Once a join request arrives, your chat will appear here automatically.",
            reply_markup=kb.main((await client.get_me()).username, config.is_admin(uid)),
        )
        return

    await msg.reply_text(
        f"{style.h(f'Managed Chats ({len(chats)} Total)')}\n\nSelect a chat to configure:",
        reply_markup=kb.chat_list(chats, page=1),
    )


@Client.on_callback_query(filters.regex("^main$"))
async def cb_main(client: Client, q: CallbackQuery):
    me = await client.get_me()
    await ui.edit(
        q.message,
        f"{style.h('Auto-Approve Bot')}\n\n"
        "Manage channels, configure rules, and review analytics below.",
        reply_markup=kb.main(me.username, config.is_admin(q.from_user.id)),
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^chats:(\d+)$"))
async def cb_chats(client: Client, q: CallbackQuery):
    page = int(q.matches[0].group(1))
    uid = q.from_user.id
    chats = await db.all_chats(owner_id=uid if not config.is_admin(uid) else None)

    if not chats:
        await ui.edit(
            q.message,
            f"{style.h('No chats found')}\n\nAdd the bot as admin with invite permissions to get started.",
            reply_markup=kb.back("main"),
        )
        await q.answer()
        return

    await ui.edit(
        q.message,
        f"{style.h(f'Managed Chats ({len(chats)} Total)')}\n\nSelect a chat to manage:",
        reply_markup=kb.chat_list(chats, page),
    )
    await q.answer()


@Client.on_callback_query(filters.regex("^help$"))
async def cb_help(client: Client, q: CallbackQuery):
    text = (
        f"{style.h('How to Use Auto-Approve Bot')}\n\n"
        f"{style.l('1.')} Add the bot as Admin with {style.l('Invite Users via Link')} permission.\n"
        f"{style.l('2.')} Invite Links → Create Link → enable {style.l('Request Admin Approval')}.\n"
        f"{style.l('3.')} Send /admin in PM to configure auto-approval, captcha, delay & welcome.\n\n"
        f"{style.h('Commands')}\n"
        "• /start, /plan, /managechnls, /admin — menus\n"
        "• /defaults — set & apply default settings to all your channels\n"
        "• /approveall [chat_id] [limit] — bulk approve backlog\n"
        "• /queue [chat_id] — live queue & ETA\n"
        "• /schedule, /schedules — scheduled approvals\n"
        "• /login, /logout, /sessions — user session\n"
        "• /stats, /ping — diagnostics\n"
        "• /broadcast, /logs — admin tools"
    )
    await ui.edit(q.message, text, reply_markup=kb.back("main"))
    await q.answer()


@Client.on_callback_query(filters.regex("^noop$"))
async def cb_noop(client: Client, q: CallbackQuery):
    await q.answer()