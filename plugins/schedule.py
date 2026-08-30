"""
plugins/schedule.py — Interactive /schedule multi-step wizard and schedule management.

Implements automated date/time approval scheduling, timezone conversion,
and job inspection with inline cancellations.
"""

import datetime
from typing import Dict, Optional
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType
import config
from config import LOGGER
from database import db
from core.permissions import can_manage_chat
from core.scheduler import (
    parse_schedule_time,
    create_schedule,
    cancel_schedule,
    list_schedules,
    get_schedule,
    UTC,
)

# User wizard state: { user_id: { "step": str, "chat_id": int, "limit": Optional[int], "time_str": str, "tz": str, ... } }
_wizard_states: Dict[int, dict] = {}


# ─── /schedule & /schedules Command ────────────────────────────────────────
@Client.on_message(filters.command(["schedule", "schedules"]))
async def cmd_schedule(client: Client, msg: Message):
    uid = msg.from_user.id
    args = msg.command[1:] if len(msg.command) > 1 else []

    # If sub-command is "list", show schedule list
    if args and args[0].lower() in ("list", "ls", "all"):
        await _show_schedule_list(client, msg, uid)
        return

    # In private chat with no args -> Start wizard Step 1: Pick Chat
    owner_filter = uid if not config.is_admin(uid) else None
    chats = await db.all_chats(owner_id=owner_filter)

    if not chats:
        await msg.reply_text(
            "📅 <b>Scheduled Approvals (/schedule)</b>\n\n"
            "⚠️ No managed channels or groups found.\n\n"
            "1. Add the bot to your channel as an Administrator.\n"
            "2. Send /admin to configure it.\n"
            "3. Once registered, you can schedule automated approval tasks here!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 View Scheduled Jobs", callback_data="sch_list:1")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
            ])
        )
        return

    # Step 1 Keyboard: Select channel
    rows = []
    for c in chats[:6]:
        title = c.get("title", f"Chat {c['chat_id']}")
        rows.append([InlineKeyboardButton(f"📢 {title}", callback_data=f"sch_chat:{c['chat_id']}")])

    rows.append([InlineKeyboardButton("📋 View All Scheduled Jobs", callback_data="sch_list:1")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")])

    await msg.reply_text(
        "📅 <b>Automated Approval Scheduler</b>\n\n"
        "Schedule auto-approvals to run automatically at a future date and time.\n\n"
        "👉 <b>Step 1/4:</b> Select the channel or group to schedule:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _show_schedule_list(client: Client, target, user_id: int, page: int = 1):
    """Render list of user's upcoming and recent scheduled jobs."""
    jobs = await list_schedules(user_id=user_id, limit=10)

    if not jobs:
        text = (
            "📋 <b>Scheduled Approval Jobs</b>\n\n"
            "ℹ️ You have no upcoming scheduled approval jobs.\n\n"
            "Use /schedule to schedule automated join request approvals."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Create Schedule", callback_data="sch_start")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
        ])
    else:
        status_icons = {
            "pending": "⏳ Pending",
            "running": "⚡ Running",
            "completed": "✅ Completed",
            "cancelled": "🛑 Cancelled",
            "failed": "❌ Failed",
        }
        lines = [f"📋 <b>Your Scheduled Jobs ({len(jobs)} Total)</b>\n"]
        buttons = []

        now_utc = datetime.datetime.now(UTC)

        for j in jobs:
            job_id = j["job_id"]
            cid = j["chat_id"]
            st = j.get("status", "pending")
            st_badge = status_icons.get(st, st.capitalize())
            run_at = j.get("run_at")
            run_at_str = run_at.strftime("%Y-%m-%d %H:%M UTC") if run_at else "N/A"
            lim_str = f"{j['limit']:,}" if j.get("limit") else "Unlimited"

            time_diff = ""
            if run_at and st == "pending":
                mins = int((run_at - now_utc).total_seconds() / 60)
                time_diff = f" (in {mins}m)" if mins > 0 else " (due now)"

            lines.append(
                f"• <b><code>{job_id}</code></b> — {st_badge}\n"
                f"  📢 Chat: <code>{cid}</code> | Limit: <b>{lim_str}</b>\n"
                f"  🕒 Fire Time: <code>{run_at_str}</code>{time_diff}\n"
            )

            if st == "pending":
                buttons.append([InlineKeyboardButton(f"❌ Cancel {job_id}", callback_data=f"sch_del:{job_id}")])

        buttons.append([
            InlineKeyboardButton("➕ New Schedule", callback_data="sch_start"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"sch_list:{page}"),
        ])
        buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main")])
        text = "\n".join(lines)
        markup = InlineKeyboardMarkup(buttons)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.reply_text(text, reply_markup=markup)


# ─── Callback Handlers for Wizard ──────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^sch_chat:(-?\d+)$"))
async def cb_sch_chat(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    has_perm = await can_manage_chat(q.from_user.id, chat_id, client)
    if not has_perm:
        await q.answer("❌ You are not an administrator of this chat.", show_alert=True)
        return

    _wizard_states[q.from_user.id] = {
        "chat_id": chat_id,
        "step": "limit",
    }

    cfg = await db.get_chat(chat_id)
    title = cfg.get("title", f"Chat {chat_id}") if cfg else f"Chat {chat_id}"

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("50", callback_data=f"sch_lim:{chat_id}:50"),
            InlineKeyboardButton("100", callback_data=f"sch_lim:{chat_id}:100"),
            InlineKeyboardButton("500", callback_data=f"sch_lim:{chat_id}:500"),
        ],
        [
            InlineKeyboardButton("♾️ Unlimited", callback_data=f"sch_lim:{chat_id}:0"),
            InlineKeyboardButton("✏️ Custom Limit", callback_data=f"sch_lim:{chat_id}:custom"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")],
    ])

    await q.message.edit_text(
        f"📅 <b>Schedule Approval — {title}</b>\n\n"
        f"👉 <b>Step 2/4:</b> Choose the approval volume limit:",
        reply_markup=markup,
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^sch_lim:(-?\d+):(\w+)$"))
async def cb_sch_lim(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    val = q.matches[0].group(2)
    uid = q.from_user.id
    state = _wizard_states.setdefault(uid, {"chat_id": chat_id})

    if val == "custom":
        state["step"] = "custom_limit"
        await q.message.edit_text(
            "✏️ <b>Enter Custom Approval Limit</b>\n\n"
            "Please send the exact number of requests you want to approve (e.g. <code>250</code>):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")]
            ]),
        )
        await q.answer()
        return

    limit = int(val) if val.isdigit() and int(val) > 0 else None
    state["limit"] = limit
    state["step"] = "time"

    await _show_time_picker(q, chat_id, limit)


async def _show_time_picker(q: CallbackQuery, chat_id: int, limit: Optional[int]):
    """Step 3: Select execution time."""
    lim_str = f"{limit:,}" if limit else "Unlimited"
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏱ In 10 Mins", callback_data=f"sch_tim:{chat_id}:+10m"),
            InlineKeyboardButton("⏱ In 30 Mins", callback_data=f"sch_tim:{chat_id}:+30m"),
        ],
        [
            InlineKeyboardButton("⏱ In 1 Hour", callback_data=f"sch_tim:{chat_id}:+1h"),
            InlineKeyboardButton("⏱ In 2 Hours", callback_data=f"sch_tim:{chat_id}:+2h"),
        ],
        [
            InlineKeyboardButton("⏱ In 1 Day", callback_data=f"sch_tim:{chat_id}:+1d"),
            InlineKeyboardButton("✏️ Custom Time", callback_data=f"sch_tim:{chat_id}:custom"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")],
    ])

    await q.message.edit_text(
        f"📅 <b>Schedule Approval</b>\n"
        f"• Limit: <b>{lim_str}</b>\n\n"
        f"👉 <b>Step 3/4:</b> When should this approval job run?",
        reply_markup=markup,
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^sch_tim:(-?\d+):(.+)$"))
async def cb_sch_tim(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    val = q.matches[0].group(2)
    uid = q.from_user.id
    state = _wizard_states.setdefault(uid, {"chat_id": chat_id})

    if val == "custom":
        state["step"] = "custom_time"
        await q.message.edit_text(
            "✏️ <b>Enter Custom Run Time</b>\n\n"
            "Send your desired execution time in one of these formats:\n"
            "• Relative: <code>+15m</code>, <code>+45m</code>, <code>+3h</code>\n"
            "• Exact Time today: <code>21:30</code> (HH:MM)\n"
            "• Full Date: <code>2026-08-30 22:00</code> (YYYY-MM-DD HH:MM)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")]
            ]),
        )
        await q.answer()
        return

    state["time_str"] = val
    state["step"] = "tz"
    await _show_timezone_picker(q, chat_id)


async def _show_timezone_picker(q: CallbackQuery, chat_id: int):
    """Step 4: Select timezone."""
    default_tz = config.SCHEDULER_TIMEZONE or "UTC"
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🌐 {default_tz} (Default)", callback_data=f"sch_tz:{chat_id}:{default_tz}"),
        ],
        [
            InlineKeyboardButton("🇮🇳 Asia/Kolkata", callback_data=f"sch_tz:{chat_id}:Asia/Kolkata"),
            InlineKeyboardButton("🇺🇸 America/New_York", callback_data=f"sch_tz:{chat_id}:America/New_York"),
        ],
        [
            InlineKeyboardButton("🇬🇧 Europe/London", callback_data=f"sch_tz:{chat_id}:Europe/London"),
            InlineKeyboardButton("🇦🇪 Asia/Dubai", callback_data=f"sch_tz:{chat_id}:Asia/Dubai"),
        ],
        [InlineKeyboardButton("✏️ Custom Timezone", callback_data=f"sch_tz:{chat_id}:custom")],
        [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")],
    ])

    await q.message.edit_text(
        "📅 <b>Select Timezone</b>\n\n"
        f"👉 <b>Step 4/4:</b> Choose the timezone for scheduled execution:\n"
        f"<i>(Default: <code>{default_tz}</code>)</i>",
        reply_markup=markup,
    )
    await q.answer()


@Client.on_callback_query(filters.regex(r"^sch_tz:(-?\d+):(.+)$"))
async def cb_sch_tz(client: Client, q: CallbackQuery):
    chat_id = int(q.matches[0].group(1))
    tz_val = q.matches[0].group(2)
    uid = q.from_user.id
    state = _wizard_states.setdefault(uid, {"chat_id": chat_id})

    if tz_val == "custom":
        state["step"] = "custom_tz"
        await q.message.edit_text(
            "✏️ <b>Enter Custom IANA Timezone</b>\n\n"
            "Send your timezone name (e.g. <code>Asia/Singapore</code>, <code>Europe/Berlin</code>, <code>UTC</code>):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")]
            ]),
        )
        await q.answer()
        return

    state["tz"] = tz_val
    state["step"] = "confirm"
    await _show_confirmation(q, state)


async def _show_confirmation(q: CallbackQuery, state: dict):
    """Step 5: Show summary card and confirmation button."""
    chat_id = state["chat_id"]
    limit = state.get("limit")
    time_str = state.get("time_str", "+10m")
    tz_name = state.get("tz", config.SCHEDULER_TIMEZONE or "UTC")

    try:
        run_at_utc = parse_schedule_time(time_str, tz_name)
    except Exception as e:
        await q.message.edit_text(
            f"⚠️ Time calculation error: <code>{e}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="sch_start")]]),
        )
        return

    state["run_at_utc"] = run_at_utc

    cfg = await db.get_chat(chat_id)
    chat_title = cfg.get("title", f"Chat {chat_id}") if cfg else f"Chat {chat_id}"
    lim_str = f"{limit:,}" if limit else "Unlimited ♾️"
    mins_from_now = max(0, int((run_at_utc - datetime.datetime.now(UTC)).total_seconds() / 60))

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Save Schedule", callback_data="sch_confirm")],
        [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")],
    ])

    await q.message.edit_text(
        f"📅 <b>Confirm Scheduled Approval</b>\n\n"
        f"• 📢 <b>Target Chat:</b> {chat_title} (<code>{chat_id}</code>)\n"
        f"• 🎯 <b>Approval Limit:</b> <b>{lim_str}</b>\n"
        f"• 🌐 <b>Timezone:</b> <code>{tz_name}</code>\n"
        f"• 🕒 <b>Execution Time:</b> <code>{run_at_utc.strftime('%Y-%m-%d %H:%M UTC')}</code>\n"
        f"• ⏳ <b>Firing In:</b> ~{mins_from_now} minutes\n\n"
        f"<i>The bot will automatically wake up and process approvals at this exact time.</i>",
        reply_markup=markup,
    )
    await q.answer()


@Client.on_callback_query(filters.regex("^sch_confirm$"))
async def cb_sch_confirm(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    state = _wizard_states.pop(uid, None)
    if not state or "run_at_utc" not in state:
        await q.answer("⚠️ Session expired. Please start over with /schedule.", show_alert=True)
        return

    chat_id = state["chat_id"]
    limit = state.get("limit")
    run_at_utc = state["run_at_utc"]
    tz_name = state.get("tz", "UTC")
    time_str = state.get("time_str", "")

    job_id = await create_schedule(
        chat_id=chat_id,
        user_id=uid,
        run_at=run_at_utc,
        limit=limit,
        timezone=tz_name,
        time_str=time_str,
    )

    await q.answer("🎉 Scheduled approval created successfully!", show_alert=True)
    await q.message.edit_text(
        f"🎉 <b>Scheduled Approval Saved!</b>\n\n"
        f"• <b>Job ID:</b> <code>{job_id}</code>\n"
        f"• <b>Chat ID:</b> <code>{chat_id}</code>\n"
        f"• <b>Run At:</b> <code>{run_at_utc.strftime('%Y-%m-%d %H:%M UTC')}</code>\n"
        f"• <b>Limit:</b> {f'{limit:,}' if limit else 'Unlimited ♾️'}\n\n"
        f"View all scheduled tasks anytime with <code>/schedule list</code>.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 View All Schedules", callback_data="sch_list:1")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
        ]),
    )


@Client.on_callback_query(filters.regex(r"^sch_del:(sch_[a-f0-9]+)$"))
async def cb_sch_del(client: Client, q: CallbackQuery):
    job_id = q.matches[0].group(1)
    cancelled = await cancel_schedule(job_id, user_id=q.from_user.id)
    if cancelled:
        await q.answer(f"Cancelled schedule {job_id}!", show_alert=True)
    else:
        await q.answer("Job not found or already completed.", show_alert=False)

    await _show_schedule_list(client, q, q.from_user.id, page=1)


@Client.on_callback_query(filters.regex(r"^sch_list:(\d+)$"))
async def cb_sch_list(client: Client, q: CallbackQuery):
    page = int(q.matches[0].group(1))
    await _show_schedule_list(client, q, q.from_user.id, page=page)
    await q.answer()


@Client.on_callback_query(filters.regex("^sch_start$"))
async def cb_sch_start(client: Client, q: CallbackQuery):
    await q.answer()
    uid = q.from_user.id
    _wizard_states.pop(uid, None)

    owner_filter = uid if not config.is_admin(uid) else None
    chats = await db.all_chats(owner_id=owner_filter)

    if not chats:
        await q.message.edit_text(
            "⚠️ No managed channels found. Please add the bot as admin first.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main")]]),
        )
        return

    rows = []
    for c in chats[:6]:
        title = c.get("title", f"Chat {c['chat_id']}")
        rows.append([InlineKeyboardButton(f"📢 {title}", callback_data=f"sch_chat:{c['chat_id']}")])

    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")])
    await q.message.edit_text("📅 <b>Step 1/4:</b> Select channel to schedule:", reply_markup=InlineKeyboardMarkup(rows))


@Client.on_callback_query(filters.regex("^sch_cancel$"))
async def cb_sch_cancel(client: Client, q: CallbackQuery):
    _wizard_states.pop(q.from_user.id, None)
    await q.answer("Schedule wizard cancelled.", show_alert=False)
    await q.message.edit_text(
        "❌ <b>Schedule wizard cancelled.</b>\n\nUse /schedule whenever you want to set up an automated approval.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main")]]),
    )


# ─── Text Input Listener for Custom Limit / Time / TZ ──────────────────────
@Client.on_message(
    filters.private
    & ~filters.command([
        "start", "help", "admin", "settings", "ping", "stats",
        "broadcast", "channels", "login", "logout", "sessions", "session",
        "approveall", "queue", "schedule", "schedules", "cancel"
    ])
)
async def schedule_text_input_handler(client: Client, msg: Message):
    uid = msg.from_user.id
    state = _wizard_states.get(uid)
    if not state:
        return

    step = state.get("step")
    raw_text = (msg.text or "").strip()

    if step == "custom_limit":
        if raw_text.isdigit() and int(raw_text) > 0:
            state["limit"] = int(raw_text)
            state["step"] = "time"
            # Send time picker prompt
            dummy_q = type("DummyQ", (), {"message": msg, "answer": lambda *a, **kw: None})()
            await msg.reply_text(f"✅ Limit set to <b>{int(raw_text):,}</b>.")
            # Move to time picker
            await msg.reply_text(
                "👉 <b>Step 3/4:</b> When should this approval job run?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("⏱ In 10 Mins", callback_data=f"sch_tim:{state['chat_id']}:+10m"),
                        InlineKeyboardButton("⏱ In 30 Mins", callback_data=f"sch_tim:{state['chat_id']}:+30m"),
                    ],
                    [
                        InlineKeyboardButton("⏱ In 1 Hour", callback_data=f"sch_tim:{state['chat_id']}:+1h"),
                        InlineKeyboardButton("⏱ In 2 Hours", callback_data=f"sch_tim:{state['chat_id']}:+2h"),
                    ],
                    [
                        InlineKeyboardButton("⏱ In 1 Day", callback_data=f"sch_tim:{state['chat_id']}:+1d"),
                        InlineKeyboardButton("✏️ Custom Time", callback_data=f"sch_tim:{state['chat_id']}:custom"),
                    ],
                    [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")],
                ]),
            )
        else:
            await msg.reply_text("⚠️ Please send a valid positive number for the limit (e.g. <code>150</code>).")

    elif step == "custom_time":
        try:
            # Test parse with default tz
            parse_schedule_time(raw_text, state.get("tz", config.SCHEDULER_TIMEZONE or "UTC"))
            state["time_str"] = raw_text
            state["step"] = "tz"
            await msg.reply_text(
                f"✅ Time recorded: <code>{raw_text}</code>.\n\n"
                f"👉 <b>Step 4/4:</b> Choose the timezone for scheduled execution:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🌐 UTC (Default)", callback_data=f"sch_tz:{state['chat_id']}:UTC"),
                        InlineKeyboardButton("🇮🇳 Asia/Kolkata", callback_data=f"sch_tz:{state['chat_id']}:Asia/Kolkata"),
                    ],
                    [
                        InlineKeyboardButton("🇺🇸 America/New_York", callback_data=f"sch_tz:{state['chat_id']}:America/New_York"),
                        InlineKeyboardButton("🇬🇧 Europe/London", callback_data=f"sch_tz:{state['chat_id']}:Europe/London"),
                    ],
                    [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")],
                ]),
            )
        except Exception as e:
            await msg.reply_text(f"⚠️ Invalid format: {e}\nTry <code>+15m</code>, <code>+1h</code>, or <code>20:30</code>.")

    elif step == "custom_tz":
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(raw_text)
            state["tz"] = raw_text
            state["step"] = "confirm"
            # Trigger confirmation summary
            run_at_utc = parse_schedule_time(state.get("time_str", "+10m"), raw_text)
            state["run_at_utc"] = run_at_utc
            mins = max(0, int((run_at_utc - datetime.datetime.now(UTC)).total_seconds() / 60))
            lim_str = f"{state.get('limit'):,}" if state.get("limit") else "Unlimited ♾️"

            await msg.reply_text(
                f"📅 <b>Confirm Scheduled Approval</b>\n\n"
                f"• 📢 <b>Chat ID:</b> <code>{state['chat_id']}</code>\n"
                f"• 🎯 <b>Approval Limit:</b> <b>{lim_str}</b>\n"
                f"• 🌐 <b>Timezone:</b> <code>{raw_text}</code>\n"
                f"• 🕒 <b>Execution Time:</b> <code>{run_at_utc.strftime('%Y-%m-%d %H:%M UTC')}</code>\n"
                f"• ⏳ <b>Firing In:</b> ~{mins} minutes",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Confirm & Save Schedule", callback_data="sch_confirm")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="sch_cancel")],
                ]),
            )
        except Exception:
            await msg.reply_text("⚠️ Invalid timezone name. Try <code>Asia/Kolkata</code>, <code>America/New_York</code>, or <code>UTC</code>.")
