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
from app.schemas import (
    AcceptJoinerRequest,
    ChatMessage,
    CreateMatchRequest,
    JoinMatchRequest,
    MatchSummary,
    PendingJoiner,
    RejectJoinerRequest,
    SeatInfo,
    SendChatRequest,
)

router = APIRouter(prefix="/api/matches", tags=["matches"])

_COLORS = [Color.RED, Color.GREEN, Color.YELLOW, Color.BLUE]

# Ephemeral per-room lobby chat (in memory — a waiting room is short-lived, so this
# deliberately avoids a table/migration). Capped so a room can't grow unbounded.
_CHAT: dict[str, list[dict]] = {}
_CHAT_MAX = 60

# Joiners awaiting the host's approval: room code -> [{user_id, name}]
_PENDING: dict[str, list[dict]] = {}


def _summary(m: Match, seats: list[SeatInfo] | None = None) -> MatchSummary:
    return MatchSummary(
        code=m.code,
        status=m.status.value,
        max_players=m.max_players,
        seated=sum(1 for s in m.seats if s.user_id is not None or s.is_bot),
        is_public=m.is_public,
        entry_fee=m.entry_fee,
        created_by=m.created_by,
        seats=seats or [],
        pending=[PendingJoiner(**p) for p in _PENDING.get(m.code, [])],
    )


async def _seat_infos(session, m: Match) -> list[SeatInfo]:
    """Per-seat display info (with joiner names) for a single match's waiting room."""
    ids = [s.user_id for s in m.seats if s.user_id is not None]
    names: dict[int, str] = {}
    if ids:
        rows = (
            await session.execute(select(User.id, User.first_name).where(User.id.in_(ids)))
        ).all()
        names = {rid: (fn or "Player") for rid, fn in rows}
    out: list[SeatInfo] = []
    for s in sorted(m.seats, key=lambda x: x.seat_index):
        if s.user_id is not None:
            name = names.get(s.user_id, "Player")
        elif s.is_bot:
            name = "Bot"
        else:
            name = "Open"
        out.append(SeatInfo(
            seat_index=s.seat_index, color=s.color, name=name,
            is_bot=s.is_bot, user_id=s.user_id,
        ))
    return out


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
            return _summary(match, await _seat_infos(session, match))

    # An open room requires the host's approval: queue the request instead of seating.
    if match.status is MatchStatus.WAITING and match.created_by != user.id:
        pend = _PENDING.setdefault(match.code, [])
        if not any(p["user_id"] == user.id for p in pend):
            pend.append({"user_id": user.id, "name": user.first_name or "Player"})
        return _summary(match, await _seat_infos(session, match))

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
    return _summary(match, await _seat_infos(session, match))


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
    return _summary(match, await _seat_infos(session, match))


async def _load_room(session: AsyncSession, code: str) -> Match:
    match = (
        await session.execute(select(Match).where(Match.code == code.upper()))
    ).scalar_one_or_none()
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match not found")
    return match


@router.post("/{code}/accept", response_model=MatchSummary)
async def accept_joiner(
    code: str,
    body: AcceptJoinerRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Host-only: admit a pending joiner and hand them a colour."""
    match = await _load_room(session, code)
    if match.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the host can accept players")
    if match.status is not MatchStatus.WAITING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Room is no longer open")

    colour = body.color.upper()
    if colour not in {c.name for c in _COLORS}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown colour")
    if any(s.color == colour for s in match.seats):
        raise HTTPException(status.HTTP_409_CONFLICT, "That colour is taken")
    if any(s.user_id == body.user_id for s in match.seats):
        raise HTTPException(status.HTTP_409_CONFLICT, "Player is already seated")

    taken = {s.seat_index for s in match.seats}
    free = next((i for i in range(match.max_players) if i not in taken), None)
    if free is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Room is full")

    session.add(MatchSeat(
        match_id=match.id, seat_index=free, color=colour,
        user_id=body.user_id, is_bot=False, connected=False,
    ))
    pend = _PENDING.get(match.code, [])
    _PENDING[match.code] = [p for p in pend if p["user_id"] != body.user_id]
    await session.flush()
    await session.refresh(match, attribute_names=["seats"])
    return _summary(match, await _seat_infos(session, match))


@router.post("/{code}/reject", response_model=MatchSummary)
async def reject_joiner(
    code: str,
    body: RejectJoinerRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Host-only: decline a pending joiner."""
    match = await _load_room(session, code)
    if match.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the host can manage players")
    pend = _PENDING.get(match.code, [])
    _PENDING[match.code] = [p for p in pend if p["user_id"] != body.user_id]
    return _summary(match, await _seat_infos(session, match))


@router.post("/{code}/start", response_model=MatchSummary)
async def start_match(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Host-only: begin the game. Needs at least two seated players."""
    match = await _load_room(session, code)
    if match.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the host can start the room")
    if match.status is MatchStatus.PLAYING:
        return _summary(match, await _seat_infos(session, match))
    if match.status is not MatchStatus.WAITING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Room is no longer open")
    seated = sum(1 for s in match.seats if s.user_id is not None or s.is_bot)
    if seated < 2:
        raise HTTPException(status.HTTP_409_CONFLICT, "Need at least 2 players to start")
    match.status = MatchStatus.PLAYING
    _PENDING.pop(match.code, None)   # anyone still queued missed the boat
    await session.flush()
    await session.refresh(match, attribute_names=["seats"])
    return _summary(match, await _seat_infos(session, match))


@router.delete("/{code}")
async def delete_match(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Host-only: close a room that hasn't started."""
    match = await _load_room(session, code)
    if match.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the host can delete the room")
    if match.status is MatchStatus.PLAYING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Game already started")
    match.status = MatchStatus.ABANDONED
    _CHAT.pop(match.code, None)
    _PENDING.pop(match.code, None)
    return {"ok": True}


@router.get("/{code}/chat", response_model=list[ChatMessage])
async def get_chat(
    code: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    match = await _load_room(session, code)
    return [ChatMessage(**m) for m in _CHAT.get(match.code, [])]


@router.post("/{code}/chat", response_model=list[ChatMessage])
async def send_chat(
    code: str,
    body: SendChatRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    match = await _load_room(session, code)
    text = body.text.strip()[:200]
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty message")
    in_room = any(s.user_id == user.id for s in match.seats) or any(
        p["user_id"] == user.id for p in _PENDING.get(match.code, [])
    )
    if not in_room:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the room to chat")
    msgs = _CHAT.setdefault(match.code, [])
    msgs.append({
        "id": (msgs[-1]["id"] + 1) if msgs else 1,
        "user_id": user.id,
        "name": user.first_name or "Player",
        "text": text,
    })
    del msgs[:-_CHAT_MAX]
    return [ChatMessage(**m) for m in msgs]
