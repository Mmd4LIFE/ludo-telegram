"""Telegram command handlers + Stars payment callbacks.

Minimal commands: /start (Mini App + referral / room / gift deep links), /play, /help.
Stars: answer pre_checkout_query and fulfill successful_payment idempotently.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

from app.config import settings
from app.database import SessionLocal
from app.services import ledger
from app.services import stars as stars_svc
from app.services.users import get_or_create_from_telegram

logger = logging.getLogger("ludo.bot")
router = Router()


def _play_kb(start_param: str | None = None, *, button: str | None = None) -> InlineKeyboardMarkup:
    # A WebApp button opens the Mini App at this exact URL; the app reads ?startapp=
    # (see lib/telegram.ts startParam) so a room-invite deep link joins that room.
    url = settings.WEBAPP_URL
    text = button or "🎲 Play Ludo"
    if start_param:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}startapp={start_param}"
        if button is None:
            text = "🎲 Open Ludo"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
    ]])


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    tg = message.from_user
    if tg is None:
        return
    payload = command.args  # after /start, e.g. "ref-42", "rm-ABCDE", "gift-TOKEN"
    referral = payload if payload and payload.startswith("ref-") else None
    room = payload if payload and payload.startswith("rm-") else None
    gift = payload if payload and payload.startswith("gift-") else None
    async with SessionLocal() as session:
        user, _created = await get_or_create_from_telegram(
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
            await ledger.credit(
                session, user.id, settings.BOT_START_BONUS, reason="bot_start"
            )
        await session.commit()

    if room:
        await message.answer(
            "🎲 <b>You're invited to a Ludo room!</b>\n\nTap below to join and play.",
            reply_markup=_play_kb(room, button="🎲 Join the room"),
        )
        return

    if gift:
        await message.answer(
            "🎁 <b>Someone sent you coins!</b>\n\nTap below to open the app and claim your gift.",
            reply_markup=_play_kb(gift, button="🎁 Claim gift"),
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


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    async with SessionLocal() as session:
        ok, err = await stars_svc.validate_pre_checkout(
            session,
            invoice_payload=query.invoice_payload,
            total_amount=query.total_amount,
            currency=query.currency,
        )
    await query.answer(ok=ok, error_message=err)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    payment = message.successful_payment
    if payment is None:
        return
    async with SessionLocal() as session:
        row = await stars_svc.fulfill_successful_payment(
            session,
            invoice_payload=payment.invoice_payload,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            total_amount=payment.total_amount,
            currency=payment.currency,
        )
        await session.commit()
    if row is not None and row.status == "paid":
        await message.answer(
            f"✅ Payment received — <b>{int(row.coins):,}</b> coins added to your wallet.",
            reply_markup=_play_kb(),
        )
    else:
        logger.error("Stars fulfillment failed for charge=%s", payment.telegram_payment_charge_id)
        await message.answer(
            "Payment received but we could not credit coins automatically. Contact support.",
            reply_markup=_play_kb(),
        )
