"""
core/scheduler.py — Job scheduling engine with MongoDB persistence and timezone handling.

Provides persistent scheduled approvals surviving bot restarts, timezone conversion,
and execution orchestration via core/queue_manager.
"""

import asyncio
import datetime
import re
import uuid
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from pyrogram import Client
import config
from config import LOGGER
from database import db
from core.queue_manager import queue_manager, ApprovalJob

UTC = datetime.timezone.utc


def _get_tz(tz_name: Optional[str]) -> datetime.tzinfo:
    """Safely get a tzinfo object for the given IANA timezone name."""
    if not tz_name or tz_name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return UTC


def parse_schedule_time(
    time_str: str,
    tz_name: str = "UTC",
) -> datetime.datetime:
    """
    Parse a user-provided date/time string or relative offset into a UTC datetime.
    Supports:
    - Relative offsets: '+5m', '+15m', '+30m', '+1h', '+2h', '+1d'
    - Formats: 'YYYY-MM-DD HH:MM', 'YYYY-MM-DD HH:MM:SS', 'HH:MM' (today/tomorrow)
    """
    time_str = time_str.strip()
    user_tz = _get_tz(tz_name)
    now_user_tz = datetime.datetime.now(user_tz)

    # 1. Relative offset (e.g. +10m, +2h, +1d)
    rel_match = re.match(r"^\+?(\d+)\s*(m|min|mins|minutes|h|hr|hrs|hours|d|day|days)$", time_str, re.IGNORECASE)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        if unit.startswith("m"):
            delta = datetime.timedelta(minutes=val)
        elif unit.startswith("h"):
            delta = datetime.timedelta(hours=val)
        elif unit.startswith("d"):
            delta = datetime.timedelta(days=val)
        else:
            delta = datetime.timedelta(minutes=val)

        target_dt = now_user_tz + delta
        return target_dt.astimezone(UTC)

    # 2. Explicit Date & Time: YYYY-MM-DD HH:MM(:SS)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            naive_dt = datetime.datetime.strptime(time_str, fmt)
            aware_dt = naive_dt.replace(tzinfo=user_tz)
            return aware_dt.astimezone(UTC)
        except ValueError:
            pass

    # 3. Time only: HH:MM (schedule for today or tomorrow if time has already passed)
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            naive_t = datetime.datetime.strptime(time_str, fmt).time()
            candidate = datetime.datetime.combine(now_user_tz.date(), naive_t, tzinfo=user_tz)
            if candidate <= now_user_tz:
                candidate += datetime.timedelta(days=1)
            return candidate.astimezone(UTC)
        except ValueError:
            pass

    raise ValueError(
        f"Could not parse '{time_str}'. Use '+10m', '+1h', 'HH:MM', or 'YYYY-MM-DD HH:MM'."
    )


class JobScheduler:
    """
    Persistent async scheduler polling MongoDB 'schedules' collection.
    Survives restarts and delegates execution to core/queue_manager.
    """

    def __init__(self):
        self.client: Optional[Client] = None
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._poll_interval = 5.0  # Poll every 5 seconds

    def start(self, client: Client):
        """Start the scheduler background polling loop."""
        if self._running:
            return
        self.client = client
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        LOGGER.info("Persistent JobScheduler started.")

    async def stop(self):
        """Stop the scheduler polling loop."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        LOGGER.info("JobScheduler stopped.")

    async def _poll_loop(self):
        """Background poller checking MongoDB for due jobs."""
        while self._running:
            try:
                await self._check_and_run_due_jobs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error(f"Scheduler poller error: {e}")

            await asyncio.sleep(self._poll_interval)

    async def _check_and_run_due_jobs(self):
        """Query MongoDB for pending jobs whose run_at <= current UTC time."""
        col = db.get_schedules_collection()
        if col is None:
            return

        now_utc = datetime.datetime.now(UTC)
        cursor = col.find({"status": "pending", "run_at": {"$lte": now_utc}})
        due_jobs = await cursor.to_list(length=20)

        for job_doc in due_jobs:
            job_id = job_doc["job_id"]
            # Atomically mark as running to prevent duplicate executions
            claim = await col.update_one(
                {"job_id": job_id, "status": "pending"},
                {"$set": {"status": "running", "started_at": datetime.datetime.now(UTC)}},
            )
            if claim.modified_count > 0:
                LOGGER.info(f"Triggering scheduled job {job_id} for Chat {job_doc['chat_id']}")
                asyncio.create_task(self._execute_scheduled_job(job_doc))

    async def _execute_scheduled_job(self, job_doc: dict):
        """Execute a scheduled approval job via queue_manager and record results."""
        job_id = job_doc["job_id"]
        chat_id = job_doc["chat_id"]
        limit = job_doc.get("limit")
        user_id = job_doc.get("user_id")
        col = db.get_schedules_collection()

        try:
            # Enqueue into the central queue manager
            exec_job_id = await queue_manager.enqueue(
                chat_id=chat_id,
                limit=limit,
                requested_by=user_id,
                client=self.client,
            )

            # Wait for queue execution to finish
            while queue_manager.is_running(chat_id):
                await asyncio.sleep(1.0)

            # Mark completed in DB
            now = datetime.datetime.now(UTC)
            await col.update_one(
                {"job_id": job_id},
                {
                    "$set": {
                        "status": "completed",
                        "completed_at": now,
                        "exec_job_id": exec_job_id,
                    }
                },
            )
            LOGGER.info(f"Scheduled approval job {job_id} in Chat {chat_id} completed successfully.")

            # Notify requester in PM if client available
            if self.client and user_id:
                try:
                    await self.client.send_message(
                        chat_id=user_id,
                        text=(
                            f"🔔 <b>Scheduled Approval Complete!</b>\n\n"
                            f"• <b>Job ID:</b> <code>{job_id}</code>\n"
                            f"• <b>Chat ID:</b> <code>{chat_id}</code>\n"
                            f"• <b>Limit:</b> {f'{limit:,}' if limit else 'Unlimited'}\n"
                            f"• <b>Status:</b> Completed ✅"
                        ),
                    )
                except Exception:
                    pass

        except Exception as e:
            LOGGER.error(f"Execution error for scheduled job {job_id}: {e}")
            if col is not None:
                await col.update_one(
                    {"job_id": job_id},
                    {
                        "$set": {
                            "status": "failed",
                            "error": str(e),
                            "completed_at": datetime.datetime.now(UTC),
                        }
                    },
                )


# Global singleton scheduler instance
scheduler = JobScheduler()


# ─── Schedule CRUD Helpers ──────────────────────────────────────────────────
async def create_schedule(
    chat_id: int,
    user_id: int,
    run_at: datetime.datetime,
    limit: Optional[int] = None,
    timezone: str = "UTC",
    time_str: str = "",
) -> str:
    """
    Create and persist a new scheduled approval job in MongoDB.
    """
    col = db.get_schedules_collection()
    if col is None:
        raise RuntimeError("MongoDB database not connected.")

    job_id = f"sch_{uuid.uuid4().hex[:6]}"
    now = datetime.datetime.now(UTC)

    doc = {
        "job_id": job_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "limit": limit,
        "run_at": run_at,
        "timezone": timezone,
        "time_str": time_str,
        "status": "pending",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
    }

    await col.insert_one(doc)
    LOGGER.info(f"Created scheduled approval job {job_id} for Chat {chat_id} at {run_at.isoformat()} UTC")
    return job_id


async def get_schedule(job_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single schedule document by its job_id."""
    col = db.get_schedules_collection()
    if col is None:
        return None
    return await col.find_one({"job_id": job_id})


async def cancel_schedule(job_id: str, user_id: Optional[int] = None) -> bool:
    """Cancel a pending scheduled job."""
    col = db.get_schedules_collection()
    if col is None:
        return False

    query: Dict[str, Any] = {"job_id": job_id, "status": "pending"}
    if user_id and not config.is_admin(user_id):
        query["user_id"] = user_id

    res = await col.update_one(query, {"$set": {"status": "cancelled", "completed_at": datetime.datetime.now(UTC)}})
    return res.modified_count > 0


async def list_schedules(
    user_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Query scheduled approval jobs from MongoDB."""
    col = db.get_schedules_collection()
    if col is None:
        return []

    query: Dict[str, Any] = {}
    if user_id and not config.is_admin(user_id):
        query["user_id"] = user_id
    if chat_id:
        query["chat_id"] = chat_id
    if status:
        query["status"] = status

    return await col.find(query).sort("run_at", 1).to_list(length=limit)
