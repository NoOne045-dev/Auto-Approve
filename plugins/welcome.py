"""
plugins/welcome.py — Interactive welcome message, media, and button builder.
"""

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message
from database import db
from helpers import kb, fmt, style, ui

# Temporary user conversation state: { user_id: {"action": str, "chat_id": int} }
_user_states: dict = {}


@Client.on_callback_query(filters.regex(r"^welcome:(-?\d+)$"))
async def cb_welcome_menu(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    cfg = await db.get_chat(chat_id)
    if not cfg:
        await q.answer("Chat not found!", show_alert=True)
        return

    wcfg = cfg.get("welcome", {})
    title = cfg.get("title", str(chat_id))
    DEFAULT_WELCOME = "<b>Welcome {mention}</b>\n\nYou have been approved to join <b>{chat_title}</b>."
    cur_text = wcfg.get("text", DEFAULT_WELCOME)

    text = (
        f"{style.h('Welcome Message Editor')} — <b>{fmt.escape(title)}</b>\n\n"
        f"{style.l('Current Template')}\n"
        f"<code>{fmt.escape(cur_text)}</code>\n\n"
        f"{style.l('Dynamic Variables')}\n"
        f"• <code>{{mention}}</code> — Clickable user mention\n"
        f"• <code>{{first_name}}</code>, <code>{{last_name}}</code>, <code>{{full_name}}</code>\n"
        f"• <code>{{username}}</code>, <code>{{user_id}}</code>\n"
        f"• <code>{{chat_title}}</code>, <code>{{chat_id}}</code>\n"
        f"• <code>{{date}}</code>, <code>{{time}}</code>, <code>{{invite_link}}</code>\n\n"
        f"<i>Select an option below.</i>"
    )
    await ui.edit(q.message, text, reply_markup=kb.welcome_editor(chat_id, wcfg))
    await q.answer()


@Client.on_callback_query(filters.regex(r"^toggle:wel:(-?\d+)$"))
async def cb_toggle_wel(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    cfg = await db.get_chat(chat_id)
    if cfg:
        wcfg = cfg.setdefault("welcome", {})
        new_val = not wcfg.get("enabled", True)
        wcfg["enabled"] = new_val
        await db.update_chat_key(chat_id, "welcome", wcfg)
        status = "ENABLED" if new_val else "DISABLED"
        await q.answer(f"Welcome Message: {status}")
        await q.message.edit_reply_markup(reply_markup=kb.welcome_editor(chat_id, wcfg))


@Client.on_callback_query(filters.regex(r"^toggle:wel_pm:(-?\d+)$"))
async def cb_toggle_wel_pm(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    cfg = await db.get_chat(chat_id)
    if cfg:
        wcfg = cfg.setdefault("welcome", {})
        new_val = not wcfg.get("send_pm", True)
        wcfg["send_pm"] = new_val
        await db.update_chat_key(chat_id, "welcome", wcfg)
        target = "Direct Message (PM)" if new_val else "In Channel/Group"
        await q.answer(f"Target set to: {target}")
        await q.message.edit_reply_markup(reply_markup=kb.welcome_editor(chat_id, wcfg))


@Client.on_callback_query(filters.regex(r"^set_wel_text:(-?\d+)$"))
async def cb_set_wel_text(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    _user_states[q.from_user.id] = {"action": "text", "chat_id": chat_id}

    await ui.edit(
        q.message,
        f"{style.h('Send your new welcome message text')}\n\n"
        "You can use HTML tags (<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;a&gt;</code>) and variables:\n"
        "<code>{mention}</code>, <code>{first_name}</code>, <code>{chat_title}</code>, <code>{date}</code>\n\n"
        "To attach buttons at the bottom, format them as:\n"
        "<code>[Button Text | https://example.com]</code>",
        reply_markup=kb.cancel(f"welcome:{chat_id}"),
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^set_wel_media:(-?\d+)$"))
async def cb_set_wel_media(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    _user_states[q.from_user.id] = {"action": "media", "chat_id": chat_id}

    await ui.edit(
        q.message,
        f"{style.h('Attach Media to Welcome Message')}\n\n"
        f"Send any {style.l('Photo, Video, GIF/Animation, or Document')} now.\n\n"
        "<i>To remove existing media, type <code>remove</code>.</i>",
        reply_markup=kb.cancel(f"welcome:{chat_id}"),
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^preview_wel:(-?\d+)$"))
async def cb_preview_wel(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    cfg = await db.get_chat(chat_id)
    if not cfg:
        await q.answer("Chat not found!", show_alert=True)
        return

    wcfg = cfg.get("welcome", {})
    raw_template = wcfg.get("text", "<b>Welcome {mention}</b> to <b>{chat_title}</b>.")
    rendered = fmt.render(
        raw_template,
        user=q.from_user,
        chat=q.message.chat,
        invite_link="https://t.me/+SampleInviteLink",
    )
    clean_text, markup = fmt.parse_buttons(rendered)

    media_id = wcfg.get("media_id")
    media_type = wcfg.get("media_type")

    await q.answer("Sending preview to your PM...")
    try:
        if media_id:
            if media_type == "photo":
                await client.send_photo(q.from_user.id, photo=media_id, caption=clean_text, reply_markup=markup)
            elif media_type == "video":
                await client.send_video(q.from_user.id, video=media_id, caption=clean_text, reply_markup=markup)
            elif media_type == "animation":
                await client.send_animation(q.from_user.id, animation=media_id, caption=clean_text, reply_markup=markup)
            else:
                await client.send_document(q.from_user.id, document=media_id, caption=clean_text, reply_markup=markup)
        else:
            await client.send_message(q.from_user.id, text=clean_text, reply_markup=markup)
    except Exception as e:
        await q.message.reply_text(f"{style.h('Preview error')} <code>{e}</code>")


# ─── Input Listener for Setting Text / Media ────────────────────────────────
@Client.on_message(
    filters.private
    & ~filters.command(["start", "help", "admin", "settings", "ping", "stats", "broadcast", "channels"])
)
async def welcome_input_handler(client: Client, msg: Message):
    uid = msg.from_user.id
    state = _user_states.get(uid)
    if not state:
        return

    action = state["action"]
    chat_id = state["chat_id"]
    cfg = await db.get_chat(chat_id) or {"chat_id": chat_id}
    wcfg = cfg.setdefault("welcome", {})

    if action == "text":
        new_text = msg.text or msg.caption or ""
        wcfg["text"] = new_text
        await db.update_chat_key(chat_id, "welcome", wcfg)
        del _user_states[uid]
        await msg.reply_text(f"{style.h('Welcome message text updated')}", reply_markup=kb.welcome_editor(chat_id, wcfg))

    elif action == "media":
        if msg.text and msg.text.strip().lower() == "remove":
            wcfg["media_id"] = None
            wcfg["media_type"] = None
            await db.update_chat_key(chat_id, "welcome", wcfg)
            del _user_states[uid]
            await msg.reply_text(f"{style.h('Media removed from welcome message')}", reply_markup=kb.welcome_editor(chat_id, wcfg))
            return

        media_id = None
        media_type = None
        if msg.photo:
            media_id = msg.photo.file_id
            media_type = "photo"
        elif msg.video:
            media_id = msg.video.file_id
            media_type = "video"
        elif msg.animation:
            media_id = msg.animation.file_id
            media_type = "animation"
        elif msg.document:
            media_id = msg.document.file_id
            media_type = "document"

        if media_id:
            wcfg["media_id"] = media_id
            wcfg["media_type"] = media_type
            if msg.caption:
                wcfg["text"] = msg.caption
            await db.update_chat_key(chat_id, "welcome", wcfg)
            del _user_states[uid]
            await msg.reply_text(f"{style.h('Media successfully attached')}", reply_markup=kb.welcome_editor(chat_id, wcfg))
        else:
            await msg.reply_text(f"{style.h('Send a valid Photo, Video, GIF, or Document')} (or send <code>remove</code>).")
