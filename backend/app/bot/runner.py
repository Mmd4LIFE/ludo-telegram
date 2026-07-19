"""Bot startup: registers handlers and runs polling (or webhook) mode."""
from __future__ import annotations

import asyncio
import logging

from aiogram.types import BotCommand

from app.bot.handlers import router as handlers_router
from app.bot.instance import get_bot, get_dispatcher, set_bot_username
from app.config import settings

logger = logging.getLogger("ludo.bot")

_polling_task: asyncio.Task | None = None

COMMANDS = [
    BotCommand(command="start", description="Start / open the game"),
    BotCommand(command="play", description="Open the Ludo board"),
    BotCommand(command="help", description="How to play"),
]


async def setup_bot() -> None:
    global _polling_task
    if not settings.BOT_TOKEN:
        logger.warning("BOT_TOKEN not set — Telegram bot disabled")
        return
    bot = get_bot()
    dp = get_dispatcher()
    dp.include_router(handlers_router)

    try:
        me = await bot.get_me()
        set_bot_username(me.username or "")
        logger.info("Bot is @%s", me.username)
    except Exception:  # noqa: BLE001
        logger.exception("getMe failed")

    try:
        await bot.set_my_commands(COMMANDS)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to set commands")

    try:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🎲 Play", web_app=WebAppInfo(url=settings.WEBAPP_URL)
            )
        )
        logger.info("Menu button set to %s", settings.WEBAPP_URL)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to set menu button")

    if settings.BOT_MODE == "webhook":
        webhook_url = f"{settings.PUBLIC_URL}/webhook/{settings.WEBHOOK_SECRET}"
        await bot.set_webhook(
            webhook_url, drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
        logger.info("Webhook set to %s", webhook_url)
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        _polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        logger.info("Bot polling started")


async def shutdown_bot() -> None:
    global _polling_task
    if _polling_task:
        _polling_task.cancel()
    try:
        bot = get_bot()
        await bot.session.close()
    except Exception:  # noqa: BLE001
        pass
