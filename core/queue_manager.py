"""
core/queue_manager.py — Centralized token-bucket approval queue manager with ETA and live tracking.

Coordinates backlog approvals, live request throttling, multi-channel scheduling,
and status monitoring.
"""

import asyncio
import datetime
import inspect
import uuid
from typing import Any, Callable, Dict, List, Optional
from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError
import config
from config import LOGGER
from database import db
from helpers import limiter

UTC = datetime.timezone.utc


class ApprovalJob:
    """Represents a queued or currently executing approval job for a specific chat."""

    def __init__(
        self,
        job_id: str,
        chat_id: int,
        limit: Optional[int] = None,
        requested_by: Optional[int] = None,
    ):
        self.job_id = job_id
        self.chat_id = chat_id
        self.limit = limit  # None means unlimited
        self.requested_by = requested_by
        self.status = "queued"  # "queued", "running", "completed", "cancelled", "failed"
        self.approved = 0
        self.processed = 0
        self.total_estimated = limit
        self.created_at = datetime.datetime.now(UTC)
        self.started_at: Optional[datetime.datetime] = None
        self.completed_at: Optional[datetime.datetime] = None
        self.error: Optional[str] = None
        self.cancel_event = asyncio.Event()

    def to_dict(self, position: int = 1) -> Dict[str, Any]:
        rate = max(1.0, float(config.MAX_APPROVALS_PER_SECOND))
        if self.limit is not None:
            remaining = max(0, self.limit - self.approved)
            eta = int(remaining / rate) if rate > 0 else 0
        else:
            eta = 0

        return {
            "job_id": self.job_id,
            "chat_id": self.chat_id,
            "position": position,
            "waiting": max(0, position - 1),
            "status": self.status,
            "is_running": self.status == "running",
            "approved": self.approved,
            "processed": self.processed,
            "limit": self.limit,
            "eta_seconds": eta,
            "started_at": self.started_at,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


class QueueManager:
    """
    Singleton queue manager tracking active and pending approval jobs across all chats.
    """

    def __init__(self):
        self._active_jobs: Dict[int, ApprovalJob] = {}  # chat_id -> ApprovalJob
        self._job_order: List[int] = []  # List of chat_ids in queue order
        self._lock = asyncio.Lock()

    def is_running(self, chat_id: int) -> bool:
        """Return True if a job is actively running or queued for the given chat."""
        job = self._active_jobs.get(chat_id)
        return bool(job and job.status in ("queued", "running"))

    def get_status(self, chat_id: int) -> Optional[Dict[str, Any]]:
        """
        Get live status, queue position, ETA, and progress metrics for a chat's approval job.
        """
        job = self._active_jobs.get(chat_id)
        if not job:
            return None

        try:
            position = self._job_order.index(chat_id) + 1
        except ValueError:
            position = 1

        return job.to_dict(position=position)

    def get_all_active_jobs(self) -> List[Dict[str, Any]]:
        """Return status for all currently active and queued approval jobs."""
        results = []
        for idx, chat_id in enumerate(self._job_order, 1):
            job = self._active_jobs.get(chat_id)
            if job:
                results.append(job.to_dict(position=idx))
        return results

    def cancel_job(self, chat_id: int) -> bool:
        """
        Signal cancellation for an active or queued job.
        """
        job = self._active_jobs.get(chat_id)
        if job and job.status in ("queued", "running"):
            job.cancel_event.set()
            job.status = "cancelled"
            LOGGER.info(f"Cancellation signaled for approval job {job.job_id} in Chat {chat_id}")
            return True
        return False

    async def enqueue(
        self,
        chat_id: int,
        limit: Optional[int] = None,
        requested_by: Optional[int] = None,
        client: Optional[Client] = None,
        progress_callback: Optional[Callable[[ApprovalJob], Any]] = None,
    ) -> str:
        """
        Enqueue and initiate a new approval job for the chat.
        If a job is already running, returns the existing job ID.
        """
        async with self._lock:
            if self.is_running(chat_id):
                return self._active_jobs[chat_id].job_id

            job_id = str(uuid.uuid4())[:8]
            job = ApprovalJob(
                job_id=job_id,
                chat_id=chat_id,
                limit=limit,
                requested_by=requested_by,
            )
            self._active_jobs[chat_id] = job
            self._job_order.append(chat_id)

        # Start asynchronous worker task
        asyncio.create_task(self._process_job(job, client, progress_callback))
        return job_id

    async def _process_job(
        self,
        job: ApprovalJob,
        client: Optional[Client],
        progress_callback: Optional[Callable[[ApprovalJob], Any]],
    ):
        """Worker executing approval operations through the token-bucket rate limiter."""
        job.status = "running"
        job.started_at = datetime.datetime.now(UTC)
        LOGGER.info(f"Started approval job {job.job_id} for Chat {job.chat_id} (Limit: {job.limit})")

        from core.session_manager import get_client_for_user

        user_client = None
        target_client = client

        # If user session exists for the requester, use it for backlog iteration
        if job.requested_by:
            user_client = await get_client_for_user(job.requested_by)
            if user_client:
                try:
                    await user_client.start()
                    target_client = user_client
                except Exception as e:
                    LOGGER.warning(f"Could not start user client for backlog: {e}; falling back to bot client.")
                    user_client = None

        try:
            if not target_client:
                raise RuntimeError("No Telegram client available for approval processing.")

            # Iterate over pending requests
            async for req in target_client.get_chat_join_requests(chat_id=job.chat_id):
                if job.cancel_event.is_set():
                    job.status = "cancelled"
                    break

                # Strict limit check: stop as soon as limit is reached
                if job.limit is not None and job.approved >= job.limit:
                    job.status = "completed"
                    break

                user = req.from_user
                await limiter.acquire()

                # Approve request using client
                for attempt in range(3):
                    if job.cancel_event.is_set():
                        break
                    try:
                        # Bot client or user client can approve
                        if client:
                            await client.approve_chat_join_request(chat_id=job.chat_id, user_id=user.id)
                        else:
                            await target_client.approve_chat_join_request(chat_id=job.chat_id, user_id=user.id)

                        job.approved += 1
                        await db.bump_stat(job.chat_id, approved=True)
                        break
                    except FloodWait as fw:
                        limiter.flood_wait(fw.value)
                        await asyncio.sleep(fw.value + 1)
                    except RPCError as rpc:
                        LOGGER.debug(f"RPC error during approval: {rpc}")
                        break
                    except Exception as e:
                        LOGGER.debug(f"Approval error on attempt {attempt + 1}: {e}")
                        await asyncio.sleep(1)

                job.processed += 1

                # Send progress update every 5 items or on limit completion
                if progress_callback and (job.processed % 5 == 0 or (job.limit and job.approved >= job.limit)):
                    try:
                        if inspect.iscoroutinefunction(progress_callback):
                            await progress_callback(job)
                        else:
                            progress_callback(job)
                    except Exception:
                        pass

                if job.limit is not None and job.approved >= job.limit:
                    job.status = "completed"
                    break

            if job.status == "running":
                job.status = "completed"

        except Exception as e:
            if not job.cancel_event.is_set():
                job.status = "failed"
                job.error = str(e)
                LOGGER.error(f"Approval job {job.job_id} error in Chat {job.chat_id}: {e}")
        finally:
            job.completed_at = datetime.datetime.now(UTC)
            if user_client and user_client.is_connected:
                try:
                    await user_client.stop()
                except Exception:
                    pass

            # Final progress callback invocation
            if progress_callback:
                try:
                    if inspect.iscoroutinefunction(progress_callback):
                        await progress_callback(job)
                    else:
                        progress_callback(job)
                except Exception:
                    pass

            # Clean up active job tracking
            async with self._lock:
                if job.chat_id in self._job_order:
                    self._job_order.remove(job.chat_id)
                self._active_jobs.pop(job.chat_id, None)

            LOGGER.info(
                f"Finished approval job {job.job_id} for Chat {job.chat_id}: "
                f"status={job.status}, approved={job.approved:,}, processed={job.processed:,}"
            )


# Global singleton instance
queue_manager = QueueManager()


# Module-level convenience functions
def enqueue(*args, **kwargs):
    return queue_manager.enqueue(*args, **kwargs)


def get_status(chat_id: int):
    return queue_manager.get_status(chat_id)


def is_running(chat_id: int):
    return queue_manager.is_running(chat_id)


def cancel_job(chat_id: int):
    return queue_manager.cancel_job(chat_id)
