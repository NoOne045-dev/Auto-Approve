"""
config.py — Central bot configuration.
Reads settings from environment variables or .env file.
"""
import os
import re
import logging
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── Telegram Credentials ───────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
API_ID: int = int(os.getenv("API_ID", "0") if os.getenv("API_ID", "").strip().isdigit() else 0)
API_HASH: str = os.getenv("API_HASH", "")

# ─── Access Control ─────────────────────────────────────────────────────────
OWNER_ID: int = int(os.getenv("OWNER_ID", "0") if os.getenv("OWNER_ID", "").strip().isdigit() else 0)
_raw_admins = os.getenv("ADMINS", "")
ADMINS: List[int] = [int(x) for x in re.split(r"[\s,]+", _raw_admins.strip()) if x.isdigit()]

# ─── Database ───────────────────────────────────────────────────────────────
MONGO_URL: str = os.getenv("MONGO_URL", "")
DATABASE_NAME: str = os.getenv("DATABASE_NAME", "AutoApproveBot")

# ─── Rate Limiting & Concurrency ────────────────────────────────────────────
MAX_APPROVALS_PER_SECOND: int = int(os.getenv("MAX_APPROVALS_PER_SECOND", "25"))

# ─── Anti-Spam ──────────────────────────────────────────────────────────────
ENABLE_CAS_CHECK: bool = os.getenv("ENABLE_CAS_CHECK", "true").lower() in ("1", "true", "yes")

# ─── Web Server (required for Render "Web Service" port binding) ───────────
# Render injects PORT automatically for web services; default kept for local runs.
PORT: int = int(os.getenv("PORT", "8080") if os.getenv("PORT", "").strip().isdigit() else 8080)

# ─── Branding ────────────────────────────────────────────────────────────────
# Photo URL(s) shown on /start. Accepts one URL, or several separated by
# commas/newlines — one is picked at random each time /start runs.
import re as _re
_raw_start_pic = (os.getenv("START_PIC") or os.getenv("Start_pic") or "").strip()
START_PICS: List[str] = [p.strip() for p in _re.split(r"[,\n]+", _raw_start_pic) if p.strip()]
START_PIC: str = START_PICS[0] if START_PICS else ""  # kept for backward compatibility

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE_PATH: str = (os.getenv("LOG_FILE_PATH") or "bot.log").strip()

_log_formatter = logging.Formatter(
    fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)

from logging.handlers import RotatingFileHandler
_file_handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
_file_handler.setFormatter(_log_formatter)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    handlers=[_console_handler, _file_handler],
)
LOGGER = logging.getLogger("AutoApproveBot")

# ─── Scheduler & Quota ───────────────────────────────────────────────────────
SCHEDULER_TIMEZONE: str = os.getenv("SCHEDULER_TIMEZONE", "UTC").strip() or "UTC"
DEFAULT_PLAN: str = os.getenv("DEFAULT_PLAN", "FREE").strip() or "FREE"
QUOTA_RESET_HOUR: int = int(os.getenv("QUOTA_RESET_HOUR", "0") if os.getenv("QUOTA_RESET_HOUR", "").strip().isdigit() else 0)
CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "300") if os.getenv("CACHE_TTL_SECONDS", "").strip().isdigit() else 300)

# ─── Security & Cryptography ────────────────────────────────────────────────
SESSION_ENCRYPTION_KEY: str = os.getenv("SESSION_ENCRYPTION_KEY", "").strip()

# ─── Access Mode (Free/Public vs Premium-Gated) ─────────────────────────────
# When True (default), every plan/quota-gated feature across the bot is
# unlocked for everyone — runs fully free & public, no upgrade prompts, no
# daily/weekly/monthly quota checks. Set PUBLIC_MODE=false in .env to
# re-enable the PRO/ENTERPRISE plan gating in core/quota.py. One switch.
PUBLIC_MODE: bool = os.getenv("PUBLIC_MODE", "true").strip().lower() in ("1", "true", "yes")

# ─── Upgrade Contact Link ────────────────────────────────────────────────────
# Shown on every "Contact Owner to Upgrade" button (plan.py, managechnls.py).
# Set OWNER_CONTACT_URL in .env to your own @username before going live —
# e.g. OWNER_CONTACT_URL=https://t.me/your_username
OWNER_CONTACT_URL: str = (os.getenv("OWNER_CONTACT_URL") or "https://t.me/telegram").strip()

_PLACEHOLDER_KEYS = {
    "",
    "<output of Fernet.generate_key()>",
    "CHANGEME",
    "your_fernet_key_here",
    "replace_with_fernet_key",
    "replace_this_with_your_generated_fernet_key",
}


def validate_session_encryption_key() -> bool:
    """
    Validate that SESSION_ENCRYPTION_KEY is present and a valid 32-byte url-safe Fernet key.
    """
    if not SESSION_ENCRYPTION_KEY or SESSION_ENCRYPTION_KEY in _PLACEHOLDER_KEYS:
        return False
    try:
        from cryptography.fernet import Fernet
        Fernet(SESSION_ENCRYPTION_KEY.encode())
        return True
    except Exception:
        return False


# ─── Helper Functions ───────────────────────────────────────────────────────
def is_owner(user_id: int) -> bool:
    return OWNER_ID != 0 and user_id == OWNER_ID


def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or user_id in ADMINS


def owner_contact_url() -> str:
    """
    tg://user?id=<id> opens a DM with OWNER_ID directly — works even
    without a public @username and needs no manual URL to edit before
    commit. Falls back to OWNER_CONTACT_URL only if OWNER_ID isn't set.
    """
    return f"tg://user?id={OWNER_ID}" if OWNER_ID else OWNER_CONTACT_URL