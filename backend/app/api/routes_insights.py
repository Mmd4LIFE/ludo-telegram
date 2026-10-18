"""Admin Insights assistant API (admin-gated, read-only over the game data).

    POST   /api/admin/insights/ask                     ask a question (optionally with history)
    GET    /api/admin/insights/status                  OpenAI / read-only role / knowledge health
    GET    /api/admin/insights/log                     recent questions (audit)
    GET    /api/admin/insights/queries/{id}            one question, its answer + agent steps
    POST   /api/admin/insights/queries/{id}/feedback   👍 saves it as a learned example, 👎 drops it
    GET    /api/admin/insights/knowledge               knowledge store entries
    POST   /api/admin/insights/knowledge               teach a note (definition / rule)
    PATCH  /api/admin/insights/knowledge/{id}          enable / disable / edit
    DELETE /api/admin/insights/knowledge/{id}          delete a note or learned example
    POST   /api/admin/insights/reindex                 rebuild schema chunks + embeddings
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.config import settings
from app.database import get_session
from app.insights import agent, knowledge, llm, readonly
from app.models import InsightsKnowledge, InsightsQuery, InsightsStep, User

router = APIRouter(prefix="/api/admin/insights", tags=["insights"])


class HistoryTurn(BaseModel):
    question: str = Field("", max_length=500)
    answer: str = Field("", max_length=4000)
    sql: str | None = Field(None, max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(..., max_length=500)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=6)


class FeedbackRequest(BaseModel):
    value: int = Field(..., ge=-1, le=1)


class NoteRequest(BaseModel):
    title: str = Field("", max_length=200)
    content: str = Field(..., min_length=3, max_length=4000)


class KnowledgePatch(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(None, max_length=200)
    content: str | None = Field(None, min_length=3, max_length=4000)


@router.post("/ask")
async def ask(
    body: AskRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(400, "Ask a question")
    return await agent.ask(session, admin.id, q, [h.model_dump() for h in body.history])


@router.get("/status")
async def status(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(
                InsightsKnowledge.kind,
                func.count(),
                func.count().filter(InsightsKnowledge.embedding.is_not(None)),
            )
            .where(InsightsKnowledge.enabled.is_(True))
            .group_by(InsightsKnowledge.kind)
        )
    ).all()
    return {
        "openai": llm.available(),
        "model": llm.chat_model(),
        "embed_model": settings.OPENAI_EMBED_MODEL,
        "tz_offset": knowledge.tz_offset(),
        "readonly": readonly.role_status(),
        "knowledge": {k: {"count": n, "embedded": e} for k, n, e in rows},
    }


def _query_out(q: InsightsQuery, steps: list[InsightsStep] | None = None) -> dict:
    r = q.result or {}
    table = None
    if r.get("columns"):
        table = {
            "columns": r.get("columns", []), "rows": r.get("rows", []),
            "row_count": r.get("row_count", 0), "truncated": r.get("truncated", False), "duration_ms": 0,
        }
    return {
        "query_id": q.id, "status": q.status, "question": q.question, "answer": q.answer,
        "sql": q.sql, "table": table, "chart": r.get("chart"), "follow_ups": r.get("follow_ups", []),
        "sources": r.get("sources", []), "usage": r.get("usage", {}), "error": q.error,
        "model": q.model, "latency_ms": q.latency_ms, "feedback": q.feedback,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "steps": [
            {"seq": s.seq, "stage": s.stage, "status": s.status, "duration_ms": s.duration_ms, "detail": s.detail}
            for s in (steps or [])
        ],
    }


@router.get("/log")
async def log(
    limit: int = Query(30, ge=1, le=100),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(select(InsightsQuery).order_by(InsightsQuery.id.desc()).limit(limit))
    ).scalars().all()
    return [
        {
            "id": r.id, "question": r.question, "status": r.status, "answer": (r.answer or "")[:300],
            "has_sql": bool(r.sql), "feedback": r.feedback, "latency_ms": r.latency_ms, "model": r.model,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/queries/{query_id}")
async def query_detail(
    query_id: int,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = await session.get(InsightsQuery, query_id)
    if q is None:
        raise HTTPException(404, "Not found")
    steps = (
        await session.execute(
            select(InsightsStep).where(InsightsStep.query_id == query_id).order_by(InsightsStep.seq)
        )
    ).scalars().all()
    return _query_out(q, list(steps))


@router.post("/queries/{query_id}/feedback")
async def feedback(
    query_id: int,
    body: FeedbackRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = await session.get(InsightsQuery, query_id)
    if q is None:
        raise HTTPException(404, "Not found")
    q.feedback = body.value or None
    await session.commit()
    key = f"learned:{q.id}"
    if body.value == 1 and q.sql:
        await knowledge.add_entry(
            session, kind="example", key=key, title=q.question,
            content=f"Q: {q.question}\nSQL:\n{q.sql}", source="learned", admin_id=admin.id,
        )
    else:
        row = (await session.execute(select(InsightsKnowledge).where(InsightsKnowledge.key == key))).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            await session.commit()
            knowledge.invalidate()
    return {"ok": True, "feedback": q.feedback}


def _knowledge_out(r: InsightsKnowledge) -> dict:
    return {
        "id": r.id, "key": r.key, "kind": r.kind, "source": r.source, "title": r.title,
        "content": r.content, "enabled": r.enabled, "embedded": r.embedding is not None,
        "editable": r.source in ("admin", "learned"),
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("/knowledge")
async def list_knowledge(
    kind: str | None = Query(None, max_length=16),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(InsightsKnowledge).order_by(InsightsKnowledge.kind, InsightsKnowledge.key)
    if kind:
        stmt = stmt.where(InsightsKnowledge.kind == kind)
    return [_knowledge_out(r) for r in (await session.execute(stmt)).scalars().all()]


@router.post("/knowledge")
async def add_note(
    body: NoteRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await knowledge.add_entry(
        session, kind="note", key=f"note:{secrets.token_hex(6)}", title=body.title.strip(),
        content=body.content.strip(), source="admin", admin_id=admin.id,
    )
    return _knowledge_out(row)


@router.patch("/knowledge/{entry_id}")
async def patch_knowledge(
    entry_id: int,
    body: KnowledgePatch,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(InsightsKnowledge, entry_id)
    if row is None:
        raise HTTPException(404, "Not found")
    if (body.title is not None or body.content is not None) and row.source not in ("admin", "learned"):
        raise HTTPException(400, "Built-in knowledge can only be enabled or disabled")
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.title is not None:
        row.title = body.title.strip()
    if body.content is not None:
        row.content = body.content.strip()
    if body.title is not None or body.content is not None:
        row.embedding, row.embed_model = None, ""
    await session.commit()
    await knowledge.embed_pending(session)
    knowledge.invalidate()
    await session.refresh(row)
    return _knowledge_out(row)


@router.delete("/knowledge/{entry_id}")
async def delete_knowledge(
    entry_id: int,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(InsightsKnowledge, entry_id)
    if row is None:
        raise HTTPException(404, "Not found")
    if row.source not in ("admin", "learned"):
        raise HTTPException(400, "Built-in knowledge can't be deleted — disable it instead")
    await session.delete(row)
    await session.commit()
    knowledge.invalidate()
    return {"ok": True}


@router.post("/reindex")
async def reindex(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await readonly.ensure_role()
    stats = await knowledge.sync(session)
    return {**stats, "readonly": readonly.role_status()}
