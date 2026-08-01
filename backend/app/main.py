"""FastAPI application entrypoint. Runs the REST API, the WebSocket and the bot."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_admin,
    routes_auth,
    routes_matches,
    routes_profile,
    routes_stats,
    routes_ws,
)
from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ludo")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.bot.runner import setup_bot, shutdown_bot
    from app.game.manager import manager

    try:
        await setup_bot()
    except Exception:  # noqa: BLE001
        logger.exception("Bot setup failed (continuing without bot)")
    manager.start_janitor()
    yield
    await manager.shutdown()
    await shutdown_bot()


app = FastAPI(title="Ludo Board", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (routes_auth, routes_profile, routes_matches, routes_stats, routes_ws, routes_admin):
    app.include_router(module.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "ludo-board", "version": app.version}


@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != settings.WEBHOOK_SECRET:
        return Response(status_code=403)
    from aiogram.types import Update

    from app.bot.instance import get_bot, get_dispatcher

    data = await request.json()
    update = Update.model_validate(data, context={"bot": get_bot()})
    await get_dispatcher().feed_update(get_bot(), update)
    return {"ok": True}
