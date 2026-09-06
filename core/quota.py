"""
core/quota.py — Plan tiers, feature gates, usage tracking, and quota enforcement engine.

Manages plan tiers (FREE, PRO, ENTERPRISE, UNLIMITED) and feature gates across channels.
"""

from typing import Any, Dict, Optional, Set
import config
from database import db


# ─── Plan Definitions ───────────────────────────────────────────────────────
PLAN_FEATURES: Dict[str, Set[str]] = {
    "FREE": {"auto_approve", "captcha", "welcome", "custom_delay"},
    "PRO": {"auto_approve", "captcha", "welcome", "goodbye", "custom_delay", "require_pfp", "cas_check", "scheduled_approvals"},
    "ENTERPRISE": {"auto_approve", "captcha", "welcome", "goodbye", "custom_delay", "require_pfp", "cas_check", "scheduled_approvals", "priority_queue", "unlimited_volume"},
    "UNLIMITED": {"auto_approve", "captcha", "welcome", "goodbye", "custom_delay", "require_pfp", "cas_check", "scheduled_approvals", "priority_queue", "unlimited_volume"},
}

DAILY_QUOTA_LIMITS: Dict[str, int] = {
    "FREE": 500,
    "PRO": 10000,
    "ENTERPRISE": 100000,
    "UNLIMITED": -1,  # -1 means unlimited
}


async def get_chat_plan(chat_id: int) -> str:
    """Retrieve the plan tier for a given channel (default = FREE)."""
    cfg = await db.get_chat(chat_id)
    if cfg and "plan" in cfg:
        return str(cfg["plan"]).upper()
    return getattr(config, "DEFAULT_PLAN", "FREE").upper()


async def is_feature_allowed(chat_id: int, feature: str, user_id: Optional[int] = None) -> bool:
    """
    Check if a feature (e.g. 'goodbye') is enabled under the channel's plan tier.
    Global bot owners/admins bypass feature gates.
    """
    if user_id and config.is_admin(user_id):
        return True

    plan = await get_chat_plan(chat_id)
    allowed_features = PLAN_FEATURES.get(plan, PLAN_FEATURES["FREE"])
    return feature in allowed_features


async def set_chat_plan(chat_id: int, plan: str) -> None:
    """Set the plan tier for a channel."""
    plan_upper = plan.upper()
    if plan_upper not in PLAN_FEATURES:
        raise ValueError(f"Invalid plan '{plan}'. Choose from {list(PLAN_FEATURES.keys())}")
    await db.update_chat_key(chat_id, "plan", plan_upper)
