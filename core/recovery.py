"""
core/recovery.py — Auto-reconnect supervisor, crash recovery, health checks, and state restoration.

Ensures resilient bot operation across network drops, MTProto disconnects, and unexpected restarts.
Provides state recovery for in-flight jobs and MongoDB latency measurements.
"""

import asyncio
import datetime
import time
from typing import Any, Callable, Dict, Optional
from pyrogram import Client
from pyrogram.errors import RPCError
import config
from config import LOGGER
from database import db
from core.queue_manager import queue_manager

UTC = datetime.timezone.utc

# Track last successful approval timestamp globally
_last_approval_time: Optional[datetime.datetime] = None


def record_last_approval_timestamp() -> None:
    """Record timestamp of the most recent successful approval."""
    global _last_approval_time
    _last_approval_time = datetime.datetime.now(UTC)


def get_last_approval_timestamp() -> Optional[datetime.datetime]:
    """Retrieve timestamp of the last successful approval."""
    return _last_approval_time


async def get_db_latency_ms() -> float:
    """
    Measure round-trip ping latency to MongoDB in milliseconds.
    Returns -1.0 if database is unavailable.
    """
    if db.db is None:
        return -1.0
    start = time.monotonic()
    try:
        await db.db.command("ping")
        latency = (time.monotonic() - start) * 1000.0
        return round(latency, 2)
    except Exception as e:
        LOGGER.error(f"[RECOVERY | DB_PING_ERROR] MongoDB ping failed: {e}")
        return -1.0


async def recover_inflight_jobs(client: Optional[Client] = None) -> int:
    """
    Scan MongoDB for scheduled approval jobs stuck in 'running' state due to a bot crash or restart.
    Resets them to 'pending' state so the JobScheduler immediately re-claims and resumes them.
    Returns the count of recovered jobs.
    """
    col = db.get_schedules_collection()
    if col is None:
        return 0

    cursor = col.find({"status": "running"})
    interrupted_jobs = await cursor.to_list(length=100)

    recovered_count = 0
    for doc in interrupted_jobs:
        job_id = doc["job_id"]
        chat_id = doc["chat_id"]
        LOGGER.warning(
            f"[RECOVERY | INFLIGHT_RESTORE | job_id={job_id} | chat_id={chat_id}] "
            f"Restoring interrupted scheduled job to pending state."
        )
        await col.update_one(
            {"job_id": job_id, "status": "running"},
            {
                "$set": {
                    "status": "pending",
                    "recovered_at": datetime.datetime.now(UTC),
                }
            },
        )
        recovered_count += 1

    if recovered_count > 0:
        LOGGER.info(f"[RECOVERY | STATE_RELOADED] Successfully restored {recovered_count} interrupted jobs from MongoDB.")

    return recovered_count


class BotSupervisor:
    """
    Supervisor loop wrapping Telegram MTProto Client connection with exponential backoff.
    Auto-reconnects on network crashes or disconnects without dropping state.
    """

    def __init__(self, app: Client):
        self.app = app
        self.max_backoff = 60.0
        self.initial_backoff = 1.0

    async def run(self, on_started_coro: Optional[Callable] = None):
        """Run bot client inside an auto-reconnect supervisor loop."""
        backoff = self.initial_backoff
        attempt = 0

        while True:
            try:
                attempt += 1
                LOGGER.info(f"[SUPERVISOR | CONNECT_ATTEMPT | attempt={attempt}] Starting Telegram Client...")
                await self.app.start()

                # Restore interrupted state from MongoDB
                await recover_inflight_jobs(self.app)

                backoff = self.initial_backoff  # Reset backoff on successful connection

                if on_started_coro:
                    if asyncio.iscoroutinefunction(on_started_coro):
                        await on_started_coro()
                    else:
                        on_started_coro()

                # Keep running until disconnected or stopped
                from pyrogram import idle
                await idle()

                LOGGER.info("[SUPERVISOR | DISCONNECT] Client stopped cleanly.")
                break

            except (RPCError, ConnectionError, OSError) as e:
                LOGGER.error(f"[SUPERVISOR | CLIENT_CRASH | attempt={attempt}] Connection dropped: {e}")
                LOGGER.info(f"[SUPERVISOR | RETRY_BACKOFF] Waiting {backoff:.1f}s before reconnecting...")
                await asyncio.sleep(backoff)
                backoff = min(self.max_backoff, backoff * 2.0)
            except asyncio.CancelledError:
                LOGGER.info("[SUPERVISOR | SHUTDOWN] Supervisor loop cancelled.")
                break
            except Exception as e:
                LOGGER.critical(f"[SUPERVISOR | UNHANDLED_CRASH] Unexpected error in supervisor: {e}")
                await asyncio.sleep(backoff)
                backoff = min(self.max_backoff, backoff * 2.0)
