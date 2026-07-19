"""Shared aiogram Bot/Dispatcher singletons."""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings

bot: Bot | None = None
dp: Dispatcher | None = None
bot_username: str = ""


def set_bot_username(username: str) -> None:
    global bot_username
    bot_username = username


def get_bot_username() -> str:
    return bot_username or settings.BOT_USERNAME


def get_bot() -> Bot:
    global bot
    if bot is None:
        if not settings.BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN is not configured")
        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return bot


def get_dispatcher() -> Dispatcher:
    global dp
    if dp is None:
        dp = Dispatcher()
    return dp
