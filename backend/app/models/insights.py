"""Observability tables for the admin Insights Assistant.

``InsightsQuery`` is the audit log (one row per admin question); ``InsightsStep`` records
each pipeline stage so the flow of every question can be monitored end to end. Both are
append-only.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InsightsQuery(Base):
    __tablename__ = "insights_queries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="error")   # answered|clarified|refused|error
    answer: Mapped[str] = mapped_column(String(2000), default="")
    metric: Mapped[str | None] = mapped_column(String(64), default=None)
    capability: Mapped[str | None] = mapped_column(String(64), default=None)
    intent: Mapped[dict | None] = mapped_column(JSONB, default=None)
    params: Mapped[dict | None] = mapped_column(JSONB, default=None)
    result: Mapped[dict | None] = mapped_column(JSONB, default=None)
    error: Mapped[str | None] = mapped_column(String(500), default=None)
    model: Mapped[str] = mapped_column(String(48), default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InsightsStep(Base):
    __tablename__ = "insights_steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    query_id: Mapped[int] = mapped_column(
        ForeignKey("insights_queries.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(32))    # extract|resolve_time|resolve_entity|route|execute|compose
    status: Mapped[str] = mapped_column(String(16), default="ok")   # ok|skip|error|clarify
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
