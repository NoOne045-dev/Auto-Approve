"""
plugins/admin.py — Interactive channel settings control center.
Handles toggles, delays, filters, and channel deletion.
"""

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery
import config
from database import db
from helpers import kb, fmt


# ─── Single Chat Settings View ──────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^chat:(-?\d+)$"))
async def cb_chat(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    cfg = await db.get_chat(chat_id)
    if not cfg:
        await q.answer("Chat not found in database!", show_alert=True)
        return

    title = cfg.get("title", f"Chat {chat_id}")
    stats = cfg.get("stats", {})
    approved = stats.get("approved", 0)
    rejected = stats.get("rejected", 0)

    text = (
        f"⚙️ <b>Settings — {fmt.escape(title)}</b>\n"
        f"🆔 <code>{chat_id}</code>\n\n"
        f"📊 <b>Quick Stats:</b>\n"
        f"• ✅ Approved: <b>{approved:,}</b>\n"
        f"• 🚫 Rejected: <b>{rejected:,}</b>\n\n"
        f"<i>Toggle options below to customize behavior:</i>"
    )
    await q.message.edit_text(text, reply_markup=kb.chat_settings(chat_id, cfg))
    await q.answer()


# ─── Toggle Switches ────────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^toggle:(aa|cap|pfp|cas):(-?\d+)$"))
async def cb_toggle(client: Client, q: CallbackQuery):
    key = q.matches[0].group(1)
    chat_id = int(q.matches[0].group(2))
    cfg = await db.get_chat(chat_id)
    if not cfg:
        await q.answer("Chat not found!", show_alert=True)
        return

    labels = {
        "aa": "Auto-Approval",
        "cap": "Captcha Verification",
        "pfp": "Require Profile Photo",
        "cas": "CAS Anti-Spam Check",
    }

    if key == "aa":
        new_val = not cfg.get("auto_approve", True)
        cfg["auto_approve"] = new_val
        await db.update_chat_key(chat_id, "auto_approve", new_val)
    elif key == "cap":
        new_val = not cfg.get("captcha", False)
        cfg["captcha"] = new_val
        await db.update_chat_key(chat_id, "captcha", new_val)
    elif key in ("pfp", "cas"):
        filters_d = cfg.setdefault("filters", {})
        target_key = "require_pfp" if key == "pfp" else "cas_check"
        new_val = not filters_d.get(target_key, True if key == "cas" else False)
        filters_d[target_key] = new_val
        await db.update_chat_key(chat_id, "filters", filters_d)

    status_str = "🟢 ON" if new_val else "🔴 OFF"
    await q.answer(f"{labels[key]}: {status_str}")
    await q.message.edit_reply_markup(reply_markup=kb.chat_settings(chat_id, cfg))


# ─── Delay Picker ───────────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^set_delay:(-?\d+)$"))
async def cb_set_delay(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    await q.message.edit_text(
        "⏳ <b>Select Auto-Approval Delay</b>\n\n"
        "Configure how long the bot waits before approving a join request:\n"
        "<i>(A small delay mimics human admin behavior and bypasses bot-detection algorithms)</i>",
        reply_markup=kb.delay_picker(chat_id),
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^delay:(-?\d+):(\d+)$"))
async def cb_apply_delay(client: Client, q: CallbackQuery):
    chat_id = int(query_chat_id := q.matches[0].group(1))
    delay = int(q.matches[0].group(2))
    
    await db.update_chat_key(chat_id, "delay", delay)
    cfg = await db.get_chat(chat_id)
    
    await q.answer(f"Delay set to {delay} seconds!", show_alert=True)
    title = cfg.get("title", f"Chat {chat_id}")
    await q.message.edit_text(
        f"⚙️ <b>Settings — {fmt.escape(title)}</b>\n🆔 <code>{chat_id}</code>",
        reply_markup=kb.chat_settings(chat_id, cfg),
    )


# ─── Delete / Remove Chat ───────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^del_chat:(-?\d+)$"))
async def cb_del_chat(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    await db.delete_chat(chat_id)
    await q.answer("Chat removed from bot management.", show_alert=True)

    uid = q.from_user.id
    chats = await db.all_chats(owner_id=uid if not config.is_admin(uid) else None)
    await q.message.edit_text(
        f"📢 <b>Managed Chats ({len(chats)} Total)</b>",
        reply_markup=kb.chat_list(chats, page=1),
    )
