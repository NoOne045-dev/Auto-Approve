"""
main.py — Main application entry point with production-grade reliability supervisor & graceful shutdown.
Powered by Kurigram (actively maintained Pyrogram fork) and Async MongoDB (Motor).
"""

import asyncio
import signal
import sys
from pyrogram import Client, idle
from pyrogram.enums import ParseMode

import config
from config import LOGGER
from database import db
from core.queue_manager import queue_manager
from core.recovery import recover_inflight_jobs
from plugins.join_request import start_approval_workers
from webserver import start_webserver

# Initialize Kurigram client with plugins directory
app = Client(
    name="AutoApproveBot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    plugins=dict(root="plugins"),
    workers=config.MAX_APPROVALS_PER_SECOND * 100,
    parse_mode=ParseMode.HTML,
)


async def graceful_shutdown(runner=None):
    """Graceful shutdown handler stopping queue acceptance, scheduler, web server, and DB connection."""
    LOGGER.info("[SHUTDOWN | INITIATED] Graceful shutdown signal received...")
    
    # 1. Stop accepting new queue jobs
    queue_manager.stop_accepting_jobs()
    
    # 2. Stop persistent job scheduler
    from core.scheduler import scheduler
    try:
        await scheduler.stop()
        LOGGER.info("[SHUTDOWN | SCHEDULER] Scheduler stopped cleanly.")
    except Exception as e:
        LOGGER.error(f"[SHUTDOWN | SCHEDULER_ERROR] {e}")

    # 3. Stop Telegram Client
    try:
        if app.is_connected:
            await app.stop()
            LOGGER.info("[SHUTDOWN | TELEGRAM] Pyrogram client disconnected.")
    except Exception as e:
        LOGGER.error(f"[SHUTDOWN | TELEGRAM_ERROR] {e}")

    # 4. Stop web server
    if runner:
        try:
            await runner.cleanup()
            LOGGER.info("[SHUTDOWN | WEBSERVER] Web server stopped.")
        except Exception as e:
            LOGGER.error(f"[SHUTDOWN | WEBSERVER_ERROR] {e}")

    # 5. Close MongoDB connection
    try:
        await db.close()
        LOGGER.info("[SHUTDOWN | DATABASE] MongoDB connection closed.")
    except Exception as e:
        LOGGER.error(f"[SHUTDOWN | DATABASE_ERROR] {e}")

    LOGGER.info("[SHUTDOWN | COMPLETE] All bot processes terminated cleanly.")


async def main():
    LOGGER.info("=" * 60)
    LOGGER.info("[STARTUP | INIT] Starting Auto-Approve Bot (Powered by Kurigram)...")
    LOGGER.info("=" * 60)

    # Validate essential environment credentials
    if not config.BOT_TOKEN or not config.API_ID or not config.API_HASH:
        LOGGER.critical("[STARTUP | CRITICAL] BOT_TOKEN, API_ID, and API_HASH must be set in .env!")
        sys.exit(1)

    if not config.MONGO_URL:
        LOGGER.critical("[STARTUP | CRITICAL] MONGO_URL is missing in .env! MongoDB is required.")
        sys.exit(1)

    # Validate session encryption key
    if not config.validate_session_encryption_key():
        LOGGER.critical("[STARTUP | CRITICAL] SESSION_ENCRYPTION_KEY is missing, placeholder, or invalid in .env!")
        sys.exit(1)

    # Connect to MongoDB
    await db.connect()

    # Start web server first for Render port binding
    runner = await start_webserver()

    # Start Telegram Client
    await app.start()
    me = await app.get_me()
    LOGGER.info(f"[STARTUP | CONNECTED] Bot active as @{me.username} (ID: {me.id})")

    # Restore in-flight interrupted jobs from MongoDB
    await recover_inflight_jobs(app)

    # Start async approval queue workers
    start_approval_workers(app, count=4)

    # Start persistent job scheduler
    from core.scheduler import scheduler
    scheduler.start(app)

    LOGGER.info("[STARTUP | ACTIVE] Bot listening for join requests...")

    # Attach signal handlers for graceful shutdown on Linux/macOS
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(graceful_shutdown(runner)))
        except (NotImplementedError, RuntimeError):
            pass

    # Keep running until terminated
    try:
        await idle()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await graceful_shutdown(runner)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("[SHUTDOWN] Bot process exited.")
