"""
main.py — Main application entry point.
Powered by Kurigram (actively maintained Pyrogram fork) and Async MongoDB (Motor).
"""
import asyncio
import sys

from pyrogram import Client, idle
from pyrogram.enums import ParseMode

import config
from config import LOGGER
from database import db
from plugins.join_request import start_approval_workers
from webserver import start_webserver

# Initialize Kurigram client with plugins directory
app = Client(
    name="AutoApproveBot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    plugins=dict(root="plugins"),
    workers=config.MAX_APPROVALS_PER_SECOND * 2,
    parse_mode=ParseMode.HTML,
)


async def main():
    LOGGER.info("=" * 60)
    LOGGER.info("Starting Auto-Approve Bot (Powered by Kurigram)...")
    LOGGER.info("=" * 60)

    # Validate essential environment credentials
    if not config.BOT_TOKEN or not config.API_ID or not config.API_HASH:
        LOGGER.critical("CRITICAL: BOT_TOKEN, API_ID, and API_HASH must be set in .env!")
        LOGGER.critical("Please copy .env.example to .env and configure your credentials.")
        sys.exit(1)

    if not config.MONGO_URL:
        LOGGER.critical("CRITICAL: MONGO_URL is missing in .env! MongoDB is required.")
        sys.exit(1)

    # Validate session encryption key
    if not config.validate_session_encryption_key():
        LOGGER.critical("CRITICAL: SESSION_ENCRYPTION_KEY is missing, placeholder, or invalid in .env!")
        LOGGER.critical("Please set a valid 32-byte Fernet key. Generate one with:")
        LOGGER.critical("python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        sys.exit(1)

    # Connect to MongoDB
    await db.connect()

    # Start web server first — Render marks a "Web Service" deploy as failed
    # if nothing binds to $PORT within the deploy timeout.
    runner = await start_webserver()
    # Start Telegram Client
    await app.start()
    me = await app.get_me()
    LOGGER.info(f"Bot started successfully as @{me.username} (ID: {me.id})")

    # Start async approval queue workers
    start_approval_workers(app, count=4)

    # Start persistent job scheduler
    from core.scheduler import scheduler
    scheduler.start(app)

    LOGGER.info("Bot is active, scheduler is running, and listening for join requests...")

    # Keep running until terminated
    await idle()

    # Graceful shutdown
    LOGGER.info("Stopping bot gracefully...")
    await scheduler.stop()
    await app.stop()
    await runner.cleanup()
    await db.close()
    LOGGER.info("Bot stopped successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("Bot exited.")