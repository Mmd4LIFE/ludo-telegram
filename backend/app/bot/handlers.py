"""Telegram command handlers.

Minimal for the base: /start (opens the Mini App + grants the one-off reachable bonus and
handles referral deep links), /play, /help. The game lives entirely in the Mini App; the
bot is the launcher and the notification channel.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.filters import Command

from app.config import settings
from app.database import SessionLocal
from app.services.users import get_or_create_from_telegram

logger = logging.getLogger("ludo.bot")
router = Router()


def _play_kb(start_param: str | None = None) -> InlineKeyboardMarkup:
    # A WebApp button opens the Mini App at this exact URL; the app reads ?startapp=
    # (see lib/telegram.ts startParam) so a room-invite deep link joins that room.
    url = settings.WEBAPP_URL
    text = "🎲 Play Ludo"
    if start_param:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}startapp={start_param}"
        text = "🎲 Join the room"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
    ]])


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    tg = message.from_user
    if tg is None:
        return
    payload = command.args  # after /start, e.g. "ref-42" or "rm-ABCDE"
    # Only ref-* is a referral; rm-* is a room invite handed straight to the Mini App.
    referral = payload if payload and payload.startswith("ref-") else None
    room = payload if payload and payload.startswith("rm-") else None
    async with SessionLocal() as session:
        user, created = await get_or_create_from_telegram(
            session, {
                "id": tg.id,
                "first_name": tg.first_name,
                "username": tg.username,
                "language_code": tg.language_code,
            },
            referral=referral,
        )
        if not user.bot_started:
            user.bot_started = True
            user.coins += settings.BOT_START_BONUS
        await session.commit()

    if room:
        await message.answer(
            "🎲 <b>You're invited to a Ludo room!</b>\n\nTap below to join and play.",
            reply_markup=_play_kb(room),
        )
        return

    await message.answer(
        "🎲 <b>Ludo Board</b>\n\n"
        "Roll, race your four tokens home, and knock rivals back to base.\n"
        "Play against friends or the house bots — tap below to start.",
        reply_markup=_play_kb(),
    )


@router.message(Command("play"))
async def cmd_play(message: Message) -> None:
    await message.answer("Tap to open the board:", reply_markup=_play_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>How to play Ludo</b>\n\n"
        "• Roll a 6 to bring a token out of your base.\n"
        "• Move a token by the number you roll.\n"
        "• Land on a rival (off a star square) to send it back to base.\n"
        "• Roll a 6, capture, or get a token home to roll again.\n"
        "• First to get all four tokens home wins.\n",
        reply_markup=_play_kb(),
    )
