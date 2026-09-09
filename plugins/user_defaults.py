"""
plugins/user_defaults.py — /defaults: a per-user settings template that can
be pushed to (and overwrite) every channel the user manages in one tap, so
they don't have to configure auto-approve/captcha/delay/filters one channel
at a time.

Storage: a small, self-contained Motor collection ("user_defaults") using
the same config.MONGO_URL / config.DATABASE_NAME the rest of the bot
already connects with — kept independent of database.py's internals
(never reviewed) so this can't collide with or break anything there.
"""

from typing import Optional
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
import config
from config import LOGGER
from database import db
from core.cache import invalidate_chat
from helpers import style, ui

_mongo = AsyncIOMotorClient(config.MONGO_URL)
_prefs_col = _mongo[config.DATABASE_NAME]["user_defaults"]

_DEFAULT_TEMPLATE = {
    "auto_approve": True,
    "captcha": False,
    "delay": 0,
    "require_pfp": False,
    "cas_check": True,
}


async def get_user_defaults(user_id: int) -> dict:
    doc = await _prefs_col.find_one({"user_id": user_id})
    if doc and "defaults" in doc:
        merged = dict(_DEFAULT_TEMPLATE)
        merged.update(doc["defaults"])
        return merged
    return dict(_DEFAULT_TEMPLATE)


async def set_user_defaults(user_id: int, defaults: dict):
    await _prefs_col.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "defaults": defaults}},
        upsert=True,
    )


def _render_text(d: dict) -> str:
    delay_str = f"{d['delay']}s"
    return (
        f"{style.h('Your Default Channel Settings')}\n\n"
        "This template is yours — set it once, then push it to every "
        "channel you manage instead of configuring each one by hand.\n\n"
        f"• {style.kv('Auto-Approve', style.on(d['auto_approve']))}\n"
        f"• {style.kv('Captcha', style.on(d['captcha']))}\n"
        f"• {style.kv('Approval Delay', delay_str)}\n"
        f"• {style.kv('Require Avatar', style.on(d['require_pfp']))}\n"
        f"• {style.kv('Anti-Spam CAS', style.on(d['cas_check']))}\n\n"
        "<i>Toggle below, then tap Apply to overwrite these on all your channels.</i>"
    )


def _markup(d: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{style.btn('Auto-Approve')} · {style.on(d['auto_approve'])}", callback_data="udef_tgl:aa"),
            InlineKeyboardButton(f"{style.btn('Captcha')} · {style.on(d['captcha'])}", callback_data="udef_tgl:cap"),
        ],
        [
            InlineKeyboardButton(f"{style.btn('Require Avatar')} · {style.on(d['require_pfp'])}", callback_data="udef_tgl:pfp"),
            InlineKeyboardButton(f"{style.btn('Anti-Spam CAS')} · {style.on(d['cas_check'])}", callback_data="udef_tgl:cas"),
        ],
        [InlineKeyboardButton(style.btn(f"Delay: {d['delay']}s (tap to cycle)"), callback_data="udef_delay")],
        [InlineKeyboardButton("📤 Apply to All My Channels", callback_data="udef_apply_confirm")],
        [InlineKeyboardButton(style.btn("Main Menu"), callback_data="main")],
    ])


@Client.on_message(filters.command(["defaults", "mysettings", "defaultsettings"]) & filters.private)
async def cmd_defaults(client: Client, msg: Message):
    uid = msg.from_user.id
    d = await get_user_defaults(uid)
    await msg.reply_text(_render_text(d), reply_markup=_markup(d))


@Client.on_callback_query(filters.regex(r"^udef_tgl:(aa|cap|pfp|cas)$"))
async def cb_udef_toggle(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    key = q.matches[0].group(1)
    field = {"aa": "auto_approve", "cap": "captcha", "pfp": "require_pfp", "cas": "cas_check"}[key]
    d = await get_user_defaults(uid)
    d[field] = not d[field]
    await set_user_defaults(uid, d)
    await ui.edit(q.message, _render_text(d), reply_markup=_markup(d))
    await q.answer()


@Client.on_callback_query(filters.regex("^udef_delay$"))
async def cb_udef_delay(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    d = await get_user_defaults(uid)
    steps = [0, 5, 15, 30, 60, 300]
    try:
        idx = steps.index(d["delay"])
    except ValueError:
        idx = 0
    d["delay"] = steps[(idx + 1) % len(steps)]
    await set_user_defaults(uid, d)
    await ui.edit(q.message, _render_text(d), reply_markup=_markup(d))
    await q.answer(f"Delay set to {d['delay']}s")


@Client.on_callback_query(filters.regex("^udef_apply_confirm$"))
async def cb_udef_apply_confirm(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    is_admin_user = config.is_admin(uid)
    chats = await db.all_chats(owner_id=None if is_admin_user else uid)
    if not chats:
        await q.answer("You don't manage any channels yet.", show_alert=True)
        return

    await ui.edit(
        q.message,
        f"{style.h('Confirm Overwrite')}\n\n"
        f"This will overwrite auto-approve, captcha, delay, avatar filter, and "
        f"CAS filter on <b>{len(chats)}</b> channel(s) you manage — welcome "
        f"messages and images are left untouched.\n\nProceed?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Apply to All", callback_data="udef_apply_go"),
                InlineKeyboardButton("❌ Cancel", callback_data="udef_apply_cancel"),
            ]
        ]),
    )
    await q.answer()


@Client.on_callback_query(filters.regex("^udef_apply_cancel$"))
async def cb_udef_apply_cancel(client: Client, q: CallbackQuery):
    d = await get_user_defaults(q.from_user.id)
    await ui.edit(q.message, _render_text(d), reply_markup=_markup(d))
    await q.answer("Cancelled.")


@Client.on_callback_query(filters.regex("^udef_apply_go$"))
async def cb_udef_apply_go(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    is_admin_user = config.is_admin(uid)
    d = await get_user_defaults(uid)
    chats = await db.all_chats(owner_id=None if is_admin_user else uid)

    applied = 0
    for c in chats:
        chat_id = c["chat_id"]
        try:
            await db.update_chat_key(chat_id, "auto_approve", d["auto_approve"])
            await db.update_chat_key(chat_id, "captcha", d["captcha"])
            await db.update_chat_key(chat_id, "delay", d["delay"])
            filters_d = c.get("filters", {})
            filters_d["require_pfp"] = d["require_pfp"]
            filters_d["cas_check"] = d["cas_check"]
            await db.update_chat_key(chat_id, "filters", filters_d)
            invalidate_chat(chat_id)
            applied += 1
        except Exception as e:
            LOGGER.warning(f"Failed applying defaults to chat {chat_id} for user {uid}: {e}")

    await q.answer(f"Applied to {applied} channel(s)!", show_alert=True)
    await ui.edit(
        q.message,
        f"{style.h('Defaults Applied')}\n\n✅ Overwrote settings on <b>{applied}</b> of <b>{len(chats)}</b> channel(s).",
        reply_markup=_markup(d),
    )