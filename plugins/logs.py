"""
plugins/logs.py — /logs: bot self-diagnostics from this running instance's
log file. Admin/owner only. Two modes: recent lines inline (fast, fits
Telegram's 4096-char message limit), or the full file as a .txt download.

NOTE: the log file lives on local disk, so on platforms with an ephemeral
filesystem (e.g. Render without a persistent disk) it resets on every
restart/redeploy — "entire log" means entire log of the CURRENT instance.
"""

import html
import os
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
import config
from helpers import style, ui

RECENT_LINES = 150
MAX_TEXT_CHARS = 3500  # margin under Telegram's 4096 cap for the <pre> block + header


def _tail_lines(path: str, n: int):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()[-n:]


def _render_recent_text() -> str:
    lines = _tail_lines(config.LOG_FILE_PATH, RECENT_LINES)
    if not lines:
        return (
            f"{style.h('Bot Logs')}\n\n"
            f"No log file yet at <code>{html.escape(config.LOG_FILE_PATH)}</code> "
            f"(or it's empty)."
        )

    body = "".join(lines)
    if len(body) > MAX_TEXT_CHARS:
        body = body[-MAX_TEXT_CHARS:]
        nl = body.find("\n")
        if nl != -1:
            body = body[nl + 1:]
        note = f"<i>Showing the tail of the last {len(lines)} lines (trimmed to fit Telegram's message limit — use the .txt button for everything).</i>\n\n"
    else:
        note = f"<i>Showing the last {len(lines)} log lines.</i>\n\n"

    return f"{style.h('Bot Logs — Recent')}\n\n{note}<pre>{html.escape(body)}</pre>"


def _logs_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(style.btn(f"Recent ({RECENT_LINES} lines)"), callback_data="logs_recent"),
            InlineKeyboardButton(style.btn("Full Log (.txt)"), callback_data="logs_file"),
        ],
        [InlineKeyboardButton(style.btn("Main Menu"), callback_data="main")],
    ])


@Client.on_message(filters.command("logs") & filters.private)
async def cmd_logs(client: Client, msg: Message):
    if not config.is_admin(msg.from_user.id):
        await msg.reply_text("⛔ Admins/owner only.")
        return
    await msg.reply_text(
        f"{style.h('Bot Diagnostics')}\n\nChoose how you'd like to view this instance's logs:",
        reply_markup=_logs_markup(),
    )


@Client.on_callback_query(filters.regex("^logs_recent$"))
async def cb_logs_recent(client: Client, q: CallbackQuery):
    if not config.is_admin(q.from_user.id):
        await q.answer("⛔ Admins/owner only.", show_alert=True)
        return
    await ui.edit(q.message, _render_recent_text(), reply_markup=_logs_markup())
    await q.answer()


@Client.on_callback_query(filters.regex("^logs_file$"))
async def cb_logs_file(client: Client, q: CallbackQuery):
    if not config.is_admin(q.from_user.id):
        await q.answer("⛔ Admins/owner only.", show_alert=True)
        return
    path = config.LOG_FILE_PATH
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        await q.answer("No log file yet on this instance.", show_alert=True)
        return
    await q.answer("Uploading full log...")
    try:
        await client.send_document(
            q.from_user.id,
            document=path,
            caption=f"{style.h('Full Log')} — this running instance only (resets on restart/redeploy).",
        )
    except Exception as e:
        await q.message.reply_text(f"{style.h('Failed to send log file')} <code>{e}</code>")