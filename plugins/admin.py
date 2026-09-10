"""
plugins/admin.py — Bot-admin/owner moderation & configuration toolkit.

The old per-chat settings screen that used to live here (chat:, toggle:,
set_delay:, delay:, del_chat:) is retired — managechnls.py's Unified
Channel Control Center (mchnls_chat:, mchnls_tgl:, mchnls_delay:,
mchnls_del_confirm:) fully replaced it, and /admin duplicated /managechnls.
This file is now: /ban, /unban, /banlist, /fsub (force-subscribe gate).

Storage: two small, self-contained Motor collections ("banned_users",
"fsub_channels") using the same config.MONGO_URL / config.DATABASE_NAME the
rest of the bot connects with — kept independent of database.py's internals
(never reviewed) so this can't collide with or break anything there.
"""

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import RPCError
from motor.motor_asyncio import AsyncIOMotorClient
import config
from config import LOGGER
from helpers import style

_mongo = AsyncIOMotorClient(config.MONGO_URL)
_banned_col = _mongo[config.DATABASE_NAME]["banned_users"]
_fsub_col = _mongo[config.DATABASE_NAME]["fsub_channels"]


# ─── Shared helpers (also used by join_request.py's approval gate) ─────────
async def is_banned(user_id: int) -> bool:
    return bool(await _banned_col.find_one({"user_id": user_id}))


async def ban_user(user_id: int, banned_by: int, reason: str = ""):
    await _banned_col.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "banned_by": banned_by, "reason": reason}},
        upsert=True,
    )


async def unban_user(user_id: int) -> bool:
    res = await _banned_col.delete_one({"user_id": user_id})
    return res.deleted_count > 0


async def list_banned() -> list:
    return [d async for d in _banned_col.find({})]


async def get_fsub_channels() -> list:
    return [d async for d in _fsub_col.find({})]


async def add_fsub_channel(chat_id: int, title: str, invite_link: str = ""):
    await _fsub_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "title": title, "invite_link": invite_link}},
        upsert=True,
    )


async def remove_fsub_channel(chat_id: int) -> bool:
    res = await _fsub_col.delete_one({"chat_id": chat_id})
    return res.deleted_count > 0


async def check_fsub(client: Client, user_id: int) -> list:
    """Return the fsub channels the user is NOT a member of (empty list = passes)."""
    channels = await get_fsub_channels()
    missing = []
    for ch in channels:
        try:
            member = await client.get_chat_member(ch["chat_id"], user_id)
            if member.status in ("left", "banned", "kicked"):
                missing.append(ch)
        except RPCError:
            missing.append(ch)
        except Exception:
            pass
    return missing


# ─── /ban ────────────────────────────────────────────────────────────────────
@Client.on_message(filters.command("ban") & filters.private)
async def cmd_ban(client: Client, msg: Message):
    if not config.is_admin(msg.from_user.id):
        return
    args = msg.command[1:]
    if not args or not args[0].lstrip("-").isdigit():
        await msg.reply_text(f"{style.h('Usage')}\n<code>/ban &lt;user_id&gt; [reason]</code>")
        return
    target_id = int(args[0])
    reason = " ".join(args[1:]) if len(args) > 1 else ""
    await ban_user(target_id, msg.from_user.id, reason)
    await msg.reply_text(
        f"{style.h('User Banned')}\n\n"
        f"<code>{target_id}</code> will be auto-declined on every future join request across all channels."
        + (f"\n{style.kv('Reason', reason)}" if reason else "")
    )


@Client.on_message(filters.command("unban") & filters.private)
async def cmd_unban(client: Client, msg: Message):
    if not config.is_admin(msg.from_user.id):
        return
    args = msg.command[1:]
    if not args or not args[0].lstrip("-").isdigit():
        await msg.reply_text(f"{style.h('Usage')}\n<code>/unban &lt;user_id&gt;</code>")
        return
    target_id = int(args[0])
    removed = await unban_user(target_id)
    await msg.reply_text(
        f"{style.h('User Unbanned')} <code>{target_id}</code>" if removed
        else f"{style.h('Not Banned')} — <code>{target_id}</code> wasn't on the ban list."
    )


@Client.on_message(filters.command(["banlist", "banned"]) & filters.private)
async def cmd_banlist(client: Client, msg: Message):
    if not config.is_admin(msg.from_user.id):
        return
    banned = await list_banned()
    if not banned:
        await msg.reply_text(f"{style.h('Ban List')}\n\nNo users are currently banned.")
        return
    lines = [f"{style.h(f'Ban List ({len(banned)})')}\n"]
    for b in banned[:50]:
        reason = f" — {b['reason']}" if b.get("reason") else ""
        lines.append(f"• <code>{b['user_id']}</code>{reason}")
    if len(banned) > 50:
        lines.append(f"\n<i>...and {len(banned) - 50} more.</i>")
    await msg.reply_text("\n".join(lines))


# ─── /fsub — force-subscribe gate ───────────────────────────────────────────
@Client.on_message(filters.command("fsub") & filters.private)
async def cmd_fsub(client: Client, msg: Message):
    if not config.is_admin(msg.from_user.id):
        return
    args = msg.command[1:]

    if not args or args[0].lower() not in ("add", "remove", "del", "list"):
        await msg.reply_text(
            f"{style.h('Force-Subscribe Gate')}\n\n"
            "Users must be a member of every fsub channel below before any "
            "of their join requests get auto-approved, anywhere.\n\n"
            f"{style.l('Usage')}\n"
            "• <code>/fsub add &lt;channel_id&gt;</code>\n"
            "• <code>/fsub remove &lt;channel_id&gt;</code>\n"
            "• <code>/fsub list</code>\n\n"
            "<i>The bot must already be an admin of the fsub channel to check membership.</i>"
        )
        return

    action = args[0].lower()

    if action == "list":
        channels = await get_fsub_channels()
        if not channels:
            await msg.reply_text(f"{style.h('Force-Subscribe Channels')}\n\nNone configured.")
            return
        lines = [f"{style.h(f'Force-Subscribe Channels ({len(channels)})')}\n"]
        for ch in channels:
            lines.append(f"• {ch.get('title', 'Unknown')} — <code>{ch['chat_id']}</code>")
        await msg.reply_text("\n".join(lines))
        return

    if len(args) < 2 or not args[1].lstrip("-").isdigit():
        await msg.reply_text(f"{style.h('Usage')}\n<code>/fsub {action} &lt;channel_id&gt;</code>")
        return

    chat_id = int(args[1])

    if action == "add":
        try:
            chat = await client.get_chat(chat_id)
            title = chat.title or str(chat_id)
        except Exception as e:
            await msg.reply_text(f"{style.h('Could not resolve chat')} <code>{chat_id}</code>: {e}")
            return
        await add_fsub_channel(chat_id, title)
        await msg.reply_text(f"{style.h('Force-Subscribe Channel Added')}\n\n{style.kv('Channel', title)}\n<code>{chat_id}</code>")
    else:  # remove / del
        removed = await remove_fsub_channel(chat_id)
        await msg.reply_text(
            f"{style.h('Removed')} <code>{chat_id}</code>" if removed
            else f"{style.h('Not Found')} — <code>{chat_id}</code> wasn't in the fsub list."
        )