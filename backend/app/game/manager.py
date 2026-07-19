"""MatchManager — the registry of live MatchRuntimes + a background janitor.

One process-wide singleton (``manager``). It hands out a MatchRuntime per match (creating
it on first access), routes human actions to the right runtime, and runs a periodic
janitor that reaps finished/idle matches and keeps the configured number of self-play bot
tables alive so the lobby never looks empty.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.game.runtime import MatchRuntime
from app.ludo.board import Color
from app.models import Match, MatchSeat, MatchStatus

logger = logging.getLogger("ludo.manager")


class MatchManager:
    def __init__(self) -> None:
        self._runtimes: dict[int, MatchRuntime] = {}
        self._janitor: asyncio.Task | None = None
        self._lock = asyncio.Lock()

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
        # drop runtimes whose game finished
        for mid, rt in list(self._runtimes.items()):
            from app.ludo import Phase
            if rt.state.phase is Phase.FINISHED:
                await rt.stop()
                self._runtimes.pop(mid, None)
        await self._ensure_bot_tables()

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
        colors = [Color.RED, Color.GREEN, Color.YELLOW, Color.BLUE]
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
        for rt in list(self._runtimes.values()):
            await rt.stop()


manager = MatchManager()
