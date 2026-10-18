"""Fulfill Telegram Stars purchases idempotently."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import StarPayment, StarPaymentStatus
from app.services import ledger

logger = logging.getLogger("ludo.stars")


async def fulfill_successful_payment(
    session: AsyncSession,
    *,
    invoice_payload: str,
    telegram_payment_charge_id: str,
    total_amount: int,
    currency: str,
) -> StarPayment | None:
    """Credit coins for a Stars payment. Safe to call more than once for the same charge."""
    if currency != "XTR":
        logger.warning("Ignoring non-XTR payment currency=%s", currency)
        return None

    # Already fulfilled by charge id?
    existing = (
        await session.execute(
            select(StarPayment).where(
                StarPayment.telegram_payment_charge_id == telegram_payment_charge_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    payment = (
        await session.execute(
            select(StarPayment).where(StarPayment.invoice_payload == invoice_payload)
        )
    ).scalar_one_or_none()
    if payment is None:
        logger.error("No star_payments row for payload=%s", invoice_payload)
        return None
    if payment.status == StarPaymentStatus.PAID.value:
        return payment
    if int(payment.stars) != int(total_amount):
        logger.error(
            "Stars amount mismatch payment=%s expected=%s got=%s",
            payment.id, payment.stars, total_amount,
        )
        payment.status = StarPaymentStatus.FAILED.value
        return payment

    payment.telegram_payment_charge_id = telegram_payment_charge_id
    payment.status = StarPaymentStatus.PAID.value
    payment.paid_at = datetime.now(timezone.utc)
    await ledger.credit(
        session,
        payment.user_id,
        int(payment.coins),
        reason="stars_purchase",
        ref_type="star_payment",
        ref_id=payment.id,
        meta={"stars": payment.stars, "charge_id": telegram_payment_charge_id},
    )
    return payment


async def validate_pre_checkout(
    session: AsyncSession,
    *,
    invoice_payload: str,
    total_amount: int,
    currency: str,
) -> tuple[bool, str | None]:
    if currency != "XTR":
        return False, "Unsupported currency"
    payment = (
        await session.execute(
            select(StarPayment).where(StarPayment.invoice_payload == invoice_payload)
        )
    ).scalar_one_or_none()
    if payment is None:
        return False, "Unknown invoice"
    if payment.status != StarPaymentStatus.PENDING.value:
        return False, "Invoice is no longer valid"
    if int(payment.stars) != int(total_amount):
        return False, "Amount mismatch"
    return True, None
