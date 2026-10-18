"""Coin shop — Telegram Stars packs."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import appconfig
from app.api.deps import get_current_user
from app.bot.instance import get_bot
from app.database import get_session
from app.models import User
from app.models.wallet import StarPayment, StarPaymentStatus
from app.schemas import StarsInvoiceOut, StarsInvoiceRequest, StarsPack, StarsPacksOut
from app.services.wallet import STAR_PACKS

logger = logging.getLogger("ludo.shop")
router = APIRouter(prefix="/api/shop", tags=["shop"])


@router.get("/stars/packs", response_model=StarsPacksOut)
async def stars_packs(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    cfg = await appconfig.current_values(session)
    rate = max(1, int(cfg.get("COINS_PER_STAR", 50)))
    return StarsPacksOut(
        coins_per_star=rate,
        packs=[StarsPack(stars=s, coins=s * rate) for s in STAR_PACKS],
    )


@router.post("/stars/invoice", response_model=StarsInvoiceOut)
async def stars_invoice(
    body: StarsInvoiceRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    pack = int(body.pack_stars)
    if pack not in STAR_PACKS:
        raise HTTPException(400, f"Invalid pack; choose one of {list(STAR_PACKS)}")

    cfg = await appconfig.current_values(session)
    rate = max(1, int(cfg.get("COINS_PER_STAR", 50)))
    coins = pack * rate

    payment = StarPayment(
        user_id=user.id,
        stars=pack,
        coins=coins,
        pack_stars=pack,
        status=StarPaymentStatus.PENDING.value,
    )
    session.add(payment)
    await session.flush()

    try:
        from aiogram.types import LabeledPrice

        bot = get_bot()
        link = await bot.create_invoice_link(
            title=f"{coins:,} Ludo coins",
            description=f"Buy {coins:,} coins with {pack} Telegram Stars",
            payload=payment.invoice_payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"{pack} Stars", amount=pack)],
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("createInvoiceLink failed")
        payment.status = StarPaymentStatus.FAILED.value
        await session.commit()
        raise HTTPException(502, "Could not create Stars invoice") from e

    await session.commit()
    return StarsInvoiceOut(
        invoice_link=link,
        payment_id=payment.id,
        stars=pack,
        coins=coins,
    )
