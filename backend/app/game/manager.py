"""MatchManager — the registry of live MatchRuntimes + a background janitor.

One process-wide singleton (``manager``). It hands out a MatchRuntime per match (creating
it on first access), routes human actions to the right runtime, and runs a periodic
janitor that reaps finished/idle matches and keeps the configured number of self-play bot
tables alive so the lobby never looks empty.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.game.runtime import MatchRuntime
from app.ludo.board import Color
from app.ludo.state import PlayerState
from app.models import Match, MatchJoinRequest, MatchSeat, MatchStatus, User
from app.services import ledger
from app.services.ledger import InsufficientFunds

logger = logging.getLogger("ludo.manager")


class LateJoinError(RuntimeError):
    pass


class MatchManager:
    def __init__(self) -> None:
        self._runtimes: dict[int, MatchRuntime] = {}
        self._janitor: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._presence_reset = False

    async def get_runtime(self, session: AsyncSession, match: Match) -> MatchRuntime:
        async with self._lock:
            rt = self._runtimes.get(match.id)
            if rt is None:
                rt = MatchRuntime(match)
                self._runtimes[match.id] = rt
            return rt

    def handle_action(self, match_id: int, user_id: int, msg: dict) -> None:
        rt = self._runtimes.get(match_id)
        if rt is not None:
            rt.submit(user_id, msg)

    def knock_available(self, match_id: int, user_id: int) -> bool:
        rt = self._runtimes.get(match_id)
        return bool(rt and rt.knock_available(user_id))

    async def accept_late_join(
        self,
        session: AsyncSession,
        match: Match,
        request: MatchJoinRequest,
        color_name: str,
        host_id: int,
    ) -> None:
        """Serialize a creator-approved late join through the live driver."""
        rt = await self.get_runtime(session, match)
        rt.start()

        async def _accept(live: MatchRuntime) -> None:
            # Own session: this callback runs on the driver task, not the request task.
            async with SessionLocal() as session:
                await self._accept_locked(session, live, match.id, request.id, color_name, host_id)

        await rt.run_control(_accept)

    async def _accept_locked(
        self,
        session: AsyncSession,
        live: MatchRuntime,
        match_id: int,
        request_id: int,
        color_name: str,
        host_id: int,
    ) -> None:
        m = (
            await session.execute(
                select(Match)
                .where(Match.id == match_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        req = (
            await session.execute(
                select(MatchJoinRequest)
                .where(MatchJoinRequest.id == request_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if m.status is not MatchStatus.PLAYING:
            raise LateJoinError("Game is no longer in progress")
        if req.status != "pending":
            raise LateJoinError("Join request is no longer pending")
        if live.state.ranking or any(
            p.finished_at is not None for p in live.state.players
        ):
            raise LateJoinError(
                "Late joining closed after the first player finished"
            )

        seats = (
            await session.execute(
                select(MatchSeat)
                .where(MatchSeat.match_id == m.id)
                .order_by(MatchSeat.seat_index)
            )
        ).scalars().all()
        if any(s.user_id == req.user_id for s in seats):
            req.status = "accepted"
            req.resolved_at = datetime.now(timezone.utc)
            req.resolved_by = host_id
            await session.commit()
            return
        taken = {s.seat_index for s in seats}
        free = next((i for i in range(m.max_players) if i not in taken), None)
        if free is None:
            raise LateJoinError("Room is full")
        color_name_upper = color_name.upper()
        if color_name_upper not in {c.name for c in Color}:
            raise LateJoinError("Unknown colour")
        if any(s.color == color_name_upper for s in seats):
            raise LateJoinError("That colour is taken")
        # GameState players are indexed by seat; normal room admission always fills
        # the first free seat, so a late player must append at that exact index.
        if free != len(live.state.players):
            raise LateJoinError("Room seat layout cannot accept a late player")

        joiner = await session.get(User, req.user_id)
        if joiner is None:
            raise LateJoinError("Player no longer exists")
        if m.entry_fee:
            try:
                await ledger.debit(
                    session,
                    joiner.id,
                    m.entry_fee,
                    reason="entry_fee",
                    ref_type="match",
                    ref_id=m.id,
                )
            except InsufficientFunds as exc:
                raise LateJoinError(
                    "Joiner cannot afford the entry fee"
                ) from exc

        color = Color[color_name_upper]
        new_state = live.state.clone()
        new_state.players.append(PlayerState(color=color))
        session.add(MatchSeat(
            match_id=m.id,
            seat_index=free,
            color=color.name,
            user_id=joiner.id,
            is_bot=False,
            connected=False,
            joined_at_turn=new_state.turn,
        ))
        req.status = "accepted"
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = host_id
        m.state = new_state.to_dict()

        if len(seats) + 1 >= m.max_players:
            others = (
                await session.execute(
                    select(MatchJoinRequest).where(
                        MatchJoinRequest.match_id == m.id,
                        MatchJoinRequest.status == "pending",
                        MatchJoinRequest.id != req.id,
                    )
                )
            ).scalars().all()
            for other in others:
                other.status = "expired"
                other.resolved_at = datetime.now(timezone.utc)
                other.resolved_by = host_id

        await session.commit()
        live.install_late_player(
            state=new_state,
            seat=free,
            user_id=joiner.id,
            color=color,
            name=joiner.first_name,
            level=joiner.level,
            skin=joiner.dice_skin,
        )
        await live._broadcast()
        logger.info(
            "late join accepted match=%s user=%s seat=%s turn=%s",
            m.code, joiner.id, free, new_state.turn,
        )

    async def create_rematch(self, session: AsyncSession, old_match_id: int) -> Match:
        """Clone a finished match's seats into a fresh PLAYING match (a rematch)."""
        seats = (
            await session.execute(
                select(MatchSeat).where(MatchSeat.match_id == old_match_id)
            )
        ).scalars().all()
        old = await session.get(Match, old_match_id)
        new = Match(
            max_players=old.max_players if old else 4,
            is_public=False,
            entry_fee=old.entry_fee if old else 0,
            is_bot_table=old.is_bot_table if old else False,
            status=MatchStatus.PLAYING,
        )
        session.add(new)
        await session.flush()
        for s in seats:
            session.add(MatchSeat(
                match_id=new.id, seat_index=s.seat_index, color=s.color,
                user_id=s.user_id, is_bot=s.is_bot, connected=False,
            ))
        await session.commit()
        await session.refresh(new, attribute_names=["seats"])
        logger.info("rematch %s created from %s", new.code, old_match_id)
        return new

    # ---- janitor ----------------------------------------------------------
    def start_janitor(self) -> None:
        if self._janitor is None:
            self._janitor = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("janitor tick failed")
            await asyncio.sleep(settings.JANITOR_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        if not self._presence_reset:
            async with SessionLocal() as session:
                await session.execute(
                    update(MatchSeat)
                    .where(MatchSeat.is_bot.is_(False))
                    .values(connected=False)
                )
                await session.commit()
            self._presence_reset = True
        # drop runtimes whose game finished or whose driver task has ended (abandoned)
        async with self._lock:
            from app.ludo import Phase

            dead: list[tuple[int, MatchRuntime]] = []
            for mid, rt in list(self._runtimes.items()):
                if rt.state.phase is Phase.FINISHED or rt.is_done():
                    dead.append((mid, rt))
            for mid, rt in dead:
                await rt.stop()
                self._runtimes.pop(mid, None)

        await self._close_idle_waiting_rooms()
        await self._purge_finished_bot_tables()
        await self._ensure_bot_tables()

    async def _close_idle_waiting_rooms(self) -> None:
        """Abandon WAITING rooms older than ROOM_IDLE_CLOSE_HOURS; clear lobby mem."""
        hours = settings.ROOM_IDLE_CLOSE_HOURS
        if hours <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        async with SessionLocal() as session:
            stale = (
                await session.execute(
                    select(Match).where(
                        Match.status == MatchStatus.WAITING,
                        Match.created_at < cutoff,
                    )
                )
            ).scalars().all()
            if not stale:
                return
            codes: list[str] = []
            for m in stale:
                m.status = MatchStatus.ABANDONED
                codes.append(m.code)
            await session.execute(
                update(MatchJoinRequest)
                .where(
                    MatchJoinRequest.match_id.in_([m.id for m in stale]),
                    MatchJoinRequest.status == "pending",
                )
                .values(status="expired", resolved_at=datetime.now(timezone.utc))
            )
            await session.commit()
        # clear only the non-durable waiting-room dice state
        try:
            from app.api import routes_matches as lobby

            for code in codes:
                lobby._DICE.pop(code, None)
        except Exception:  # noqa: BLE001
            logger.exception("failed clearing lobby state for idle rooms")
        logger.info("abandoned %d idle waiting rooms", len(codes))

    async def _purge_finished_bot_tables(self) -> None:
        """Delete finished/abandoned bot-table matches so self-play does not bloat tables."""
        async with SessionLocal() as session:
            ids = (
                await session.execute(
                    select(Match.id).where(
                        Match.is_bot_table.is_(True),
                        Match.status.in_((MatchStatus.FINISHED, MatchStatus.ABANDONED)),
                    )
                )
            ).scalars().all()
            if not ids:
                return
            await session.execute(delete(Match).where(Match.id.in_(ids)))
            await session.commit()
            logger.info("purged %d finished bot tables", len(ids))

    async def _ensure_bot_tables(self) -> None:
        """Keep ``settings.BOT_TABLES`` self-play matches running."""
        if settings.BOT_TABLES <= 0:
            return
        async with SessionLocal() as session:
            live = (
                await session.execute(
                    select(Match).where(
                        Match.is_bot_table.is_(True),
                        Match.status == MatchStatus.PLAYING,
                    )
                )
            ).scalars().all()
            need = settings.BOT_TABLES - len(live)
            for _ in range(max(0, need)):
                match = await self._spawn_bot_table(session)
                if match is not None:
                    rt = await self.get_runtime(session, match)
                    rt.start()

    async def _spawn_bot_table(self, session: AsyncSession) -> Match | None:
        """Create a 4-bot self-play match. Bots are seat placeholders (user_id=None)."""
        match = Match(
            max_players=4,
            is_public=True,
            is_bot_table=True,
            status=MatchStatus.PLAYING,
        )
        session.add(match)
        await session.flush()
        colors = [Color.BLUE, Color.RED, Color.GREEN, Color.YELLOW]
        for i, c in enumerate(colors):
            session.add(MatchSeat(
                match_id=match.id, seat_index=i, color=c.name, is_bot=True, connected=True,
            ))
        await session.commit()
        await session.refresh(match, attribute_names=["seats"])
        logger.info("spawned bot table %s", match.code)
        return match

    async def shutdown(self) -> None:
        if self._janitor:
            self._janitor.cancel()
        async with self._lock:
            for rt in list(self._runtimes.values()):
                await rt.stop()
            self._runtimes.clear()


manager = MatchManager()
