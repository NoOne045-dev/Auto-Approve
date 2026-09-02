"""
plugins/broadcast.py — High-speed asynchronous broadcast suite.
"""

import asyncio
import time
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
import config
from database import db
from helpers import limiter, style

_broadcast_running = False


@Client.on_message(filters.command("broadcast") & filters.private)
async def cmd_broadcast(client: Client, msg: Message):
    if not config.is_admin(msg.from_user.id):
        return

    if not msg.reply_to_message:
        await msg.reply_text(
            f"{style.h('Broadcast Suite')}\n\n"
            "Reply to any message with:\n"
            f"• <code>/broadcast</code> — Copy message with buttons\n"
            f"• <code>/broadcast -f</code> — Forward original message"
        )
        return

    total = await db.users_count()
    is_fwd = len(msg.command) > 1 and "-f" in msg.command[1:]

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(style.btn("Start Broadcast"), callback_data=f"bcast_go:{msg.reply_to_message.id}:{1 if is_fwd else 0}"),
            InlineKeyboardButton(style.btn("Cancel"), callback_data="bcast_cancel"),
        ]
    ])

    await msg.reply_text(
        f"{style.h('Confirm Broadcast')}\n\n"
        f"{style.kv('Target Audience', f'{total:,} users')}\n"
        f"{style.kv('Mode', 'Forward Mode' if is_fwd else 'Copy Mode')}\n\n"
        f"<i>Start broadcasting?</i>",
        reply_markup=markup,
    )


@Client.on_callback_query(filters.regex(r"^bcast_go:(\d+):(\d+)$"))
async def cb_broadcast_start(client: Client, q: CallbackQuery):
    global _broadcast_running
    if not config.is_admin(q.from_user.id):
        return

    if _broadcast_running:
        await q.answer("Another broadcast is currently running.", show_alert=True)
        return

    target_id = int(q.matches[0].group(1))
    is_fwd = q.matches[0].group(2) == "1"
    _broadcast_running = True

    user_ids = await db.all_user_ids()
    total = len(user_ids)

    if not total:
        await q.message.edit_text(f"{style.h('No active users')} found in the database.")
        _broadcast_running = False
        return

    status_msg = await q.message.edit_text(f"{style.h('Broadcast Starting')}\nTarget: {total:,} users")

    success = 0
    blocked = 0
    deleted = 0
    failed = 0
    start_time = time.time()

    for idx, uid in enumerate(user_ids, 1):
        if not _broadcast_running:
            break

        await limiter.acquire()
        try:
            if is_fwd:
                await client.forward_messages(chat_id=uid, from_chat_id=q.message.chat.id, message_ids=target_id)
            else:
                await client.copy_message(chat_id=uid, from_chat_id=q.message.chat.id, message_id=target_id)
            success += 1
        except FloodWait as fw:
            limiter.flood_wait(fw.value)
            await asyncio.sleep(fw.value + 1)
        except UserIsBlocked:
            blocked += 1
            await db.set_blocked(uid, True)
        except InputUserDeactivated:
            deleted += 1
            await db.set_blocked(uid, True)
        except Exception:
            failed += 1

        if idx % 20 == 0 or idx == total:
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            eta = int((total - idx) / rate) if rate > 0 else 0
            pct = int((idx / total) * 100)
            filled = int(pct / 10)
            bar = "█" * filled + "░" * (10 - filled)

            try:
                await status_msg.edit_text(
                    f"{style.h('Broadcast in Progress')}\n"
                    f"[{bar}] <b>{pct}%</b>\n\n"
                    f"{style.kv('Total', f'{total:,}')}\n"
                    f"{style.kv('Delivered', f'{success:,}')}\n"
                    f"{style.kv('Blocked', f'{blocked:,}')}\n"
                    f"{style.kv('Deleted', f'{deleted:,}')}\n"
                    f"{style.kv('ETA', f'{eta}s')}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(style.btn("Stop Broadcast"), callback_data="bcast_cancel")]
                    ]),
                )
            except Exception:
                pass

    _broadcast_running = False
    duration = int(time.time() - start_time)
    await status_msg.edit_text(
        f"{style.h('Broadcast Completed')}\n\n"
        f"{style.kv('Duration', f'{duration}s')}\n"
        f"{style.kv('Delivered', f'{success:,}')}\n"
        f"{style.kv('Blocked', f'{blocked:,}')}\n"
        f"{style.kv('Deleted', f'{deleted:,}')}\n"
        f"{style.kv('Failed', f'{failed:,}')}"
    )


@Client.on_callback_query(filters.regex("^bcast_cancel$"))
async def cb_broadcast_cancel(client: Client, q: CallbackQuery):
    global _broadcast_running
    _broadcast_running = False
    await q.answer("Broadcast stopped!", show_alert=True)
    await q.message.edit_text(f"{style.h('Broadcast cancelled')}.")

