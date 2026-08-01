"""Public player stats: the scoreboard and per-player profile cards.

Both are visible to any authenticated player (you tap someone's name in a game to see
their card). Neither ever exposes ``telegram_id`` — the response is the ``PlayerStats``
schema, which has no such field.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_session
from app.models import User
from app.schemas import PlayerStats

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/scoreboard", response_model=list[PlayerStats])
async def scoreboard(
    limit: int = Query(50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Players ranked by their average dice output (highest first).

    Only real players who have actually rolled appear. Ties break on who has rolled more
    (a bigger, more trustworthy sample), then on games won.
    """
    rows = (
        await session.execute(select(User).where(User.is_bot.is_(False)))
    ).scalars().all()
    stats = [PlayerStats.from_user(u) for u in rows]
    stats = [s for s in stats if s.dice_rolls > 0]
    stats.sort(key=lambda s: (s.dice_avg, s.dice_rolls, s.games_won), reverse=True)
    return stats[:limit]


@router.get("/users/{user_id}/profile", response_model=PlayerStats)
async def user_profile(
    user_id: int,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """One player's public card — tapped from their name on the board."""
    u = await session.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Player not found")
    return PlayerStats.from_user(u)
