"""
plugins/chat_added.py — Detects when the BOT ITSELF is added to, promoted in,
or removed from a channel/group.

Previously a chat only appeared in /admin, /managechnls, or /channels after
its FIRST join request came in (handle_chat_join_request() in join_request.py
was the only place that ever called _default_chat_config()/db.set_chat()).
Until then there was no way to tell whether the bot had actually been added
successfully or to open its settings. This registers the chat the moment the
bot joins, and DMs whoever added it a confirmation (plus a permission warning
if "Invite Users via Link" wasn't granted).
"""

from pyrogram import Client, ContinuePropagation
from pyrogram.types import ChatMemberUpdated
from pyrogram.enums import ChatMemberStatus
from config import LOGGER
from database import db
from core.cache import invalidate_chat
from plugins.join_request import _default_chat_config


@Client.on_chat_member_updated()
async def on_bot_membership_changed(client: Client, event: ChatMemberUpdated):
    new_member = event.new_chat_member
    old_member = event.old_chat_member
    user = (new_member.user if new_member else None) or (old_member.user if old_member else None)

    # Only care about the BOT's own membership — leave.py already handles
    # regular members leaving/getting kicked, so hand everything else on.
    if not user or not user.is_self:
        raise ContinuePropagation

    chat = event.chat
    new_status = new_member.status if new_member else None
    added_by = getattr(event, "from_user", None)

    joined = new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)
    left = new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED) or new_status is None

    if joined:
        cfg = await db.get_chat(chat.id)
        first_time = cfg is None
        if first_time:
            cfg = _default_chat_config(chat)
            if added_by:
                cfg["owner_id"] = added_by.id
            await db.set_chat(chat.id, cfg)
            LOGGER.info(f"Bot added to chat {chat.id} ({chat.title}) — registered immediately.")
        elif cfg.get("title") != chat.title:
            await db.update_chat_key(chat.id, "title", chat.title)

        invalidate_chat(chat.id)

        if first_time and new_status == ChatMemberStatus.ADMINISTRATOR and added_by:
            can_invite = bool(getattr(new_member.privileges, "can_invite_users", False)) if new_member.privileges else False
            warn = (
                ""
                if can_invite
                else "\n\n⚠️ I don't have <b>Invite Users via Link</b> permission yet — "
                     "grant it or I can't approve join requests here."
            )
            try:
                await client.send_message(
                    added_by.id,
                    f"✅ <b>Added to {chat.title}</b>\n\n"
                    f"This chat is now set up — it shows up right away in /admin and "
                    f"/managechnls, no need to wait for a join request.{warn}",
                )
            except Exception:
                pass  # added_by hasn't started the bot in PM — nothing we can do

    elif left:
        LOGGER.info(f"Bot removed from chat {chat.id} ({getattr(chat, 'title', chat.id)}).")
        invalidate_chat(chat.id)

    # Always let leave.py (and anything else) still see this same update.
    raise ContinuePropagation