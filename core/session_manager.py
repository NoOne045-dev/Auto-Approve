"""
core/session_manager.py — Cryptographic session storage and Pyrogram user client management.

Handles Fernet symmetric encryption and decryption for Telegram user session strings,
guaranteeing zero plaintext leakage across logs, database documents, and user messages.
"""

import datetime
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Union
from cryptography.fernet import Fernet
from pyrogram import Client
import config
from config import LOGGER
from database import db

UTC = datetime.timezone.utc


def encrypt(session_string: str) -> bytes:
    """
    Encrypt a plaintext Pyrogram session string using Fernet symmetric encryption.
    Keyed by config.SESSION_ENCRYPTION_KEY.
    """
    if not session_string or not isinstance(session_string, str):
        raise ValueError("Invalid session string provided for encryption.")

    if not config.SESSION_ENCRYPTION_KEY:
        raise ValueError("SESSION_ENCRYPTION_KEY is not configured.")

    key = config.SESSION_ENCRYPTION_KEY.strip().encode("utf-8")
    fernet = Fernet(key)
    return fernet.encrypt(session_string.encode("utf-8"))


def decrypt(token: Union[bytes, str]) -> str:
    """
    Decrypt a Fernet ciphertext token back into a session string.
    CALLED ONLY INTERNALLY when an MTProto client must be spawned.
    NEVER log or return this value to user-facing layers.
    """
    if not token:
        raise ValueError("No encryption token provided for decryption.")

    if not config.SESSION_ENCRYPTION_KEY:
        raise ValueError("SESSION_ENCRYPTION_KEY is not configured.")

    if isinstance(token, str):
        token = token.encode("utf-8")

    key = config.SESSION_ENCRYPTION_KEY.strip().encode("utf-8")
    fernet = Fernet(key)
    decrypted_bytes = fernet.decrypt(token)
    return decrypted_bytes.decode("utf-8")


async def save_session(
    user_id: int,
    session_string: str,
    phone_number: Optional[str] = None,
) -> bool:
    """
    Encrypt and store a user's Telegram session string in MongoDB.
    Overwrites any prior session for this user.
    """
    col = db.get_sessions_collection()
    if col is None:
        raise RuntimeError("MongoDB database is not connected.")

    encrypted_token = encrypt(session_string)
    now = datetime.datetime.now(UTC)

    # Optional phone masking for safe display in /sessions
    phone_masked = None
    if phone_number and len(phone_number) >= 6:
        phone_masked = phone_number[:3] + "•••" + phone_number[-4:]

    doc = {
        "user_id": user_id,
        "session_token": encrypted_token,
        "phone_masked": phone_masked,
        "is_active": True,
        "updated_at": now,
    }

    await col.update_one(
        {"user_id": user_id},
        {"$set": doc, "$setOnInsert": {"created_at": now, "last_used": None}},
        upsert=True,
    )
    LOGGER.info(f"Securely saved encrypted session for user ID {user_id}")
    return True


async def revoke_session(user_id: int) -> bool:
    """
    Revoke and delete a user's stored session from the database.
    """
    col = db.get_sessions_collection()
    if col is None:
        return False

    res = await col.delete_one({"user_id": user_id})
    deleted = res.deleted_count > 0
    if deleted:
        LOGGER.info(f"Revoked session document for user ID {user_id}")
    return deleted


async def get_session_info(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Retrieve sanitized session status metadata without leaking the session token.
    """
    col = db.get_sessions_collection()
    if col is None:
        return None

    doc = await col.find_one({"user_id": user_id}, {"session_token": 0})
    if not doc:
        return None

    return {
        "connected": bool(doc.get("is_active", True)),
        "phone_masked": doc.get("phone_masked"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "last_used": doc.get("last_used"),
    }


async def get_client_for_user(user_id: int) -> Optional[Client]:
    """
    Builds and returns an in-memory Pyrogram User Client for the given user.
    Decrypts the session internally without printing or logging the plaintext.
    """
    col = db.get_sessions_collection()
    if col is None:
        return None

    doc = await col.find_one({"user_id": user_id})
    if not doc or not doc.get("session_token"):
        return None

    try:
        session_str = decrypt(doc["session_token"])
        client = Client(
            name=f"user_session_{user_id}",
            session_string=session_str,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            in_memory=True,
            no_updates=True,
        )
        # Update last_used timestamp
        await col.update_one(
            {"user_id": user_id},
            {"$set": {"last_used": datetime.datetime.now(UTC)}},
        )
        return client
    except Exception as e:
        LOGGER.error(f"Failed to initialize user client for user ID {user_id}: {type(e).__name__}")
        return None


@asynccontextmanager
async def user_client_session(user_id: int):
    """
    Async context manager for safely running ephemeral user client operations.
    Handles start() and stop() lifecycle automatically.
    """
    client = await get_client_for_user(user_id)
    if not client:
        yield None
        return

    try:
        await client.start()
        yield client
    finally:
        try:
            await client.stop()
        except Exception:
            pass
