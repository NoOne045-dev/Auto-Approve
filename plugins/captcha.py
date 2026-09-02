"""
plugins/captcha.py — Handles interactive DM captcha challenge verification callbacks.
"""

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery
from database import db
from helpers import style


@Client.on_callback_query(filters.regex(r"^captcha:(-?\d+):(button|math|emoji):(0|1)$"))
async def on_captcha_callback(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    kind = q.matches[0].group(2)
    is_correct = q.matches[0].group(3) == "1"
    user = q.from_user

    pending = await db.get_captcha(user.id, chat_id)
    if not pending:
        await q.answer("Session expired or was already verified.", show_alert=True)
        try:
            await q.message.edit_text(f"{style.h('Already verified')}")
        except Exception:
            pass
        return

    await db.del_captcha(user.id, chat_id)

    if is_correct:
        await q.answer("Verification successful. Approving your request...")
        try:
            chat = await client.get_chat(chat_id)
        except Exception:
            chat = None

        from plugins.join_request import approval_queue
        invite_link = pending.get("invite_link")
        await approval_queue.put((chat_id, user.id, user, chat, 0, invite_link))

        try:
            await q.message.edit_text(
                f"{style.h('Verification Successful')}\n\n"
                "Your join request has been verified and approved. Welcome."
            )
        except Exception:
            pass
    else:
        await q.answer("Incorrect answer. Join request declined.", show_alert=True)
        try:
            await client.decline_chat_join_request(chat_id=chat_id, user_id=user.id)
            await db.bump_stat(chat_id, approved=False)
            await q.message.edit_text(
                f"{style.h('Verification Failed')}\n\n"
                "Your join request was declined. You can send a new request if this was a mistake."
            )
        except Exception:
            pass
