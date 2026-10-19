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
from app.config import settings
from app.database import get_session
from app.ludo.board import Color
from app.ludo.rules import initial_state
from app.models import (
    Knockout,
    Match,
    MatchJoinRequest,
    MatchSeat,
    MatchStatus,
    MessageReaction,
    ReactionEmoji,
    User,
)
from app.services import ledger
from app.services.ledger import InsufficientFunds
from app.models import ChatMessage as ChatRow
from app.models import PollTemplate, Poll, PollOption, PollVote
from app.game.connection import hub
from app.game.manager import LateJoinError, manager
from datetime import datetime, timezone
import math
import random
import time

from app.schemas import (
    AcceptJoinerRequest,
    ChatMessage,
    CreatePollRequest,
    DiceEntry,
    DiceState,
    CreateMatchRequest,
    JoinMatchRequest,
    KnockEvent,
    MatchLobby,
    MatchSummary,
    PendingJoiner,
    PollOptionOut,
    PollOut,
    ReactRequest,
    RejectJoinerRequest,
    SeatInfo,
    SetColorRequest,
    SendChatRequest,
    VoteRequest,
)

router = APIRouter(prefix="/api/matches", tags=["matches"])

# Seat fill order matches board turn order (blue first, then clockwise).
_COLORS = [Color.BLUE, Color.RED, Color.GREEN, Color.YELLOW]


async def _allowed_reactions(session: AsyncSession) -> list[str]:
    """The admin-managed set of reaction emojis (ordered)."""
    rows = (
        await session.execute(
            select(ReactionEmoji.emoji).order_by(ReactionEmoji.position, ReactionEmoji.id)
        )
    ).scalars().all()
    return list(rows)

# How many recent chat messages a room returns (chat is persisted in chat_messages).
_CHAT_MAX = 60

# Fun waiting-room dice: room code -> {user_id: stats}. Rolled server-side so the
# ranking can't be faked, rate-limited so it can't be spammed.
_DICE: dict[str, dict[int, dict]] = {}
DICE_COOLDOWN = 3.0


def _dice_state(code: str) -> DiceState:
    room = _DICE.get(code, {})
    entries = [
        DiceEntry(
            user_id=s["user_id"], name=s["name"], rolls=s["rolls"], total=s["total"],
            avg=round(s["total"] / s["rolls"], 2) if s["rolls"] else 0.0,
            best=s["best"], last_value=s["last_value"],
        )
        for s in room.values()
    ]
    # luckiest first: best average, then most rolls
    entries.sort(key=lambda e: (e.avg, e.rolls), reverse=True)
    return DiceState(cooldown=DICE_COOLDOWN, ranking=entries)


def _has_finisher(m: Match) -> bool:
    state = m.state or {}
    if state.get("ranking"):
        return True
    return any(p.get("finished_at") is not None for p in state.get("players", []))


def _available_colors(m: Match) -> list[str]:
    taken = {s.color for s in m.seats}
    return [c.name for c in _COLORS if c.name not in taken]


def _joinable(m: Match) -> bool:
    seated = sum(1 for s in m.seats if s.user_id is not None or s.is_bot)
    return (
        not m.is_bot_table
        and m.status in (MatchStatus.WAITING, MatchStatus.PLAYING)
        and seated < m.max_players
        and (m.status is MatchStatus.WAITING or not _has_finisher(m))
    )


def _summary(
    m: Match,
    seats: list[SeatInfo] | None = None,
    *,
    pending: list[PendingJoiner] | None = None,
    user_id: int | None = None,
    request_status: str | None = None,
    host_name: str = "Player",
) -> MatchSummary:
    return MatchSummary(
        code=m.code,
        status=m.status.value,
        max_players=m.max_players,
        seated=sum(1 for s in m.seats if s.user_id is not None or s.is_bot),
        is_public=m.is_public,
        entry_fee=m.entry_fee,
        created_by=m.created_by,
        created_at=m.created_at.isoformat() if m.created_at else None,
        seats=seats or [],
        pending=pending or [],
        host_name=host_name,
        i_am_seated=bool(
            user_id is not None and any(s.user_id == user_id for s in m.seats)
        ),
        request_status=request_status,
        joinable=_joinable(m),
        available_colors=_available_colors(m),
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


async def _room_summary(
    session: AsyncSession, m: Match, user_id: int | None = None
) -> MatchSummary:
    host_name = "Player"
    if m.created_by:
        host_name = (
            await session.execute(
                select(User.first_name).where(User.id == m.created_by)
            )
        ).scalar_one_or_none() or "Player"
    rows = (
        await session.execute(
            select(MatchJoinRequest, User.first_name)
            .join(User, User.id == MatchJoinRequest.user_id)
            .where(
                MatchJoinRequest.match_id == m.id,
                MatchJoinRequest.status == "pending",
            )
            .order_by(MatchJoinRequest.requested_at)
        )
    ).all()
    pending = [
        PendingJoiner(
            user_id=req.user_id,
            name=name or "Player",
            requested_at=req.requested_at.isoformat() if req.requested_at else None,
        )
        for req, name in rows
    ]
    if user_id != m.created_by:
        pending = []
    request_status = None
    if user_id is not None:
        request_status = (
            await session.execute(
                select(MatchJoinRequest.status).where(
                    MatchJoinRequest.match_id == m.id,
                    MatchJoinRequest.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
    return _summary(
        m,
        await _seat_infos(session, m),
        pending=pending,
        user_id=user_id,
        request_status=request_status,
        host_name=host_name,
    )


async def _is_room_member(
    session: AsyncSession, match: Match, user_id: int
) -> bool:
    if any(s.user_id == user_id for s in match.seats):
        return True
    return bool((
        await session.execute(
            select(MatchJoinRequest.id).where(
                MatchJoinRequest.match_id == match.id,
                MatchJoinRequest.user_id == user_id,
                MatchJoinRequest.status == "pending",
            )
        )
    ).scalar_one_or_none())


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

    active = (
        await session.execute(
            select(func.count())
            .select_from(Match)
            .where(
                Match.created_by == user.id,
                Match.status.in_((MatchStatus.WAITING, MatchStatus.PLAYING)),
            )
        )
    ).scalar_one()
    if active >= settings.MAX_ACTIVE_ROOMS_PER_USER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"You already have {active} active rooms "
            f"(max {settings.MAX_ACTIVE_ROOMS_PER_USER})",
        )

    match = Match(
        max_players=body.max_players,
        is_public=body.is_public,
        entry_fee=body.entry_fee,
        created_by=user.id,
        status=MatchStatus.WAITING,
    )
    session.add(match)
    await session.flush()

    if body.entry_fee:
        try:
            await ledger.debit(
                session,
                user.id,
                body.entry_fee,
                reason="entry_fee",
                ref_type="match",
                ref_id=match.id,
            )
        except InsufficientFunds as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not enough coins for the entry fee") from e

    # seat the creator on BLUE (seat 0) — opening colour in clockwise turn order
    session.add(MatchSeat(
        match_id=match.id, seat_index=0, color=Color.BLUE.name,
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
    if body.fill_with_bots:
        match.state = initial_state(
            len(match.seats),
            seat_colors=[Color[s.color] for s in sorted(match.seats, key=lambda s: s.seat_index)],
        ).to_dict()
        await session.flush()
    return await _room_summary(session, match, user.id)


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

    # 60-minute join deadline for waiting rooms
    if match.status is MatchStatus.WAITING and match.created_at is not None:
        created = match.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
        if age_h >= settings.ROOM_IDLE_CLOSE_HOURS:
            match.status = MatchStatus.ABANDONED
            await session.flush()
            raise HTTPException(status.HTTP_409_CONFLICT, "Room expired (60 minute join deadline)")

    # already seated?
    for s in match.seats:
        if s.user_id == user.id:
            return await _room_summary(session, match, user.id)

    if not _joinable(match):
        if _has_finisher(match):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Late joining closed after the first player finished",
            )
        raise HTTPException(status.HTTP_409_CONFLICT, "Match is full")

    # Every outsider asks first, before or during play. The durable row survives app
    # and backend restarts; a rejected player may request again later.
    req = (
        await session.execute(
            select(MatchJoinRequest).where(
                MatchJoinRequest.match_id == match.id,
                MatchJoinRequest.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if req is None:
        session.add(MatchJoinRequest(
            match_id=match.id, user_id=user.id, status="pending",
            requested_at=now,
        ))
    elif req.status != "pending":
        req.status = "pending"
        req.requested_at = now
        req.resolved_at = None
        req.resolved_by = None
    await session.flush()
    await hub.broadcast(match.code, {"type": "join_requests"})
    return await _room_summary(session, match, user.id)


@router.get("", response_model=list[MatchSummary])
async def list_open_matches(
    user: User = Depends(get_current_user),
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
    return [_summary(m, user_id=user.id) for m in rows]


@router.get("/lobby", response_model=MatchLobby)
async def lobby_matches(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Server-backed lobby sections, including rooms recoverable after app restart."""
    active_statuses = (MatchStatus.WAITING, MatchStatus.PLAYING)
    seated_ids = select(MatchSeat.match_id).where(MatchSeat.user_id == user.id)
    requested_ids = select(MatchJoinRequest.match_id).where(
        MatchJoinRequest.user_id == user.id,
        MatchJoinRequest.status == "pending",
    )
    mine = (
        await session.execute(
            select(Match)
            .where(
                Match.status.in_(active_statuses),
                Match.id.in_(seated_ids.union(requested_ids)),
            )
            .order_by(Match.updated_at.desc())
        )
    ).scalars().all()
    waiting = (
        await session.execute(
            select(Match)
            .where(
                Match.is_public.is_(True),
                Match.is_bot_table.is_(False),
                Match.status == MatchStatus.WAITING,
            )
            .order_by(Match.created_at.desc())
            .limit(30)
        )
    ).scalars().all()
    playing = (
        await session.execute(
            select(Match)
            .where(
                Match.is_public.is_(True),
                Match.is_bot_table.is_(False),
                Match.status == MatchStatus.PLAYING,
            )
            .order_by(Match.updated_at.desc())
            .limit(30)
        )
    ).scalars().all()
    playing = [m for m in playing if _joinable(m)]

    all_matches = {m.id: m for m in [*mine, *waiting, *playing]}
    host_ids = {m.created_by for m in all_matches.values() if m.created_by}
    host_names = {}
    if host_ids:
        host_names = {
            uid: name or "Player"
            for uid, name in (
                await session.execute(
                    select(User.id, User.first_name).where(User.id.in_(host_ids))
                )
            ).all()
        }
    request_statuses = {
        mid: req_status
        for mid, req_status in (
            await session.execute(
                select(MatchJoinRequest.match_id, MatchJoinRequest.status).where(
                    MatchJoinRequest.user_id == user.id,
                    MatchJoinRequest.match_id.in_(all_matches),
                )
            )
        ).all()
    } if all_matches else {}

    def out(m: Match) -> MatchSummary:
        return _summary(
            m,
            user_id=user.id,
            request_status=request_statuses.get(m.id),
            host_name=host_names.get(m.created_by, "Player"),
        )

    mine_ids = {m.id for m in mine}
    return MatchLobby(
        mine=[out(m) for m in mine],
        waiting=[out(m) for m in waiting if m.id not in mine_ids],
        playing=[out(m) for m in playing if m.id not in mine_ids],
    )


@router.get("/{code}", response_model=MatchSummary)
async def get_match(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    match = (
        await session.execute(select(Match).where(Match.code == code.upper()))
    ).scalar_one_or_none()
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match not found")
    return await _room_summary(session, match, user.id)


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
    if match.status not in (MatchStatus.WAITING, MatchStatus.PLAYING):
        raise HTTPException(status.HTTP_409_CONFLICT, "Room is no longer open")
    req = (
        await session.execute(
            select(MatchJoinRequest).where(
                MatchJoinRequest.match_id == match.id,
                MatchJoinRequest.user_id == body.user_id,
                MatchJoinRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Player has no pending request")
    if match.status is MatchStatus.PLAYING:
        # The live runtime is the single writer for an in-progress board.
        try:
            await manager.accept_late_join(
                session, match, req, body.color, user.id
            )
        except LateJoinError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        await session.refresh(match, attribute_names=["seats", "state"])
        await hub.broadcast(match.code, {"type": "join_requests"})
        return await _room_summary(session, match, user.id)

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

    if match.entry_fee:
        try:
            await ledger.debit(
                session,
                body.user_id,
                match.entry_fee,
                reason="entry_fee",
                ref_type="match",
                ref_id=match.id,
            )
        except InsufficientFunds as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Joiner cannot afford the entry fee"
            ) from e

    session.add(MatchSeat(
        match_id=match.id, seat_index=free, color=colour,
        user_id=body.user_id, is_bot=False, connected=False,
    ))
    req.status = "accepted"
    req.resolved_at = datetime.now(timezone.utc)
    req.resolved_by = user.id
    if len(match.seats) + 1 >= match.max_players:
        others = (
            await session.execute(
                select(MatchJoinRequest).where(
                    MatchJoinRequest.match_id == match.id,
                    MatchJoinRequest.status == "pending",
                    MatchJoinRequest.id != req.id,
                )
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for other in others:
            other.status = "expired"
            other.resolved_at = now
            other.resolved_by = user.id
    await session.flush()
    await session.refresh(match, attribute_names=["seats"])
    await hub.broadcast(match.code, {"type": "join_requests"})
    return await _room_summary(session, match, user.id)


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
    req = (
        await session.execute(
            select(MatchJoinRequest).where(
                MatchJoinRequest.match_id == match.id,
                MatchJoinRequest.user_id == body.user_id,
                MatchJoinRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if req is not None:
        req.status = "rejected"
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = user.id
        await session.flush()
        await hub.broadcast(match.code, {"type": "join_requests"})
    return await _room_summary(session, match, user.id)


@router.post("/{code}/withdraw", response_model=MatchSummary)
async def withdraw_request(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Requester withdraws their own pending join without deleting the audit row."""
    match = await _load_room(session, code)
    req = (
        await session.execute(
            select(MatchJoinRequest).where(
                MatchJoinRequest.match_id == match.id,
                MatchJoinRequest.user_id == user.id,
                MatchJoinRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if req is not None:
        req.status = "rejected"
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = user.id
        await session.flush()
        await hub.broadcast(match.code, {"type": "join_requests"})
    return await _room_summary(session, match, user.id)


@router.post("/{code}/color", response_model=MatchSummary)
async def set_color(
    code: str,
    body: SetColorRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Host-only: (re)assign a seated player's colour — including the host's own.

    If the requested colour already belongs to someone else the two simply swap, so the
    four colours always stay distinct without the host having to shuffle manually.
    """
    match = await _load_room(session, code)
    if match.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the host can set colours")
    if match.status is not MatchStatus.WAITING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Game already started")

    colour = body.color.upper()
    if colour not in {c.name for c in _COLORS}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown colour")

    target = next((s for s in match.seats if s.user_id == body.user_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That player is not seated here")
    if target.color != colour:
        holder = next((s for s in match.seats if s.color == colour), None)
        if holder is not None:
            holder.color = target.color   # swap so colours stay unique
        target.color = colour
        await session.flush()
        await session.refresh(match, attribute_names=["seats"])
    return await _room_summary(session, match, user.id)


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
        return await _room_summary(session, match, user.id)
    if match.status is not MatchStatus.WAITING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Room is no longer open")
    seated = sum(1 for s in match.seats if s.user_id is not None or s.is_bot)
    if seated < 2:
        raise HTTPException(status.HTTP_409_CONFLICT, "Need at least 2 players to start")
    ordered = sorted(match.seats, key=lambda s: s.seat_index)
    match.state = initial_state(
        len(ordered), seat_colors=[Color[s.color] for s in ordered]
    ).to_dict()
    match.status = MatchStatus.PLAYING
    _DICE.pop(match.code, None)
    await session.flush()
    await session.refresh(match, attribute_names=["seats"])
    return await _room_summary(session, match, user.id)


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
    now = datetime.now(timezone.utc)
    pending = (
        await session.execute(
            select(MatchJoinRequest).where(
                MatchJoinRequest.match_id == match.id,
                MatchJoinRequest.status == "pending",
            )
        )
    ).scalars().all()
    for req in pending:
        req.status = "expired"
        req.resolved_at = now
        req.resolved_by = user.id
    _DICE.pop(match.code, None)
    return {"ok": True}


@router.get("/{code}/dice", response_model=DiceState)
async def get_dice(
    code: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    match = await _load_room(session, code)
    return _dice_state(match.code)


@router.post("/{code}/dice", response_model=DiceState)
async def roll_dice(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Roll the fun lobby die (server-side, rate-limited) and return the ranking."""
    match = await _load_room(session, code)
    in_room = await _is_room_member(session, match, user.id)
    if not in_room:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the room to play")

    now = time.time()
    room = _DICE.setdefault(match.code, {})
    me = room.get(user.id)
    if me is not None and now - me["last_at"] < DICE_COOLDOWN:
        wait = math.ceil(DICE_COOLDOWN - (now - me["last_at"]))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, f"Slow down — {wait}s to go"
        )

    value = random.randint(1, 6)
    if me is None:
        me = {
            "user_id": user.id, "name": user.first_name or "Player",
            "rolls": 0, "total": 0, "best": 0, "last_value": 0, "last_at": 0.0,
        }
        room[user.id] = me
    me["rolls"] += 1
    me["total"] += value
    me["best"] = max(me["best"], value)
    me["last_value"] = value
    me["last_at"] = now
    return _dice_state(match.code)


@router.get("/{code}/knocks", response_model=list[KnockEvent])
async def match_knocks(
    code: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Every knock and passed-up knock (potential) in this game — visible to anyone in the
    room. Each carries the attacker and the victim they knocked (or could have)."""
    match = await _load_room(session, code)
    rows = (
        await session.execute(
            select(Knockout).where(Knockout.match_id == match.id).order_by(Knockout.id)
        )
    ).scalars().all()
    if not rows:
        return []

    # resolve display names: humans from users, empty seats/bots from the seat list
    uids = {r.attacker_user_id for r in rows} | {r.victim_user_id for r in rows if r.victim_user_id}
    names: dict[int, str] = {}
    if uids:
        found = (
            await session.execute(select(User.id, User.first_name).where(User.id.in_(uids)))
        ).all()
        names = {rid: (fn or "Player") for rid, fn in found}
    seat_name = {
        s.seat_index: ("Bot" if s.is_bot else names.get(s.user_id, "Player"))
        for s in match.seats
    }

    def vname(r: Knockout) -> str:
        if r.victim_user_id is not None:
            return names.get(r.victim_user_id, "Player")
        return seat_name.get(r.victim_seat, "Bot")

    return [
        KnockEvent(
            id=r.id, turn=r.turn, taken=r.taken,
            attacker_user_id=r.attacker_user_id, attacker_seat=r.attacker_seat,
            attacker_name=names.get(r.attacker_user_id, seat_name.get(r.attacker_seat, "Player")),
            victim_user_id=r.victim_user_id, victim_seat=r.victim_seat, victim_name=vname(r),
        )
        for r in rows
    ]


async def _recent_chat(
    session: AsyncSession, match_id: int, me_id: int
) -> list[ChatMessage]:
    """The last _CHAT_MAX non-deleted messages for a match, oldest → newest, each with
    its aggregated reactions and the viewer's own reaction."""
    rows = (
        await session.execute(
            select(ChatRow)
            .where(ChatRow.match_id == match_id, ChatRow.deleted_at.is_(None))
            .order_by(ChatRow.id.desc())
            .limit(_CHAT_MAX)
        )
    ).scalars().all()
    rows = list(reversed(rows))

    counts: dict[int, dict[str, int]] = {}
    mine: dict[int, str] = {}
    if rows:
        ids = [r.id for r in rows]
        reacts = (
            await session.execute(
                select(MessageReaction).where(MessageReaction.message_id.in_(ids))
            )
        ).scalars().all()
        for rx in reacts:
            counts.setdefault(rx.message_id, {})
            counts[rx.message_id][rx.emoji] = counts[rx.message_id].get(rx.emoji, 0) + 1
            if rx.user_id == me_id:
                mine[rx.message_id] = rx.emoji

    polls = await _polls_for(session, [r.poll_id for r in rows if r.poll_id], me_id)

    return [
        ChatMessage(
            id=r.id, user_id=r.user_id, name=r.name, text=r.text,
            edited=r.edited, reply_to=r.reply_to,
            reply_name=r.reply_name, reply_text=r.reply_text,
            reactions=counts.get(r.id, {}), my_reaction=mine.get(r.id),
            poll=polls.get(r.poll_id) if r.poll_id else None,
        )
        for r in rows
    ]


async def _polls_for(
    session: AsyncSession, poll_ids: list[int], me_id: int
) -> dict[int, PollOut]:
    """Build a PollOut (options + vote tallies + the viewer's vote) for each poll id."""
    ids = [pid for pid in poll_ids if pid]
    if not ids:
        return {}
    polls = (
        await session.execute(select(Poll).where(Poll.id.in_(ids)))
    ).scalars().all()
    options = (
        await session.execute(
            select(PollOption).where(PollOption.poll_id.in_(ids)).order_by(PollOption.position, PollOption.id)
        )
    ).scalars().all()
    votes = (
        await session.execute(select(PollVote).where(PollVote.poll_id.in_(ids)))
    ).scalars().all()

    by_opt: dict[int, int] = {}
    total: dict[int, int] = {}
    mine: dict[int, int] = {}
    for v in votes:
        by_opt[v.option_id] = by_opt.get(v.option_id, 0) + 1
        total[v.poll_id] = total.get(v.poll_id, 0) + 1
        if v.user_id == me_id:
            mine[v.poll_id] = v.option_id

    opts_by_poll: dict[int, list[PollOptionOut]] = {}
    for o in options:
        opts_by_poll.setdefault(o.poll_id, []).append(
            PollOptionOut(id=o.id, text=o.text, position=o.position, votes=by_opt.get(o.id, 0))
        )

    out: dict[int, PollOut] = {}
    for p in polls:
        out[p.id] = PollOut(
            id=p.id, question=p.question, kind=p.kind, status=p.status,
            total_votes=total.get(p.id, 0), my_vote=mine.get(p.id),
            options=opts_by_poll.get(p.id, []),
        )
    return out


@router.get("/{code}/chat", response_model=list[ChatMessage])
async def get_chat(
    code: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    match = await _load_room(session, code)
    return await _recent_chat(session, match.id, user.id)


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
    in_room = await _is_room_member(session, match, user.id)
    if not in_room:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the room to chat")
    # (chat stays available while the game is playing, not just in the waiting room)
    row = ChatRow(
        match_id=match.id,
        user_id=user.id,
        name=user.first_name or "Player",
        text=text,
        edited=False,
    )
    if body.reply_to is not None:
        parent = (
            await session.execute(
                select(ChatRow).where(
                    ChatRow.id == body.reply_to, ChatRow.match_id == match.id
                )
            )
        ).scalar_one_or_none()
        if parent is not None:
            row.reply_to = parent.id
            row.reply_name = parent.name
            row.reply_text = (parent.text or "")[:80]
    session.add(row)
    await session.commit()
    return await _recent_chat(session, match.id, user.id)


@router.patch("/{code}/chat/{msg_id}", response_model=list[ChatMessage])
async def edit_chat(
    code: str,
    msg_id: int,
    body: SendChatRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Edit one of your own messages."""
    match = await _load_room(session, code)
    text = body.text.strip()[:200]
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty message")
    row = (
        await session.execute(
            select(ChatRow).where(ChatRow.id == msg_id, ChatRow.match_id == match.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    if row.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your message")
    row.text = text
    row.edited = True
    await session.commit()
    return await _recent_chat(session, match.id, user.id)


@router.delete("/{code}/chat/{msg_id}", response_model=list[ChatMessage])
async def delete_chat(
    code: str,
    msg_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Soft-delete one of your own messages (the row is kept, just hidden)."""
    match = await _load_room(session, code)
    row = (
        await session.execute(
            select(ChatRow).where(ChatRow.id == msg_id, ChatRow.match_id == match.id)
        )
    ).scalar_one_or_none()
    if row is None or row.deleted_at is not None:
        return await _recent_chat(session, match.id, user.id)
    if row.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your message")
    row.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    return await _recent_chat(session, match.id, user.id)


@router.post("/{code}/chat/{msg_id}/react", response_model=list[ChatMessage])
async def react_chat(
    code: str,
    msg_id: int,
    body: ReactRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Toggle your reaction on a message. One reaction per person per message:
    tapping the same emoji removes it; a different emoji replaces it."""
    if body.emoji not in await _allowed_reactions(session):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown reaction")
    match = await _load_room(session, code)
    in_room = await _is_room_member(session, match, user.id)
    if not in_room:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the room to react")

    msg = (
        await session.execute(
            select(ChatRow).where(
                ChatRow.id == msg_id,
                ChatRow.match_id == match.id,
                ChatRow.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if msg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")

    existing = (
        await session.execute(
            select(MessageReaction).where(
                MessageReaction.message_id == msg_id,
                MessageReaction.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(MessageReaction(message_id=msg_id, user_id=user.id, emoji=body.emoji))
    elif existing.emoji == body.emoji:
        await session.delete(existing)          # tap again to remove
    else:
        existing.emoji = body.emoji             # switch reaction
    await session.commit()
    return await _recent_chat(session, match.id, user.id)


# --- polls ------------------------------------------------------------------
@router.post("/{code}/polls", response_model=list[ChatMessage])
async def create_poll(
    code: str,
    body: CreatePollRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Spawn an instant poll from a template into the chat. A "knock"-triggered template
    may only be sent when the sender actually has a capture available right now."""
    match = await _load_room(session, code)
    in_room = any(s.user_id == user.id for s in match.seats)
    if not in_room:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the room to post a poll")

    tmpl = await session.get(PollTemplate, body.template_id)
    if tmpl is None or not tmpl.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Poll not found")
    opts = [str(o).strip() for o in (tmpl.options or []) if str(o).strip()]
    if len(opts) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Poll needs at least two options")
    if tmpl.trigger == "knock" and not manager.knock_available(match.id, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "You can only ask this when you can knock")

    poll = Poll(
        match_id=match.id, created_by=user.id, template_id=tmpl.id,
        question=tmpl.question, kind="instant", status="open",
    )
    session.add(poll)
    await session.flush()
    for i, text in enumerate(opts):
        session.add(PollOption(poll_id=poll.id, text=text[:100], position=i))
    session.add(ChatRow(
        match_id=match.id, user_id=user.id, name=user.first_name or "Player",
        text=tmpl.question, poll_id=poll.id,
    ))
    await session.commit()
    return await _recent_chat(session, match.id, user.id)


@router.post("/{code}/polls/{poll_id}/vote", response_model=list[ChatMessage])
async def vote_poll(
    code: str,
    poll_id: int,
    body: VoteRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Cast (or change) your vote on a poll. One vote per person; re-voting moves it."""
    match = await _load_room(session, code)
    poll = await session.get(Poll, poll_id)
    if poll is None or poll.match_id != match.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Poll not found")
    if poll.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "Poll is closed")
    opt = await session.get(PollOption, body.option_id)
    if opt is None or opt.poll_id != poll_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown option")

    existing = (
        await session.execute(
            select(PollVote).where(PollVote.poll_id == poll_id, PollVote.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(PollVote(poll_id=poll_id, option_id=opt.id, user_id=user.id))
    elif existing.option_id == opt.id:
        await session.delete(existing)          # tap your choice again to un-vote
    else:
        existing.option_id = opt.id             # move your vote
    await session.commit()
    return await _recent_chat(session, match.id, user.id)
