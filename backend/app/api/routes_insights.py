"""Admin Insights Assistant API (admin-gated, read-only).

    POST /api/admin/insights/ask            ask a question
    GET  /api/admin/insights/capabilities   what can be asked
    GET  /api/admin/insights/log            recent questions (audit)
    GET  /api/admin/insights/queries/{id}   one question + its pipeline steps (flow)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.api.deps import require_admin
from app.database import get_session
from app.insights import capabilities as caps
from app.insights.orchestrator import answer as run_answer
from app.models import InsightsQuery, InsightsStep, User

router = APIRouter(prefix="/api/admin/insights", tags=["insights"])


class AskRequest(BaseModel):
    question: str


@router.post("/ask")
async def ask(
    body: AskRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(400, "Ask a question")
    return await run_answer(session, admin.id, q)


@router.get("/capabilities")
async def capabilities(_admin: User = Depends(require_admin)):
    return caps.catalog()


@router.get("/log")
async def log(
    limit: int = Query(30, ge=1, le=100),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(InsightsQuery).order_by(InsightsQuery.id.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id, "question": r.question, "status": r.status, "metric": r.metric,
            "capability": r.capability, "answer": r.answer, "latency_ms": r.latency_ms,
            "model": r.model, "created_at": r.created_at.isoformat() if r.created_at else None,
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
    return {
        "query": {
            "id": q.id, "question": q.question, "status": q.status, "answer": q.answer,
            "metric": q.metric, "capability": q.capability, "intent": q.intent,
            "params": q.params, "result": q.result, "error": q.error, "model": q.model,
            "latency_ms": q.latency_ms,
            "created_at": q.created_at.isoformat() if q.created_at else None,
        },
        "steps": [
            {"seq": s.seq, "stage": s.stage, "status": s.status,
             "duration_ms": s.duration_ms, "detail": s.detail}
            for s in steps
        ],
    }
