"""
core/quota.py — Plan tiers, usage tracking, and quota enforcement engine.

Tracks daily, weekly, monthly, and lifetime approval volumes per user/chat based on plan tier.
Manages automatic quota resets and feature gates.
"""

import datetime
from typing import Any, Dict, Optional, Tuple, Set
import config
from database import db

UTC = datetime.timezone.utc


# ─── Plan Definitions ───────────────────────────────────────────────────────
PLANS: Dict[str, Dict[str, Any]] = {
    "FREE": {
        "name": "Free Tier",
        "max_channels": 2,
        "daily_limit": 100,
        "weekly_limit": 500,
        "monthly_limit": 1500,
        "features": {"captcha", "custom_delay", "welcome", "auto_approve"},
    },
    "PRO": {
        "name": "Pro Tier",
        "max_channels": 10,
        "daily_limit": 5000,
        "weekly_limit": 25000,
        "monthly_limit": 100000,
        "features": {"captcha", "custom_delay", "goodbye_message", "goodbye", "scheduling", "schedule", "welcome", "auto_approve"},
    },
    "BUSINESS": {
        "name": "Business Tier",
        "max_channels": 50,
        "daily_limit": 50000,
        "weekly_limit": 250000,
        "monthly_limit": 1000000,
        "features": {"captcha", "custom_delay", "goodbye_message", "goodbye", "scheduling", "schedule", "broadcast", "require_pfp", "cas_check", "welcome", "auto_approve"},
    },
    "UNLIMITED": {
        "name": "Unlimited Tier",
        "max_channels": -1,
        "daily_limit": -1,
        "weekly_limit": -1,
        "monthly_limit": -1,
        "features": {"captcha", "custom_delay", "goodbye_message", "goodbye", "scheduling", "schedule", "broadcast", "require_pfp", "cas_check", "welcome", "auto_approve", "priority_queue"},
    },
}

PLAN_FEATURES: Dict[str, Set[str]] = {p: set(info["features"]) for p, info in PLANS.items()}
DAILY_QUOTA_LIMITS: Dict[str, int] = {p: info["daily_limit"] for p, info in PLANS.items()}


# Helper to get next reset datetime
def get_next_reset_time() -> datetime.datetime:
    now = datetime.datetime.now(UTC)
    reset_hour = getattr(config, "QUOTA_RESET_HOUR", 0)
    candidate = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += datetime.timedelta(days=1)
    return candidate


async def get_user_plan(user_id: int) -> str:
    """Return user's assigned plan tier (or FREE by default). Admin users get UNLIMITED."""
    if config.is_admin(user_id):
        return "UNLIMITED"
    
    col = db.get_quotas_collection()
    if col is not None:
        doc = await col.find_one({"user_id": user_id})
        if doc and "plan" in doc:
            plan = str(doc["plan"]).upper()
            if plan in PLANS:
                return plan
    return getattr(config, "DEFAULT_PLAN", "FREE").upper()


async def set_user_plan(user_id: int, plan: str) -> None:
    """Assign a plan tier to a user."""
    plan_upper = plan.upper()
    if plan_upper not in PLANS:
        raise ValueError(f"Invalid plan '{plan}'. Choose from {list(PLANS.keys())}")
    
    col = db.get_quotas_collection()
    if col is not None:
        await col.update_one(
            {"user_id": user_id},
            {"$set": {"plan": plan_upper, "updated_at": datetime.datetime.now(UTC)}},
            upsert=True,
        )


async def get_chat_plan(chat_id: int) -> str:
    """Retrieve the plan tier for a given channel."""
    cfg = await db.get_chat(chat_id)
    if cfg and "plan" in cfg:
        plan = str(cfg["plan"]).upper()
        if plan in PLANS:
            return plan
    if cfg and "owner_id" in cfg:
        return await get_user_plan(cfg["owner_id"])
    return getattr(config, "DEFAULT_PLAN", "FREE").upper()


async def set_chat_plan(chat_id: int, plan: str) -> None:
    """Set the plan tier for a channel."""
    plan_upper = plan.upper()
    if plan_upper not in PLANS:
        raise ValueError(f"Invalid plan '{plan}'. Choose from {list(PLANS.keys())}")
    await db.update_chat_key(chat_id, "plan", plan_upper)


async def is_feature_allowed(chat_id: int, feature: str, user_id: Optional[int] = None) -> bool:
    """
    Check if a feature (e.g. 'goodbye', 'scheduling') is allowed under the plan tier.
    Global bot owners/admins bypass feature gates.
    """
    if user_id and config.is_admin(user_id):
        return True

    plan = await get_chat_plan(chat_id)
    allowed_features = PLAN_FEATURES.get(plan, PLAN_FEATURES["FREE"])
    return feature.lower() in allowed_features


async def get_usage(user_id: int) -> Dict[str, Any]:
    """
    Retrieve quota usage (daily, weekly, monthly, lifetime, resets_at) for user_id,
    performing automatic period resets if needed.
    """
    col = db.get_quotas_collection()
    now = datetime.datetime.now(UTC)
    reset_hour = getattr(config, "QUOTA_RESET_HOUR", 0)

    # Calculate current day reset threshold
    today_reset = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if now < today_reset:
        today_reset -= datetime.timedelta(days=1)

    doc = await col.find_one({"user_id": user_id}) if col is not None else None
    if not doc:
        doc = {
            "user_id": user_id,
            "plan": "UNLIMITED" if config.is_admin(user_id) else getattr(config, "DEFAULT_PLAN", "FREE").upper(),
            "daily_usage": 0,
            "weekly_usage": 0,
            "monthly_usage": 0,
            "lifetime_usage": 0,
            "last_daily_reset": today_reset,
            "last_weekly_reset": today_reset,
            "last_monthly_reset": today_reset,
        }

    plan_name = doc.get("plan", "FREE").upper()
    if config.is_admin(user_id):
        plan_name = "UNLIMITED"

    plan_info = PLANS.get(plan_name, PLANS["FREE"])

    # Check daily reset
    last_daily = doc.get("last_daily_reset", today_reset)
    if isinstance(last_daily, datetime.datetime) and last_daily < today_reset:
        doc["daily_usage"] = 0
        doc["last_daily_reset"] = today_reset
        if col is not None:
            await col.update_one({"user_id": user_id}, {"$set": {"daily_usage": 0, "last_daily_reset": today_reset}})

    # Check weekly reset (7 days)
    last_weekly = doc.get("last_weekly_reset", today_reset)
    if isinstance(last_weekly, datetime.datetime) and (now - last_weekly).days >= 7:
        doc["weekly_usage"] = 0
        doc["last_weekly_reset"] = today_reset
        if col is not None:
            await col.update_one({"user_id": user_id}, {"$set": {"weekly_usage": 0, "last_weekly_reset": today_reset}})

    # Check monthly reset (30 days)
    last_monthly = doc.get("last_monthly_reset", today_reset)
    if isinstance(last_monthly, datetime.datetime) and (now - last_monthly).days >= 30:
        doc["monthly_usage"] = 0
        doc["last_monthly_reset"] = today_reset
        if col is not None:
            await col.update_one({"user_id": user_id}, {"$set": {"monthly_usage": 0, "last_monthly_reset": today_reset}})

    return {
        "user_id": user_id,
        "plan": plan_name,
        "plan_info": plan_info,
        "daily": doc.get("daily_usage", 0),
        "daily_limit": plan_info["daily_limit"],
        "weekly": doc.get("weekly_usage", 0),
        "weekly_limit": plan_info["weekly_limit"],
        "monthly": doc.get("monthly_usage", 0),
        "monthly_limit": plan_info["monthly_limit"],
        "lifetime": doc.get("lifetime_usage", 0),
        "resets_at": get_next_reset_time(),
    }


async def record_approval(user_id: int, count: int = 1) -> None:
    """Record completed join request approvals in MongoDB quotas collection."""
    col = db.get_quotas_collection()
    if col is None or user_id <= 0:
        return

    now = datetime.datetime.now(UTC)
    await col.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "daily_usage": count,
                "weekly_usage": count,
                "monthly_usage": count,
                "lifetime_usage": count,
            },
            "$set": {"updated_at": now},
            "$setOnInsert": {
                "plan": "UNLIMITED" if config.is_admin(user_id) else getattr(config, "DEFAULT_PLAN", "FREE").upper(),
                "created_at": now,
            },
        },
        upsert=True,
    )


async def is_within_quota(user_id: int, requested_count: int = 1) -> Tuple[bool, str]:
    """
    Check if approving requested_count fits within user's daily/weekly/monthly quotas.
    Returns (True, "") if allowed, or (False, error_msg) if quota exceeded.
    Global bot owners/admins bypass quota limits.
    """
    if config.is_admin(user_id):
        return True, ""

    usage = await get_usage(user_id)
    plan_name = usage["plan"]
    
    # Check daily limit
    daily_limit = usage["daily_limit"]
    if daily_limit != -1 and (usage["daily"] + requested_count) > daily_limit:
        return (
            False,
            f"❌ <b>Daily Quota Exceeded!</b>\n\n"
            f"You have used <b>{usage['daily']:,}</b> / <b>{daily_limit:,}</b> daily approvals under the <b>{plan_name}</b> plan.\n"
            f"Please upgrade to <b>PRO</b> or <b>BUSINESS</b> tier to increase your limit."
        )

    # Check weekly limit
    weekly_limit = usage["weekly_limit"]
    if weekly_limit != -1 and (usage["weekly"] + requested_count) > weekly_limit:
        return (
            False,
            f"❌ <b>Weekly Quota Exceeded!</b>\n\n"
            f"You have used <b>{usage['weekly']:,}</b> / <b>{weekly_limit:,}</b> weekly approvals under the <b>{plan_name}</b> plan.\n"
            f"Please upgrade your plan tier to continue approvals."
        )

    # Check monthly limit
    monthly_limit = usage["monthly_limit"]
    if monthly_limit != -1 and (usage["monthly"] + requested_count) > monthly_limit:
        return (
            False,
            f"❌ <b>Monthly Quota Exceeded!</b>\n\n"
            f"You have used <b>{usage['monthly']:,}</b> / <b>{monthly_limit:,}</b> monthly approvals under the <b>{plan_name}</b> plan.\n"
            f"Please upgrade your plan tier to continue approvals."
        )

    return True, ""
