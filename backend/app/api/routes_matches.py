"""Match lifecycle: create, join, list, inspect.

Kept deliberately lean for the base. The live game itself runs over the websocket
(``routes_ws``); these REST endpoints are just the lobby. A follow-up session moves the
heavier bits (entry-fee escrow, matchmaking, rematch) into ``app/services/matches.py``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_session
from app.ludo.board import Color
from app.models import Match, MatchSeat, MatchStatus, User
from app.schemas import CreateMatchRequest, JoinMatchRequest, MatchSummary

router = APIRouter(prefix="/api/matches", tags=["matches"])

_COLORS = [Color.RED, Color.GREEN, Color.YELLOW, Color.BLUE]


def _summary(m: Match) -> MatchSummary:
    return MatchSummary(
        code=m.code,
        status=m.status.value,
        max_players=m.max_players,
        seated=sum(1 for s in m.seats if s.user_id is not None or s.is_bot),
        is_public=m.is_public,
        entry_fee=m.entry_fee,
    )


@router.post("", response_model=MatchSummary)
async def create_match(
    body: CreateMatchRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not 2 <= body.max_players <= 4:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "max_players must be 2..4")
    if body.entry_fee and user.coins < body.entry_fee:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not enough coins for the entry fee")

    match = Match(
        max_players=body.max_players,
        is_public=body.is_public,
        entry_fee=body.entry_fee,
        created_by=user.id,
        status=MatchStatus.WAITING,
    )
    session.add(match)
    await session.flush()

    # seat the creator on RED (seat 0)
    session.add(MatchSeat(
        match_id=match.id, seat_index=0, color=Color.RED.name,
        user_id=user.id, is_bot=False, connected=False,
    ))

    if body.fill_with_bots:
        for i in range(1, body.max_players):
            session.add(MatchSeat(
                match_id=match.id, seat_index=i, color=_COLORS[i].name,
                is_bot=True, connected=True,
            ))
        match.status = MatchStatus.PLAYING

    await session.flush()
    await session.refresh(match, attribute_names=["seats"])
    return _summary(match)


@router.post("/join", response_model=MatchSummary)
async def join_match(
    body: JoinMatchRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    match = (
        await session.execute(
            select(Match).where(Match.code == body.code.upper())
        )
    ).scalar_one_or_none()
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match not found")
    if match.status not in (MatchStatus.WAITING, MatchStatus.PLAYING):
        raise HTTPException(status.HTTP_409_CONFLICT, "Match is not open")

    # already seated?
    for s in match.seats:
        if s.user_id == user.id:
            return _summary(match)

    taken = {s.seat_index for s in match.seats}
    free = next((i for i in range(match.max_players) if i not in taken), None)
    if free is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Match is full")

    session.add(MatchSeat(
        match_id=match.id, seat_index=free, color=_COLORS[free].name,
        user_id=user.id, is_bot=False, connected=False,
    ))
    await session.flush()
    await session.refresh(match, attribute_names=["seats"])
    return _summary(match)


@router.get("", response_model=list[MatchSummary])
async def list_open_matches(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Match)
            .where(Match.is_public.is_(True), Match.status == MatchStatus.WAITING)
            .order_by(Match.created_at.desc())
            .limit(30)
        )
    ).scalars().all()
    return [_summary(m) for m in rows]


@router.get("/{code}", response_model=MatchSummary)
async def get_match(
    code: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    match = (
        await session.execute(select(Match).where(Match.code == code.upper()))
    ).scalar_one_or_none()
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match not found")
    return _summary(match)
