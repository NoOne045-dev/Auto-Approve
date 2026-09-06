"""
core/cache.py — In-memory TTL caching layer for chat configurations, permissions, and plans.

Reduces MongoDB query load during high-throughput join request and management operations.
"""

import time
from typing import Any, Callable, Dict, Optional
import config


class TTLCache:
    """Simple thread-safe in-memory cache with Time-To-Live (TTL) expiration."""

    def __init__(self, default_ttl: int = 300):
        self.default_ttl = default_ttl
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() > entry["expires_at"]:
            del self._cache[key]
            return None
        return entry["value"]

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl_val = ttl if ttl is not None else self.default_ttl
        self._cache[key] = {
            "value": value,
            "expires_at": time.time() + ttl_val,
        }

    def delete(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()


# Global cache instance
cache = TTLCache(default_ttl=getattr(config, "CACHE_TTL_SECONDS", 300))


# ─── Helper Functions for Chat & Admin Caching ─────────────────────────────
async def get_cached_chat(chat_id: int, fetch_coro: Optional[Callable] = None) -> Optional[dict]:
    """Retrieve chat config from cache, or fetch from DB and cache if missing."""
    key = f"chat:{chat_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    if fetch_coro:
        data = await fetch_coro(chat_id)
        if data:
            cache.set(key, data)
        return data
    return None


def set_cached_chat(chat_id: int, data: dict) -> None:
    """Store or update chat config in cache."""
    cache.set(f"chat:{chat_id}", data)


def invalidate_chat(chat_id: int) -> None:
    """Invalidate cached chat settings."""
    cache.delete(f"chat:{chat_id}")


async def get_cached_admin_chats(user_id: int, fetch_coro: Callable) -> list:
    """Retrieve list of admin-managed chats for a user, using cache."""
    key = f"user_chats:{user_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    chats = await fetch_coro(user_id)
    cache.set(key, chats)
    return chats


def invalidate_admin_chats(user_id: int) -> None:
    """Invalidate cached admin chats list for a user."""
    cache.delete(f"user_chats:{user_id}")
