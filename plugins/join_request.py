"""
plugins/join_request.py — Core event handler for incoming ChatJoinRequest updates.
Manages anti-spam heuristics, CAPTCHA verification gateway, and queued approvals.
"""

import asyncio
from pyrogram import Client
from pyrogram.types import ChatJoinRequest
from pyrogram.errors import FloodWait, UserIsBlocked, PeerIdInvalid, ChatAdminRequired
import config
from config import LOGGER
from database import db
from helpers import limiter, make_captcha, check_spam, fmt, style

# Approval Task Queue: (chat_id, user_id, user_obj, chat_obj, delay, invite_link)
approval_queue: asyncio.Queue = asyncio.Queue()
_workers_started = False


def _default_chat_config(chat) -> dict:
    return {
        "chat_id": chat.id,
        "title": chat.title,
        "username": getattr(chat, "username", None),
        "owner_id": 0,
        "auto_approve": True,
        "captcha": False,
        "captcha_kind": "button",
        "delay": 0,
        "filters": {
            "require_pfp": False,
            "require_username": False,
            "cas_check": True,
        },
        "welcome": {
            "enabled": True,
            "send_pm": True,
            "text": "<b>Welcome {mention}</b>\n\nYour join request to <b>{chat_title}</b> has been approved.",
            "media_id": None,
            "media_type": None,
        },
        "stats": {"approved": 0, "rejected": 0},
    }


async def _approval_worker(client: Client, worker_id: int):
    """
    Background worker loop processing join requests through the token bucket rate limiter.
    """
    while True:
        try:
            chat_id, user_id, user, chat, delay, invite_link = await approval_queue.get()
            
            if delay > 0:
                await asyncio.sleep(delay)

            await limiter.acquire()

            for attempt in range(3):
                try:
                    await client.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
                    LOGGER.info(f"Approved User {user_id} ({getattr(user, 'first_name', '')}) in Chat {chat_id}")
                    await db.bump_stat(chat_id, approved=True)
                    await _send_welcome_message(client, chat_id, user, chat, invite_link)
                    break
                except FloodWait as fw:
                    limiter.flood_wait(fw.value)
                    await asyncio.sleep(fw.value + 1)
                except ChatAdminRequired:
                    LOGGER.warning(f"Bot lacks admin permission in Chat {chat_id}")
                    break
                except Exception as e:
                    LOGGER.debug(f"Approval error on attempt {attempt+1}: {e}")
                    await asyncio.sleep(1)

            approval_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            LOGGER.error(f"Approval worker exception: {e}")
            await asyncio.sleep(1)


async def _send_welcome_message(client: Client, chat_id: int, user, chat, invite_link: str):
    cfg = await db.get_chat(chat_id)
    if not cfg:
        return
    wcfg = cfg.get("welcome", {})
    if not wcfg.get("enabled", True):
        return

    raw_template = wcfg.get(
        "text",
        "<b>Welcome {mention}</b>\n\nYour join request to <b>{chat_title}</b> has been approved.",
    )
    rendered = fmt.render(raw_template, user=user, chat=chat, invite_link=invite_link)
    text, markup = fmt.parse_buttons(rendered)
    
    media_id = wcfg.get("media_id")
    media_type = wcfg.get("media_type")
    target = user.id if wcfg.get("send_pm", True) else chat_id

    try:
        if media_id and media_type:
            if media_type == "photo":
                await client.send_photo(target, photo=media_id, caption=text, reply_markup=markup)
            elif media_type == "video":
                await client.send_video(target, video=media_id, caption=text, reply_markup=markup)
            elif media_type == "animation":
                await client.send_animation(target, animation=media_id, caption=text, reply_markup=markup)
            else:
                await client.send_document(target, document=media_id, caption=text, reply_markup=markup)
        else:
            await client.send_message(target, text=text, reply_markup=markup)
    except (UserIsBlocked, PeerIdInvalid):
        pass  # User has blocked bot or not started PM
    except Exception as e:
        LOGGER.debug(f"Failed to dispatch welcome message to {target}: {e}")


def start_approval_workers(client: Client, count: int = 4):
    global _workers_started
    if _workers_started:
        return
    for i in range(count):
        asyncio.create_task(_approval_worker(client, i))
    _workers_started = True
    LOGGER.info(f"Started {count} async approval queue workers.")


# ─── Telegram Event Handler ──────────────────────────────────────────────────
@Client.on_chat_join_request()
async def handle_chat_join_request(client: Client, req: ChatJoinRequest):
    chat = req.chat
    user = req.from_user
    invite_link = req.invite_link.invite_link if req.invite_link else None

    # Register user in DB
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        is_premium=getattr(user, "is_premium", False) or False,
    )

    # Get or auto-initialize chat settings
    cfg = await db.get_chat(chat.id)
    if not cfg:
        cfg = _default_chat_config(chat)
        await db.set_chat(chat.id, cfg)

    if not cfg.get("auto_approve", True):
        return  # Auto-approval disabled for this chat

    # Check Anti-Spam filters
    passed, reason = await check_spam(user, cfg.get("filters", {}), client)
    if not passed:
        LOGGER.info(f"Declined spam user {user.id} in {chat.id}: {reason}")
        try:
            await client.decline_chat_join_request(chat_id=chat.id, user_id=user.id)
            await db.bump_stat(chat.id, approved=False)
        except Exception:
            pass
        return

    # Check CAPTCHA verification gateway
    if cfg.get("captcha", False):
        kind = cfg.get("captcha_kind", "button")
        c_text, markup, answer = make_captcha(kind, chat.id)
        try:
            msg = await client.send_message(
                chat_id=user.id,
                text=(
                    f"{style.h('Hi')}, <b>{fmt.escape(user.first_name or 'there')}</b>\n"
                    f"You requested to join <b>{fmt.escape(chat.title)}</b>.\n\n{c_text}"
                ),
                reply_markup=markup,
            )
            await db.set_captcha(
                user_id=user.id,
                chat_id=chat.id,
                answer=answer,
                kind=kind,
                msg_id=msg.id,
                invite_link=invite_link,
            )
            return  # Wait for user callback
        except (UserIsBlocked, PeerIdInvalid):
            # User hasn't started bot in PM; fallback to direct approval
            pass

    # Enqueue to rate-limited worker
    await approval_queue.put((chat.id, user.id, user, chat, cfg.get("delay", 0), invite_link))
