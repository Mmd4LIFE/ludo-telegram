"""Admin native SQL runner (read-only).

    POST /api/admin/sql/run       run one read-only statement
    GET  /api/admin/sql/history   recent runs (audit)
    GET  /api/admin/sql/schema    tables + visible columns for the editor sidebar

Runs on the dedicated read-only role (see ``app.insights.readonly``); every run is audited.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.config import settings
from app.database import get_session
from app.insights import knowledge, readonly
from app.insights.readonly import SqlError, SqlRejected
from app.models import InsightsSqlRun, User

router = APIRouter(prefix="/api/admin/sql", tags=["sql"])


class RunRequest(BaseModel):
    sql: str = Field(..., max_length=20000)
    max_rows: int = Field(500, ge=1, le=5000)


@router.post("/run")
async def run_sql(
    body: RunRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    audit = InsightsSqlRun(admin_id=admin.id, sql=body.sql[:20000], status="ok")
    out: dict
    try:
        res = await readonly.run(body.sql, max_rows=body.max_rows)
        audit.row_count, audit.duration_ms = res.row_count, res.duration_ms
        out = {"ok": True, **res.as_dict(), "error": None}
    except SqlRejected as e:
        audit.status, audit.error = "rejected", str(e)[:500]
        out = {"ok": False, "error": str(e)}
    except SqlError as e:
        audit.status, audit.error = "error", str(e)[:500]
        out = {"ok": False, "error": str(e)}
    session.add(audit)
    await session.commit()
    return {**out, "run_id": audit.id, "max_rows": min(body.max_rows, settings.INSIGHTS_MAX_ROWS)}


@router.get("/history")
async def history(
    limit: int = Query(30, ge=1, le=100),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(select(InsightsSqlRun).order_by(InsightsSqlRun.id.desc()).limit(limit))
    ).scalars().all()
    return [
        {
            "id": r.id, "sql": r.sql, "status": r.status, "row_count": r.row_count,
            "duration_ms": r.duration_ms, "error": r.error, "mine": r.admin_id == admin.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/schema")
async def schema(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    counts = await knowledge.row_counts(session)
    tables = []
    for t in knowledge.tables():
        info = knowledge.table_info(t.name)
        if info:
            tables.append({**info, "rows": counts.get(t.name)})
    return {"tables": tables, "readonly": readonly.role_status(), "max_rows": settings.INSIGHTS_MAX_ROWS}
