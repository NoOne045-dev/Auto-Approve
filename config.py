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

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("AutoApproveBot")

# ─── Scheduler & Quota ───────────────────────────────────────────────────────
SCHEDULER_TIMEZONE: str = os.getenv("SCHEDULER_TIMEZONE", "UTC").strip() or "UTC"
DEFAULT_PLAN: str = os.getenv("DEFAULT_PLAN", "FREE").strip() or "FREE"
QUOTA_RESET_HOUR: int = int(os.getenv("QUOTA_RESET_HOUR", "0") if os.getenv("QUOTA_RESET_HOUR", "").strip().isdigit() else 0)
CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "300") if os.getenv("CACHE_TTL_SECONDS", "").strip().isdigit() else 300)

# ─── Security & Cryptography ────────────────────────────────────────────────
SESSION_ENCRYPTION_KEY: str = os.getenv("SESSION_ENCRYPTION_KEY", "").strip()

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

