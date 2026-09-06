"""
core/permissions.py — Access control, role management, and admin validation.

Enforces master owner privileges, super admin rights, and channel administrator authorization.
"""

from typing import Optional
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
import config
from database import db


def is_owner(user_id: int) -> bool:
    """Return True if the user is the master bot owner."""
    return config.is_owner(user_id)


def is_admin(user_id: int) -> bool:
    """Return True if the user is the bot owner or a configured super admin."""
    return config.is_admin(user_id)


async def can_manage_chat(user_id: int, chat_id: int, client: Optional[Client] = None) -> bool:
    """
    Unified permission check for channel management, bulk approvals, and scheduling.
    Validates against:
    1. Global bot ownership / super admin status in config.py (config.OWNER_ID, config.ADMINS)
    2. Registered owner or admins in MongoDB 'chats' collection (owner_id or admins list)
    3. Live Telegram chat administrator status via MTProto API (if client provided)
    """
    if is_admin(user_id):
        return True

    # Check database chat record
    cfg = await db.get_chat(chat_id)
    if cfg:
        if cfg.get("owner_id") == user_id:
            return True
        if user_id in cfg.get("admins", []):
            return True

    # Live check via Telegram MTProto API
    if client:
        try:
            member = await client.get_chat_member(chat_id, user_id)
            if member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
                # Update DB record with confirmed admin status for future checks
                if cfg:
                    admins = set(cfg.get("admins", []))
                    if user_id not in admins:
                        admins.add(user_id)
                        await db.update_chat_key(chat_id, "admins", list(admins))
                else:
                    await db.update_chat_key(chat_id, "admins", [user_id])
                return True
        except Exception:
            pass

    return False
