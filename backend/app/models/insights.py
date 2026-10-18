"""Tables behind the admin Insights assistant and the native SQL tab.

``InsightsQuery`` is the audit log (one row per admin question); ``InsightsStep`` records
each agent step (knowledge lookups, SQL runs, the final answer) so every question can be
traced end to end. ``InsightsKnowledge`` is the RAG store: schema/business docs, worked
examples and admin-written notes, each with an optional embedding. ``InsightsSqlRun``
audits every read-only query run from the native SQL tab.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InsightsQuery(Base):
    __tablename__ = "insights_queries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="error")   # answered|error
    answer: Mapped[str] = mapped_column(Text, default="")
    sql: Mapped[str | None] = mapped_column(Text, default=None)        # query backing the answer
    feedback: Mapped[int | None] = mapped_column(SmallInteger, default=None)  # 1 good, -1 bad
    # legacy capability-router columns (kept for old rows)
    metric: Mapped[str | None] = mapped_column(String(64), default=None)
    capability: Mapped[str | None] = mapped_column(String(64), default=None)
    intent: Mapped[dict | None] = mapped_column(JSONB, default=None)
    params: Mapped[dict | None] = mapped_column(JSONB, default=None)
    # {"columns", "rows" (preview), "row_count", "truncated", "follow_ups", "chart", "usage"}
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
    stage: Mapped[str] = mapped_column(String(32))    # retrieve|search_knowledge|describe_table|run_sql|answer
    status: Mapped[str] = mapped_column(String(16), default="ok")   # ok|error
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InsightsKnowledge(Base):
    __tablename__ = "insights_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))      # table|doc|example|note
    source: Mapped[str] = mapped_column(String(16))    # auto|curated|learned|admin
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding: Mapped[list | None] = mapped_column(JSONB, default=None)
    embed_model: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class InsightsSqlRun(Base):
    __tablename__ = "insights_sql_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sql: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))   # ok|error|rejected
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
