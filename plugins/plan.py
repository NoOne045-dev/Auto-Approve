"""
plugins/plan.py — User plan inspection, usage bars, feature checklist, trust score, and live queue status.

Implements /plan command with in-place message refresh.
"""

import datetime
from typing import Dict, Any
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
import config
from database import db
from core.quota import get_usage, PLANS, PLAN_FEATURES
from core.queue_manager import queue_manager
from helpers import style, ui

UTC = datetime.timezone.utc


def _make_usage_bar(current: int, total: int, length: int = 10) -> str:
    """Generate visual progress bar for quota usage."""
    if total == -1 or total <= 0:
        return "██████████ <b>(Unlimited ♾️)</b>"
    pct = min(1.0, max(0.0, current / total))
    filled = int(pct * length)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] <b>{current:,}</b> / <b>{total:,}</b> ({int(pct * 100)}%)"


async def _render_plan_text(user_id: int) -> str:
    """Construct formatted /plan summary text."""
    usage = await get_usage(user_id)
    plan_key = usage["plan"]
    plan_info = usage["plan_info"]
    plan_name = "Public Access (Free)" if config.PUBLIC_MODE else plan_info.get("name", plan_key)

    # Feature checklist
    features_included = PLAN_FEATURES.get(plan_key, set())
    checklist = []
    feature_labels = [
        ("captcha", "Human Captcha Verification"),
        ("custom_delay", "Configurable Approval Delays"),
        ("welcome", "Custom Welcome Messages"),
        ("goodbye_message", "Automated Goodbye Messages"),
        ("scheduling", "Automated Date/Time Scheduler"),
        ("broadcast", "Admin Broadcast Suite"),
        ("require_pfp", "Profile Photo Filter"),
        ("cas_check", "CAS Anti-Spam Check"),
    ]

    for key, label in feature_labels:
        icon = "✅" if (config.PUBLIC_MODE or key in features_included or config.is_admin(user_id)) else "🔒"
        checklist.append(f"• {icon} {label}")

    checklist_str = "\n".join(checklist)

    # Usage bars
    if config.PUBLIC_MODE:
        unlimited_bar = "██████████ <b>(Unlimited ♾️)</b>"
        daily_bar = weekly_bar = monthly_bar = unlimited_bar
        max_channels_str = "Unlimited"
    else:
        daily_bar = _make_usage_bar(usage["daily"], usage["daily_limit"])
        weekly_bar = _make_usage_bar(usage["weekly"], usage["weekly_limit"])
        monthly_bar = _make_usage_bar(usage["monthly"], usage["monthly_limit"])
        max_channels_str = plan_info["max_channels"] if plan_info["max_channels"] != -1 else "Unlimited"

    # Lifetime approvals
    lifetime_count = usage["lifetime"]

    # Engine Trust Score calculation
    gstats = await db.global_stats()
    approved = gstats.get("approved", 0)
    rejected = gstats.get("rejected", 0)
    total_ops = approved + rejected
    if total_ops > 0:
        trust_ratio = max(0.0, min(1.0, 1.0 - (rejected / total_ops)))
        trust_score = f"{trust_ratio * 100:.1f}% 🛡️"
    else:
        trust_score = "100.0% 🛡️"

    # Live queue status from queue_manager
    active_jobs = queue_manager.get_all_active_jobs()
    if active_jobs:
        queue_text = f"🟢 <b>Active Jobs:</b> {len(active_jobs)} | <b>Current Chat Queue:</b> #{active_jobs[0]['position']}"
    else:
        queue_text = "🟢 <b>Queue Engine Idle</b> (Ready for requests)"

    resets_in_mins = max(0, int((usage["resets_at"] - datetime.datetime.now(UTC)).total_seconds() / 60))

    return (
        f"{style.h('Your Plan & Usage Center')}\n\n"
        f"⭐ <b>Current Plan:</b> <b>{plan_name}</b> (<code>{plan_key}</code>)\n"
        f"🏆 <b>Max Channels:</b> {max_channels_str}\n\n"
        f"{style.h('Usage Metrics')}\n"
        f"• <b>Daily Quota:</b>\n  {daily_bar}\n"
        f"• <b>Weekly Quota:</b>\n  {weekly_bar}\n"
        f"• <b>Monthly Quota:</b>\n  {monthly_bar}\n\n"
        f"• {style.kv('Lifetime Approvals', f'<b>{lifetime_count:,}</b>')}\n"
        f"• {style.kv('Engine Trust Score', f'<b>{trust_score}</b>')}\n"
        f"• {style.kv('Quota Resets In', f'~{resets_in_mins} mins')}\n\n"
        f"{style.h('Live Queue Engine Status')}\n"
        f"{queue_text}\n\n"
        f"{style.h('Per-Feature Checklist')}\n"
        f"{checklist_str}\n\n"
        f"<i>Tap Refresh to update metrics in real time.</i>"
    )


# ─── /plan Command Handler ──────────────────────────────────────────────────
@Client.on_message(filters.command(["plan", "quota", "usage", "myplan"]))
async def cmd_plan(client: Client, msg: Message):
    uid = msg.from_user.id
    text = await _render_plan_text(uid)
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh Metrics", callback_data="plan_refresh"),
            InlineKeyboardButton("⭐ Upgrade Plan", callback_data="plan_upgrade"),
        ],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
    ])
    await ui.reply(msg, text, reply_markup=markup)


# ─── Refresh Callback Handler (Edits Message in Place) ──────────────────────
@Client.on_callback_query(filters.regex("^plan_refresh$"))
async def cb_plan_refresh(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    text = await _render_plan_text(uid)
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Refresh Metrics", callback_data="plan_refresh"),
            InlineKeyboardButton("⭐ Upgrade Plan", callback_data="plan_upgrade"),
        ],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="main")],
    ])

    await q.answer("Metrics refreshed!")
    try:
        await ui.edit(q.message, text, reply_markup=markup)
    except Exception:
        pass


@Client.on_callback_query(filters.regex("^plan_upgrade$"))
async def cb_plan_upgrade(client: Client, q: CallbackQuery):
    text = (
        f"{style.h('⭐ Upgrade Plan Tiers')}\n\n"
        "Choose a plan tier to upgrade your approval limits and unlock premium features:\n\n"
        "• <b>PRO Tier:</b> 5,000 approvals/day, 10 channels, goodbye messages, scheduling.\n"
        "• <b>BUSINESS Tier:</b> 50,000 approvals/day, 50 channels, broadcast suite, avatar & CAS filters.\n\n"
        "Contact the master bot administrator to upgrade your account instantly."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Contact Owner to Upgrade", url=config.OWNER_CONTACT_URL)],
        [InlineKeyboardButton("🔙 Back to Plan", callback_data="plan_refresh")],
    ])
    await ui.edit(q.message, text, reply_markup=markup)
    await q.answer()