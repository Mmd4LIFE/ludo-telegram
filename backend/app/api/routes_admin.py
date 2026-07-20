"""Admin-only views: who's playing and how the box is doing.

Gated by ``require_admin`` (ADMIN_IDS in the environment). Note the inherited privacy
rule still holds here: ``telegram_id`` never leaves the server, not even for an admin.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, Query

from app.api.deps import require_admin
from app.database import get_session
from app.models import Match, MatchStatus, User
from app.schemas import AdminStats, AdminUser

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats", response_model=AdminStats)
async def stats(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    async def count(stmt) -> int:
        return int((await session.execute(stmt)).scalar_one() or 0)

    users = await count(select(func.count()).select_from(User).where(User.is_bot.is_(False)))
    started = await count(
        select(func.count()).select_from(User).where(
            User.is_bot.is_(False), User.bot_started.is_(True)
        )
    )
    played = await count(
        select(func.coalesce(func.sum(User.games_played), 0)).where(User.is_bot.is_(False))
    )
    coins = await count(
        select(func.coalesce(func.sum(User.coins), 0)).where(User.is_bot.is_(False))
    )
    by_status: dict[str, int] = {}
    rows = (
        await session.execute(select(Match.status, func.count()).group_by(Match.status))
    ).all()
    for st, n in rows:
        by_status[st.value if hasattr(st, "value") else str(st)] = int(n)

    return AdminStats(
        users=users,
        users_started=started,
        games_played=played,
        coins_in_circulation=coins,
        matches_playing=by_status.get(MatchStatus.PLAYING.value, 0),
        matches_waiting=by_status.get(MatchStatus.WAITING.value, 0),
        matches_finished=by_status.get(MatchStatus.FINISHED.value, 0),
        matches_abandoned=by_status.get(MatchStatus.ABANDONED.value, 0),
    )


@router.get("/users", response_model=list[AdminUser])
async def list_users(
    q: str | None = Query(default=None, description="name/username search"),
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(User).where(User.is_bot.is_(False))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(User.first_name.ilike(like) | User.username.ilike(like))
    stmt = stmt.order_by(User.last_seen_at.desc().nullslast(), User.id.desc())
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [AdminUser.from_user(u) for u in rows]
