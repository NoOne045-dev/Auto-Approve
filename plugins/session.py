"""
plugins/session.py — Secure user session management (/login, /logout, /sessions).

Implements interactive Telegram account login flow with instant message deletion
and zero plaintext leakage guarantees.
"""

import re
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (
    FloodWait,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
)
import config
from config import LOGGER
from core.session_manager import save_session, revoke_session, get_session_info

# In-flight login state: { user_id: { "step": str, "client": Client, "phone_number": str, "phone_code_hash": str } }
_login_states: dict = {}


async def _cleanup_login_state(user_id: int):
    """Disconnect and clean up any in-flight temporary login client."""
    state = _login_states.pop(user_id, None)
    if state and "client" in state:
        try:
            temp_client: Client = state["client"]
            if temp_client.is_connected:
                await temp_client.disconnect()
        except Exception:
            pass


# ─── /login Command ─────────────────────────────────────────────────────────
@Client.on_message(filters.command("login") & filters.private)
async def cmd_login(client: Client, msg: Message):
    uid = msg.from_user.id

    # Check if session already exists
    existing = await get_session_info(uid)
    if existing and existing.get("connected"):
        await msg.reply_text(
            "⚠️ <b>Session Already Connected</b>\n\n"
            "You already have an active Telegram user session connected.\n"
            "Use /sessions to view status or /logout to disconnect first.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📱 Check Status", callback_data="session_status")],
                [InlineKeyboardButton("🚪 Logout", callback_data="session_logout")],
            ])
        )
        return

    # Clean up previous state if any
    await _cleanup_login_state(uid)

    _login_states[uid] = {"step": "phone"}

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Login", callback_data="cancel_login")]
    ])

    await msg.reply_text(
        "🔐 <b>Connect Telegram User Session</b>\n\n"
        "Connecting your account enables backlog join request processing.\n\n"
        "📱 <b>Step 1/3:</b> Send your <b>phone number</b> with country code:\n"
        "👉 Example: <code>+1234567890</code>\n\n"
        "🔒 <i>All sensitive messages (phone, OTP, password) are immediately deleted upon receipt.</i>",
        reply_markup=markup,
    )


# ─── /logout Command ────────────────────────────────────────────────────────
@Client.on_message(filters.command("logout") & filters.private)
async def cmd_logout(client: Client, msg: Message):
    uid = msg.from_user.id
    await _cleanup_login_state(uid)
    revoked = await revoke_session(uid)

    if revoked:
        await msg.reply_text(
            "🚪 <b>Logged Out Successfully</b>\n\n"
            "✅ Your stored Telegram session has been completely deleted from the database."
        )
    else:
        await msg.reply_text(
            "ℹ️ <b>No Active Session</b>\n\n"
            "You do not have any connected session. Use /login to connect one."
        )


# ─── /sessions Command ──────────────────────────────────────────────────────
@Client.on_message(filters.command(["sessions", "session"]) & filters.private)
async def cmd_sessions(client: Client, msg: Message):
    uid = msg.from_user.id
    info = await get_session_info(uid)

    if info and info.get("connected"):
        created_str = info["created_at"].strftime("%Y-%m-%d %H:%M UTC") if info.get("created_at") else "N/A"
        last_used_str = info["last_used"].strftime("%Y-%m-%d %H:%M UTC") if info.get("last_used") else "Never"
        phone_display = f"\n• <b>Account:</b> <code>{info['phone_masked']}</code>" if info.get("phone_masked") else ""

        text = (
            "📱 <b>Telegram Session Status</b>\n\n"
            f"• <b>Status:</b> Connected ✅{phone_display}\n"
            f"• <b>Connected On:</b> {created_str}\n"
            f"• <b>Last Used:</b> {last_used_str}\n\n"
            "🔒 <i>Your session is stored securely using AES-256 (Fernet) encryption. Plaintext is never stored or revealed.</i>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚪 Logout / Revoke Session", callback_data="session_logout")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
        ])
    else:
        text = (
            "📱 <b>Telegram Session Status</b>\n\n"
            "• <b>Status:</b> Not connected ❌\n\n"
            "Connect your Telegram user account to enable backlog join request approvals with /approveall."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Connect Session (/login)", callback_data="session_login")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
        ])

    await msg.reply_text(text, reply_markup=markup)


# ─── Interactive Login Input Handler ────────────────────────────────────────
@Client.on_message(
    filters.private
    & ~filters.command([
        "start", "help", "admin", "settings", "ping", "stats",
        "broadcast", "channels", "login", "logout", "sessions", "session", "cancel"
    ])
)
async def login_input_handler(client: Client, msg: Message):
    uid = msg.from_user.id
    state = _login_states.get(uid)
    if not state:
        return

    step = state.get("step")
    raw_input = (msg.text or "").strip()

    # STEP 1: Phone Number
    if step == "phone":
        # Instantly delete message containing phone number
        try:
            await msg.delete()
        except Exception:
            pass

        phone_clean = re.sub(r"[\s\-\(\)]", "", raw_input)
        if not phone_clean.startswith("+"):
            phone_clean = "+" + phone_clean

        if not re.match(r"^\+[1-9]\d{6,14}$", phone_clean):
            await msg.reply_text(
                "⚠️ <b>Invalid phone number format.</b>\n\n"
                "Please send a valid phone number with country code (e.g. <code>+1234567890</code>):",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_login")]
                ]),
            )
            return

        status_msg = await msg.reply_text("⏳ <i>Connecting to Telegram MTProto...</i>")

        temp_client = Client(
            name=f"temp_login_{uid}",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            in_memory=True,
            no_updates=True,
        )

        try:
            await temp_client.connect()
            sent_code = await temp_client.send_code(phone_clean)

            state["client"] = temp_client
            state["phone_number"] = phone_clean
            state["phone_code_hash"] = sent_code.phone_code_hash
            state["step"] = "otp"

            await status_msg.edit_text(
                f"📩 <b>Step 2/3: Enter Verification Code</b>\n\n"
                f"A verification code was sent to your Telegram app for <code>{phone_clean[:4]}•••{phone_clean[-3:]}</code>.\n\n"
                f"👉 Send the OTP code now (e.g. <code>1 2 3 4 5</code> or <code>12345</code>):\n\n"
                f"🔒 <i>(Your code message will be immediately deleted)</i>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_login")]
                ]),
            )
        except FloodWait as fw:
            await _cleanup_login_state(uid)
            await status_msg.edit_text(f"⚠️ Telegram FloodWait: Please wait {fw.value}s before trying again.")
        except PhoneNumberInvalid:
            await _cleanup_login_state(uid)
            await status_msg.edit_text("❌ Telegram rejected this phone number as invalid. Please /login again.")
        except Exception as e:
            await _cleanup_login_state(uid)
            LOGGER.error(f"Login error sending code to {uid}: {type(e).__name__}")
            await status_msg.edit_text(f"⚠️ Failed to send code: <code>{type(e).__name__}</code>. Please try /login again.")

    # STEP 2: OTP Code
    elif step == "otp":
        # Instantly delete message containing OTP
        try:
            await msg.delete()
        except Exception:
            pass

        otp_clean = re.sub(r"\D", "", raw_input)
        if not otp_clean:
            await msg.reply_text("⚠️ Please send numbers only for the verification code.")
            return

        temp_client: Client = state.get("client")
        phone_number = state.get("phone_number")
        phone_code_hash = state.get("phone_code_hash")

        if not temp_client or not phone_number or not phone_code_hash:
            await _cleanup_login_state(uid)
            await msg.reply_text("⚠️ Login session expired. Please /login again.")
            return

        status_msg = await msg.reply_text("⏳ <i>Verifying OTP code...</i>")

        try:
            await temp_client.sign_in(
                phone_number=phone_number,
                phone_code_hash=phone_code_hash,
                phone_code=otp_clean,
            )

            # Export session string and immediately pass to encrypted store
            session_str = await temp_client.export_session_string()
            await temp_client.disconnect()
            await save_session(uid, session_str, phone_number)
            _login_states.pop(uid, None)

            await status_msg.edit_text(
                "🎉 <b>Session Connected Successfully!</b>\n\n"
                "✅ Your user account has been securely encrypted and connected.\n"
                "Your account is now ready for backlog join request processing.\n\n"
                "Use /sessions to check status or /logout to disconnect anytime."
            )
        except SessionPasswordNeeded:
            state["step"] = "2fa"
            await status_msg.edit_text(
                "🔐 <b>Step 3/3: Two-Step Verification (2FA)</b>\n\n"
                "Two-step verification Cloud Password is enabled on this account.\n\n"
                "👉 Please enter your <b>2FA Password</b> now:\n\n"
                "🔒 <i>(Your password message will be immediately deleted upon receipt)</i>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_login")]
                ]),
            )
        except (PhoneCodeInvalid, PhoneCodeExpired):
            await status_msg.edit_text(
                "❌ <b>Invalid or expired code.</b>\n\n"
                "Please check the code in your Telegram app and send it again:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_login")]
                ]),
            )
        except Exception as e:
            await _cleanup_login_state(uid)
            LOGGER.error(f"Login OTP error for {uid}: {type(e).__name__}")
            await status_msg.edit_text(f"⚠️ Sign-in failed: <code>{type(e).__name__}</code>. Please try /login again.")

    # STEP 3: 2FA Cloud Password
    elif step == "2fa":
        # Instantly delete message containing 2FA password
        try:
            await msg.delete()
        except Exception:
            pass

        temp_client: Client = state.get("client")
        phone_number = state.get("phone_number")

        if not temp_client or not phone_number:
            await _cleanup_login_state(uid)
            await msg.reply_text("⚠️ Login session expired. Please /login again.")
            return

        status_msg = await msg.reply_text("⏳ <i>Verifying 2FA password...</i>")

        try:
            await temp_client.check_password(raw_input)

            # Export session string and immediately pass to encrypted store
            session_str = await temp_client.export_session_string()
            await temp_client.disconnect()
            await save_session(uid, session_str, phone_number)
            _login_states.pop(uid, None)

            await status_msg.edit_text(
                "🎉 <b>Session Connected Successfully!</b>\n\n"
                "✅ Your user account has been securely encrypted and connected.\n"
                "Your account is now ready for backlog join request processing.\n\n"
                "Use /sessions to check status or /logout to disconnect anytime."
            )
        except PasswordHashInvalid:
            await status_msg.edit_text(
                "❌ <b>Incorrect 2FA password.</b>\n\n"
                "Please enter the correct password or tap cancel:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_login")]
                ]),
            )
        except Exception as e:
            await _cleanup_login_state(uid)
            LOGGER.error(f"Login 2FA error for {uid}: {type(e).__name__}")
            await status_msg.edit_text(f"⚠️ 2FA check failed: <code>{type(e).__name__}</code>. Please try /login again.")


# ─── Callbacks ──────────────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex("^cancel_login$"))
async def cb_cancel_login(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    await _cleanup_login_state(uid)
    await q.answer("Login cancelled.", show_alert=False)
    await q.message.edit_text(
        "❌ <b>Login process cancelled.</b>\n\nSend /login whenever you want to connect your session."
    )


@Client.on_callback_query(filters.regex("^session_logout$"))
async def cb_session_logout(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    await _cleanup_login_state(uid)
    revoked = await revoke_session(uid)
    await q.answer("Logged out successfully!" if revoked else "No active session.", show_alert=True)
    await q.message.edit_text(
        "📱 <b>Telegram Session Status</b>\n\n"
        "• <b>Status:</b> Not connected ❌\n\n"
        "Your session was revoked. Send /login to connect a session.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Connect Session (/login)", callback_data="session_login")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
        ])
    )


@Client.on_callback_query(filters.regex("^session_login$"))
async def cb_session_login(client: Client, q: CallbackQuery):
    await q.answer()
    uid = q.from_user.id
    await _cleanup_login_state(uid)
    _login_states[uid] = {"step": "phone"}

    await q.message.edit_text(
        "🔐 <b>Connect Telegram User Session</b>\n\n"
        "📱 <b>Step 1/3:</b> Send your <b>phone number</b> with country code:\n"
        "👉 Example: <code>+1234567890</code>\n\n"
        "🔒 <i>All sensitive messages (phone, OTP, password) are immediately deleted upon receipt.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Login", callback_data="cancel_login")]
        ]),
    )


@Client.on_callback_query(filters.regex("^session_status$"))
async def cb_session_status(client: Client, q: CallbackQuery):
    await q.answer()
    uid = q.from_user.id
    info = await get_session_info(uid)

    if info and info.get("connected"):
        created_str = info["created_at"].strftime("%Y-%m-%d %H:%M UTC") if info.get("created_at") else "N/A"
        last_used_str = info["last_used"].strftime("%Y-%m-%d %H:%M UTC") if info.get("last_used") else "Never"
        phone_display = f"\n• <b>Account:</b> <code>{info['phone_masked']}</code>" if info.get("phone_masked") else ""

        text = (
            "📱 <b>Telegram Session Status</b>\n\n"
            f"• <b>Status:</b> Connected ✅{phone_display}\n"
            f"• <b>Connected On:</b> {created_str}\n"
            f"• <b>Last Used:</b> {last_used_str}\n\n"
            "🔒 <i>Encryption: AES-256 (Fernet). Plaintext is never stored or revealed.</i>"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚪 Logout / Revoke Session", callback_data="session_logout")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
        ])
    else:
        text = (
            "📱 <b>Telegram Session Status</b>\n\n"
            "• <b>Status:</b> Not connected ❌\n\n"
            "Connect your Telegram user account to enable backlog join request approvals with /approveall."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Connect Session (/login)", callback_data="session_login")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
        ])

    await q.message.edit_text(text, reply_markup=markup)

