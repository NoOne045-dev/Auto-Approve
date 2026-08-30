"""
webserver.py — Minimal aiohttp web server.

Render's "Web Service" type requires the process to bind to $PORT and
respond to HTTP requests, otherwise the deploy is marked as failed/unhealthy
even though the Telegram bot itself only needs long-polling (no webhook).

This module starts a tiny aiohttp server alongside the Pyrogram client so
Render's health checks (and the free-tier "port scan") succeed.

If you later want an actual Telegram webhook (instead of long polling),
add a POST route here that feeds updates into `app.dispatcher`, but for a
Kurigram/Pyrogram bot using `idle()`, long polling is what's actually
running — this server exists purely to satisfy Render's port requirement.
"""

from aiohttp import web
import config
from config import LOGGER

routes = web.RouteTableDef()


@routes.get("/")
async def root(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "AutoApproveBot"})


@routes.get("/healthz")
async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy"})


async def start_webserver() -> web.AppRunner:
    """Start the aiohttp server on config.PORT and return the runner so it
    can be cleaned up on shutdown."""
    web_app = web.Application()
    web_app.add_routes(routes)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.PORT)
    await site.start()

    LOGGER.info(f"Web server listening on 0.0.0.0:{config.PORT} (for Render health checks)")
    return runner