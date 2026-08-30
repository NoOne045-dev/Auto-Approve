"""
core/cache.py — In-memory TTL caching layer for chat configurations, permissions, and plans.

Reduces MongoDB query load during high-throughput join request processing.

TODOs:
- TODO: Implement async-safe TTLCache with configurable CACHE_TTL_SECONDS.
- TODO: Add cached getters for chat settings: get_cached_chat(chat_id).
- TODO: Add cache invalidation hooks on settings update: invalidate_chat(chat_id).
- TODO: Add cached getters for user plan tiers and permissions.
- TODO: Implement automatic background cleanup of expired cache entries.
"""

