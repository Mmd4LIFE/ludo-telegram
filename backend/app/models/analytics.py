"""Queue of score events waiting to be copied to ClickHouse.

Rows are inserted in the same transaction as the dice roll or knockout, then
deleted only after ClickHouse accepts the batch. Re-delivery is safe: ClickHouse
deduplicates on the source id.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AnalyticsOutbox(Base):
    __tablename__ = "analytics_outbox"
    __table_args__ = (
        UniqueConstraint("kind", "source_id", name="uq_analytics_outbox_source"),
        Index("ix_analytics_outbox_due", "next_attempt_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # roll | knock
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AnalyticsCursor(Base):
    """How far historical backfill has read in each source table."""

    __tablename__ = "analytics_cursors"

    stream: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
