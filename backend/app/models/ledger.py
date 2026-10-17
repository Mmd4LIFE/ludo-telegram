"""Coin ledger: balances (source of truth) + append-only balance_history."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Balance(Base):
    """Current coin balance per user. Mirrored onto ``users.coins`` for API compat."""

    __tablename__ = "balances"
    __table_args__ = (
        UniqueConstraint("user_id", "currency", name="uq_balance_user_currency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(16), default="coins", server_default="coins")
    amount: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BalanceHistory(Base):
    """Append-only ledger of every credit/debit."""

    __tablename__ = "balance_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    currency: Mapped[str] = mapped_column(String(16), default="coins", server_default="coins")
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)  # signed
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(32), default=None)
    ref_id: Mapped[int | None] = mapped_column(Integer, default=None)
    meta: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
