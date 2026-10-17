"""Public player stats: the scoreboard and per-player profile cards.

Both are visible to any authenticated player (you tap someone's name in a game to see
their card). Neither ever exposes ``telegram_id`` — the response is the ``PlayerStats``
schema, which has no such field.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_session
from app.models import User, ReactionEmoji, Card, PollTemplate
from app.schemas import PlayerStats, CardOut, PollTemplateOut


def _hist_face(face: int):
    """JSONB dice_hist face count as an int expression (0 if missing)."""
    return func.coalesce(
        cast(User.dice_hist.op("->>")(str(face)), Integer),
        0,
    )

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/poll-templates", response_model=list[PollTemplateOut])
async def poll_templates(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Instant-poll templates a player may post in chat (enabled only), ordered."""
    rows = (
        await session.execute(
            select(PollTemplate).where(PollTemplate.enabled.is_(True))
            .order_by(PollTemplate.position, PollTemplate.id)
        )
    ).scalars().all()
    return [
        PollTemplateOut(id=t.id, question=t.question, options=list(t.options or []),
                        trigger=t.trigger, enabled=t.enabled)
        for t in rows
    ]


@router.get("/cards", response_model=list[CardOut])
async def cards(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """The fantasy-card catalog (enabled cards), ordered for display."""
    rows = (
        await session.execute(
            select(Card).where(Card.enabled.is_(True)).order_by(Card.position, Card.id)
        )
    ).scalars().all()
    return [CardOut.from_row(c) for c in rows]


@router.get("/reactions", response_model=list[str])
async def reactions(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """The current set of chat reaction emojis (admin-managed), ordered for display."""
    rows = (
        await session.execute(
            select(ReactionEmoji.emoji).order_by(ReactionEmoji.position, ReactionEmoji.id)
        )
    ).scalars().all()
    return list(rows)


@router.get("/scoreboard", response_model=list[PlayerStats])
async def scoreboard(
    limit: int = Query(50, ge=1, le=200),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Players ranked by their average dice output (highest first).

    Only real players who have actually rolled appear. Ties break on who has rolled more
    (a bigger, more trustworthy sample), then on games won. Ranking is done in SQL so we
    never load the full users table.
    """
    rolls = (
        _hist_face(1) + _hist_face(2) + _hist_face(3)
        + _hist_face(4) + _hist_face(5) + _hist_face(6)
    )
    weighted = (
        1 * _hist_face(1) + 2 * _hist_face(2) + 3 * _hist_face(3)
        + 4 * _hist_face(4) + 5 * _hist_face(5) + 6 * _hist_face(6)
    )
    dice_avg = cast(weighted, Float) / cast(func.nullif(rolls, 0), Float)

    rows = (
        await session.execute(
            select(User)
            .where(User.is_bot.is_(False), rolls > 0)
            .order_by(dice_avg.desc(), rolls.desc(), User.games_won.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [PlayerStats.from_user(u) for u in rows]


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
