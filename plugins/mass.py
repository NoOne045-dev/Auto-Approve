"""
plugins/mass.py — Mass backlog actions (bulk approve, bulk decline, CSV export).
"""

import asyncio
import io
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery
from pyrogram.errors import FloodWait
import config
from config import LOGGER
from database import db
from helpers import kb, limiter, style, ui, get_join_user
from core.session_manager import get_client_for_user

_active_mass_ops = set()

_NO_SESSION_TEXT = (
    "🔒 <b>User Session Required</b>\n\n"
    "Bot tokens can't list a chat's pending join requests — Telegram only "
    "allows that for a real user account, which is exactly what "
    "<code>/login</code> connects.\n\n"
    "Send /login in PM, then try this again."
)


async def _get_backlog_client(q: CallbackQuery):
    """
    Backlog listing (get_chat_join_requests) requires a real connected user
    session — a bot token alone gets rejected by Telegram for this call
    ("bot action not allowed"). Returns a started Client, or None if the
    caller hasn't connected one (and already shows them why).
    """
    user_client = await get_client_for_user(q.from_user.id)
    if not user_client:
        await ui.edit(q.message, _NO_SESSION_TEXT, reply_markup=kb.cancel("main"))
        await q.answer()
        return None
    try:
        if not user_client.is_connected:
            await user_client.start()
        return user_client
    except Exception as e:
        LOGGER.warning(f"Failed to start user session for {q.from_user.id}: {e}")
        await ui.edit(
            q.message,
            f"{_NO_SESSION_TEXT}\n\n⚠️ Your saved session failed to connect (<code>{type(e).__name__}</code>) — try /logout then /login again.",
            reply_markup=kb.cancel("main"),
        )
        await q.answer()
        return None


@Client.on_callback_query(filters.regex(r"^mass:(-?\d+)$"))
async def cb_mass_menu(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    cfg = await db.get_chat(chat_id)
    title = cfg.get("title", f"Chat {chat_id}") if cfg else f"Chat {chat_id}"

    text = (
        f"{style.h('Mass Backlog Actions')} — <b>{title}</b>\n\n"
        "Perform bulk actions on all existing pending join requests:\n"
        f"• {style.l('Approve All')} — Approve all pending joiners with a progress bar.\n"
        f"• {style.l('Decline All')} — Purge spam backlogs safely.\n"
        f"• {style.l('Export CSV')} — Download a pending requests report."
    )
    await ui.edit(q.message, text, reply_markup=kb.mass_actions(chat_id))
    await q.answer()


@Client.on_callback_query(filters.regex(r"^mass_approve:(-?\d+)$"))
async def cb_mass_approve(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    backlog_client = await _get_backlog_client(q)
    if not backlog_client:
        return
    _active_mass_ops.add(chat_id)

    status_msg = await ui.edit(
        q.message,
        f"{style.h('Fetching pending join requests')}...",
        reply_markup=kb.cancel(f"cancel_mass:{chat_id}"),
    )

    approved = 0
    processed = 0

    try:
        async for req in backlog_client.get_chat_join_requests(chat_id=chat_id):
            if chat_id not in _active_mass_ops:
                break

            user = get_join_user(req)
            if not user:
                continue
            await limiter.acquire()
            try:
                await backlog_client.approve_chat_join_request(chat_id=chat_id, user_id=user.id)
                approved += 1
                await db.bump_stat(chat_id, approved=True)
            except FloodWait as fw:
                limiter.flood_wait(fw.value)
                await asyncio.sleep(fw.value + 1)
            except Exception as e:
                LOGGER.debug(f"Mass approve item error: {e}")

            processed += 1
            if processed % 10 == 0:
                try:
                    await status_msg.edit_text(
                        f"{style.h('Mass Approval in Progress')}\n\n"
                        f"{style.kv('Approved', f'<b>{approved:,}</b>')}\n"
                        f"{style.kv('Processed', f'<b>{processed:,}</b>')}",
                        reply_markup=kb.cancel(f"cancel_mass:{chat_id}"),
                    )
                except Exception:
                    pass

        _active_mass_ops.discard(chat_id)
        await status_msg.edit_text(
            f"{style.h('Mass Approval Complete')}\n\n"
            f"Successfully approved <b>{approved:,}</b> pending requests."
        )
    except Exception as e:
        _active_mass_ops.discard(chat_id)
        LOGGER.error(f"Mass approve error in {chat_id}: {e}")
        await status_msg.edit_text(f"{style.h('Mass approval stopped')} <code>{e}</code>")


@Client.on_callback_query(filters.regex(r"^mass_decline:(-?\d+)$"))
async def cb_mass_decline(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    backlog_client = await _get_backlog_client(q)
    if not backlog_client:
        return
    _active_mass_ops.add(chat_id)

    status_msg = await ui.edit(
        q.message,
        f"{style.h('Declining all pending requests')}...",
        reply_markup=kb.cancel(f"cancel_mass:{chat_id}"),
    )

    declined = 0
    try:
        async for req in backlog_client.get_chat_join_requests(chat_id=chat_id):
            if chat_id not in _active_mass_ops:
                break

            user = get_join_user(req)
            if not user:
                continue
            await limiter.acquire()
            try:
                await backlog_client.decline_chat_join_request(chat_id=chat_id, user_id=user.id)
                declined += 1
                await db.bump_stat(chat_id, approved=False)
            except FloodWait as fw:
                limiter.flood_wait(fw.value)
                await asyncio.sleep(fw.value + 1)
            except Exception as e:
                LOGGER.debug(f"Mass decline error: {e}")

            if declined % 10 == 0:
                try:
                    await status_msg.edit_text(
                        f"{style.h('Mass Decline in Progress')}\n\n"
                        f"{style.kv('Declined', f'<b>{declined:,}</b> requests')}",
                        reply_markup=kb.cancel(f"cancel_mass:{chat_id}"),
                    )
                except Exception:
                    pass

        _active_mass_ops.discard(chat_id)
        await status_msg.edit_text(
            f"{style.h('Mass Decline Complete')}\n\n"
            f"Declined <b>{declined:,}</b> pending requests."
        )
    except Exception as e:
        _active_mass_ops.discard(chat_id)
        await status_msg.edit_text(f"{style.h('Mass decline stopped')} <code>{e}</code>")


@Client.on_callback_query(filters.regex(r"^mass_export:(-?\d+)$"))
async def cb_mass_export(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    backlog_client = await _get_backlog_client(q)
    if not backlog_client:
        return
    await q.answer("Generating CSV export...", show_alert=False)

    msg = await q.message.reply_text(f"{style.h('Exporting pending members list')}...")
    csv_buffer = io.StringIO()
    csv_buffer.write("User ID,First Name,Last Name,Username,Date,Invite Link\n")

    count = 0
    try:
        async for req in backlog_client.get_chat_join_requests(chat_id=chat_id):
            user = get_join_user(req)
            if not user:
                continue
            link = req.invite_link.invite_link if req.invite_link else "N/A"
            date_str = req.date.strftime("%Y-%m-%d %H:%M:%S") if req.date else "N/A"
            fn = user.first_name.replace(",", " ") if user.first_name else ""
            ln = user.last_name.replace(",", " ") if user.last_name else ""
            un = f"@{user.username}" if user.username else "N/A"

            csv_buffer.write(f"{user.id},{fn},{ln},{un},{date_str},{link}\n")
            count += 1

        csv_bytes = io.BytesIO(csv_buffer.getvalue().encode("utf-8"))
        csv_bytes.name = f"pending_requests_{chat_id}.csv"

        await client.send_document(
            chat_id=q.from_user.id,
            document=csv_bytes,
            caption=f"{style.h(f'Exported {count:,} pending requests')} for chat <code>{chat_id}</code>.",
        )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"{style.h('Export failed')} <code>{e}</code>")


@Client.on_callback_query(filters.regex(r"^cancel_mass:(-?\d+)$"))
async def cb_cancel_mass(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    _active_mass_ops.discard(chat_id)
    await q.answer("Operation cancelled!", show_alert=True)