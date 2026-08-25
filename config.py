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

# ─── Helper Functions ───────────────────────────────────────────────────────
def is_owner(user_id: int) -> bool:
    return OWNER_ID != 0 and user_id == OWNER_ID

def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or user_id in ADMINS
