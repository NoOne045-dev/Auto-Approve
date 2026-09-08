"""
plugins/leave.py — ChatMemberLeft / Kicked handler for automated Goodbye messages.

Fires customizable goodbye messages with photo/video/GIF/document media and dynamic template tags
when a member leaves or is removed from a group or channel.
"""

from pyrogram import Client
from pyrogram.types import ChatMemberUpdated
from pyrogram.enums import ChatMemberStatus
from database import db
from core.cache import get_cached_chat
from helpers import fmt, style


DEFAULT_GOODBYE_TEXT = "<b>Goodbye {first_name} 👋</b>\n\nThank you for being part of <b>{chat_title}</b>!"


@Client.on_chat_member_updated()
async def on_chat_member_left(client: Client, event: ChatMemberUpdated):
    """Detect when a user leaves or is kicked from a chat and send goodbye message if configured."""
    old_status = event.old_chat_member.status if event.old_chat_member else None
    new_status = event.new_chat_member.status if event.new_chat_member else None

    # Check if member left or was banned/kicked from member/admin status
    if old_status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return
    if new_status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED):
        return

    chat = event.chat
    user = event.old_chat_member.user if event.old_chat_member else (event.new_chat_member.user if event.new_chat_member else None)
    if not user or user.is_bot:
        return

    cfg = await get_cached_chat(chat.id, db.get_chat)
    if not cfg:
        return

    gcfg = cfg.get("goodbye", {})
    if not gcfg.get("enabled", True):
        return

    raw_template = gcfg.get("text", DEFAULT_GOODBYE_TEXT)
    rendered = fmt.render(
        raw_template,
        user=user,
        chat=chat,
    )
    clean_text, markup = fmt.parse_buttons(rendered)

    media_id = gcfg.get("media_id")
    media_type = gcfg.get("media_type")

    # Determine recipient (DM to user or post in chat)
    target_id = user.id if gcfg.get("send_pm", True) else chat.id

    try:
        if media_id:
            if media_type == "photo":
                await client.send_photo(target_id, photo=media_id, caption=clean_text, reply_markup=markup)
            elif media_type == "video":
                await client.send_video(target_id, video=media_id, caption=clean_text, reply_markup=markup)
            elif media_type == "animation":
                await client.send_animation(target_id, animation=media_id, caption=clean_text, reply_markup=markup)
            else:
                await client.send_document(target_id, document=media_id, caption=clean_text, reply_markup=markup)
        else:
            await client.send_message(target_id, text=clean_text, reply_markup=markup)
    except Exception:
        pass