"""
plugins/pending_requests.py — Clears the existing backlog of pending join
requests for a chat.

`handle_chat_join_request` in join_request.py only reacts to NEW
ChatJoinRequest updates that arrive while the bot is connected. It cannot
see requests that were made before the bot started polling for that chat
(bot was offline, just added, restarted, etc.) — those sit in Telegram's
"Requests to join" queue until someone (a human admin, or this command)
processes them.

Usage:
    Inside the target group/channel:  /approve_pending
    From a DM with the bot:           /approve_pending <chat_id>

The DM form is useful for channels/groups where you don't want to post a
bot command in the channel feed itself, or where the bot only has a
private/discussion-less channel to manage.
"""

import asyncio

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, ChatAdminRequired, PeerIdInvalid

import config
from config import LOGGER
from database import db
from helpers import check_spam
from plugins.join_request import approval_queue, _default_chat_config


@Client.on_message(filters.command("approve_pending") & (filters.group | filters.channel | filters.private))
async def approve_pending_requests(client: Client, message):
    if message.from_user is None:
        # Posted "as the channel" (anonymous admin) — Telegram gives us no
        # user id to check against ADMINS/OWNER_ID in this case.
        await message.reply_text(
            "⛔ Can't verify who sent this — please send /approve_pending as "
            "yourself (not anonymously as the channel/group) so I can check "
            "your admin status."
        )
        return

    user_id = message.from_user.id
    if not config.is_admin(user_id):
        await message.reply_text("⛔ Only bot admins can run this.")
        return

    if message.chat.type.name.lower() == "private":
        # DM usage — the target chat must be given explicitly, since
        # message.chat here is just the DM with the bot, not the chat
        # you actually want to process.
        args = message.command[1:]
        if not args or not args[0].lstrip("-").isdigit():
            await message.reply_text(
                "Usage in DM: `/approve_pending <chat_id>`\n"
                "(Run it with no arguments directly inside the group/channel instead.)"
            )
            return

        chat_id = int(args[0])
        try:
            target_chat = await client.get_chat(chat_id)
        except PeerIdInvalid:
            await message.reply_text(
                "⛔ I don't know that chat. The bot must already be a member/admin "
                "of it — try sending a message in that chat once first, or double-"
                "check the chat id."
            )
            return
        except Exception as e:
            await message.reply_text(f"❌ Couldn't resolve chat `{chat_id}`: {e}")
            return
    else:
        chat_id = message.chat.id
        target_chat = message.chat

    cfg = await db.get_chat(chat_id)
    if not cfg:
        cfg = _default_chat_config(target_chat)
        await db.set_chat(chat_id, cfg)

    status = await message.reply_text("🔎 Fetching pending join requests…")

    queued = 0
    declined = 0

    try:
        async for req in client.get_chat_join_requests(chat_id=chat_id):
            user = req.from_user
            invite_link = req.invite_link.invite_link if req.invite_link else None

            # Reuse the same spam filter as live requests, unless the chat
            # has auto-approve fully disabled (then skip everything).
            if not cfg.get("auto_approve", True):
                continue

            passed, reason = await check_spam(user, cfg.get("filters", {}), client)
            if not passed:
                try:
                    await client.decline_chat_join_request(chat_id=chat_id, user_id=user.id)
                    await db.bump_stat(chat_id, approved=False)
                    declined += 1
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except Exception as e:
                    LOGGER.debug(f"Decline error for {user.id}: {e}")
                continue

            # NOTE: backlog requests are enqueued without the CAPTCHA gate —
            # there's no live callback to wait on for a request that already
            # happened. Remove this behavior (route through captcha instead)
            # if you'd rather backlog users solve a CAPTCHA before approval.
            await approval_queue.put((chat_id, user.id, user, target_chat, 0, invite_link))
            queued += 1

    except ChatAdminRequired:
        await status.edit_text(
            "⛔ I need the **Add Users / Invite via Link** admin permission "
            "in this chat to read and approve pending requests."
        )
        return
    except FloodWait as fw:
        await asyncio.sleep(fw.value + 1)
    except Exception as e:
        LOGGER.error(f"Failed to fetch pending requests for {chat_id}: {e}")
        await status.edit_text(f"❌ Error fetching pending requests: {e}")
        return

    await status.edit_text(
        f"✅ Queued **{queued}** pending request(s) for approval.\n"
        f"🚫 Declined **{declined}** (failed anti-spam checks)."
    )