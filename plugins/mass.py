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
from helpers import kb, limiter

_active_mass_ops = set()


@Client.on_callback_query(filters.regex(r"^mass:(-?\d+)$"))
async def cb_mass_menu(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    cfg = await db.get_chat(chat_id)
    title = cfg.get("title", f"Chat {chat_id}") if cfg else f"Chat {chat_id}"

    text = (
        f"⚡ <b>Mass Backlog Actions — {title}</b>\n\n"
        "Perform bulk actions on all existing pending join requests:\n"
        "• <b>Approve All:</b> Approve all pending joiners with progress bar.\n"
        "• <b>Decline All:</b> Purge spam backlogs safely.\n"
        "• <b>Export CSV:</b> Download pending requests report."
    )
    await q.message.edit_text(text, reply_markup=kb.mass_actions(chat_id))
    await q.answer()


@Client.on_callback_query(filters.regex(r"^mass_approve:(-?\d+)$"))
async def cb_mass_approve(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    _active_mass_ops.add(chat_id)

    status_msg = await q.message.edit_text(
        "⏳ <b>Fetching pending join requests...</b>",
        reply_markup=kb.cancel(f"cancel_mass:{chat_id}"),
    )

    approved = 0
    processed = 0

    try:
        async for req in client.get_chat_join_requests(chat_id=chat_id):
            if chat_id not in _active_mass_ops:
                break

            user = req.from_user
            await limiter.acquire()
            try:
                await client.approve_chat_join_request(chat_id=chat_id, user_id=user.id)
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
                        f"⚡ <b>Mass Approval in Progress...</b>\n\n"
                        f"✅ Approved: <b>{approved:,}</b>\n"
                        f"⏳ Processed: <b>{processed:,}</b>",
                        reply_markup=kb.cancel(f"cancel_mass:{chat_id}"),
                    )
                except Exception:
                    pass

        _active_mass_ops.discard(chat_id)
        await status_msg.edit_text(
            f"🎉 <b>Mass Approval Complete!</b>\n\n"
            f"✅ Successfully approved <b>{approved:,}</b> pending requests."
        )
    except Exception as e:
        _active_mass_ops.discard(chat_id)
        LOGGER.error(f"Mass approve error in {chat_id}: {e}")
        await status_msg.edit_text(f"⚠️ Mass approval stopped: <code>{e}</code>")


@Client.on_callback_query(filters.regex(r"^mass_decline:(-?\d+)$"))
async def cb_mass_decline(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    _active_mass_ops.add(chat_id)

    status_msg = await q.message.edit_text(
        "⏳ <b>Declining all pending requests...</b>",
        reply_markup=kb.cancel(f"cancel_mass:{chat_id}"),
    )

    declined = 0
    try:
        async for req in client.get_chat_join_requests(chat_id=chat_id):
            if chat_id not in _active_mass_ops:
                break

            user = req.from_user
            await limiter.acquire()
            try:
                await client.decline_chat_join_request(chat_id=chat_id, user_id=user.id)
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
                        f"❌ <b>Mass Decline in Progress...</b>\n\n"
                        f"🚫 Declined: <b>{declined:,}</b> requests",
                        reply_markup=kb.cancel(f"cancel_mass:{chat_id}"),
                    )
                except Exception:
                    pass

        _active_mass_ops.discard(chat_id)
        await status_msg.edit_text(
            f"✅ <b>Mass Decline Complete!</b>\n\n"
            f"🚫 Declined <b>{declined:,}</b> pending spam requests."
        )
    except Exception as e:
        _active_mass_ops.discard(chat_id)
        await status_msg.edit_text(f"⚠️ Mass decline stopped: <code>{e}</code>")


@Client.on_callback_query(filters.regex(r"^mass_export:(-?\d+)$"))
async def cb_mass_export(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    await q.answer("Generating CSV export...", show_alert=False)

    msg = await q.message.reply_text("📥 <i>Exporting pending members list...</i>")
    csv_buffer = io.StringIO()
    csv_buffer.write("User ID,First Name,Last Name,Username,Date,Invite Link\n")

    count = 0
    try:
        async for req in client.get_chat_join_requests(chat_id=chat_id):
            user = req.from_user
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
            caption=f"📄 <b>Exported {count:,} pending requests</b> for chat <code>{chat_id}</code>.",
        )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"⚠️ Export failed: <code>{e}</code>")


@Client.on_callback_query(filters.regex(r"^cancel_mass:(-?\d+)$"))
async def cb_cancel_mass(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    _active_mass_ops.discard(chat_id)
    await q.answer("Operation cancelled!", show_alert=True)

