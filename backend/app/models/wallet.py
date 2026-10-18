"""Wallet transfers (direct + claimable gifts) and Telegram Stars purchases."""
from __future__ import annotations

import enum
import secrets
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TransferStatus(str, enum.Enum):
    PENDING = "pending"       # gift link created; coins escrowed from sender
    COMPLETED = "completed"   # direct wallet-to-wallet done
    CLAIMED = "claimed"       # gift claimed by recipient
    CANCELLED = "cancelled"   # sender cancelled; refunded
    EXPIRED = "expired"       # unclaimed after TTL; refunded


class StarPaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


def _new_claim_token() -> str:
    return secrets.token_urlsafe(12)[:16]


def _new_invoice_payload() -> str:
    return secrets.token_urlsafe(18)[:24]


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    to_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=TransferStatus.PENDING.value, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, default=None)
    note: Mapped[str | None] = mapped_column(String(120), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class StarPayment(Base):
    __tablename__ = "star_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    coins: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pack_stars: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default=StarPaymentStatus.PENDING.value, index=True
    )
    invoice_payload: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=_new_invoice_payload
    )
    telegram_payment_charge_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
