"""MatchRuntime — drives ONE live Ludo match.

This is the operational heart: it owns the in-memory ``GameState``, the RNG, the clock and
the turn loop, and it fans out state to viewers over the websocket hub. It calls into the
pure engine (``app.ludo``) for every rule and never re-implements one.

Design (single async driver task per match):

    loop:
      if game finished -> settle placements + payouts, stop
      seat = current player
      ROLL phase:
        bot or human-timed-out -> auto roll; else await the human's "roll" (with timeout)
      MOVE phase:
        no legal move   -> pass
        bot or timed-out -> heuristic move
        human            -> await their "move" (with timeout -> heuristic)
      broadcast, small human-visible delay, repeat

Human input arrives through ``submit()`` (called by the ws route) into an asyncio.Queue.
The loop drains it; anything stale or from the wrong seat is ignored.

This is deliberately compact — it is the base a follow-up session extends with reconnect
grace, entry-fee escrow, richer animations and per-seat views. Search ROADMAP for those.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from app.config import settings
from app.database import SessionLocal
from app.ludo import (
    GameState,
    Phase,
    apply_move,
    initial_state,
    legal_moves,
    register_roll,
    roll_die,
)
from app.ludo.board import Color
from app.ludo.bots import choose_move
from app.game.connection import hub
from app.models import Match, MatchStatus

logger = logging.getLogger("ludo.runtime")


class MatchRuntime:
    def __init__(self, match: Match) -> None:
        self.match_id = match.id
        self.code = match.code
        self.max_players = match.max_players
        self.entry_fee = match.entry_fee
        # seat index -> user_id (None = house bot)
        self.seat_user: dict[int, int | None] = {}
        self.seat_is_bot: dict[int, bool] = {}
        for s in match.seats:
            self.seat_user[s.seat_index] = s.user_id
            self.seat_is_bot[s.seat_index] = s.is_bot

        self.state: GameState = (
            GameState.from_dict(match.state)
            if match.state
            else initial_state(len(match.seats) or 2, seat_colors=self._seat_colors(match))
        )
        self._rng = random.Random(match.id * 1000003)
        self._queue: asyncio.Queue[tuple[int, dict]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._started = False
        # wall-clock deadline for the current human action (unix seconds) — the client
        # renders a countdown against it; None when it's a bot's turn / no clock.
        self._deadline: float | None = None

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def submit(self, user_id: int, msg: dict) -> None:
        """Called by the ws route with a human action ({'type': 'roll'|'move', ...})."""
        self._queue.put_nowait((user_id, msg))

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _seat_colors(match: Match) -> list[Color]:
        return [Color[s.color] for s in sorted(match.seats, key=lambda x: x.seat_index)]

    def _is_bot_seat(self, seat: int) -> bool:
        return self.seat_is_bot.get(seat, True) or self.seat_user.get(seat) is None

    def _human_watching(self) -> bool:
        return hub.has_viewers(self.code)

    async def _drain_for(self, seat: int, want: str, timeout: float) -> dict | None:
        """Wait up to ``timeout`` for a matching action from the seat's human, else None."""
        deadline = timeout
        try:
            while deadline > 0:
                loop = asyncio.get_event_loop()
                t0 = loop.time()
                user_id, msg = await asyncio.wait_for(self._queue.get(), timeout=deadline)
                deadline -= loop.time() - t0
                if self.seat_user.get(seat) == user_id and msg.get("type") == want:
                    return msg
                # ignore stale/foreign input and keep waiting
        except asyncio.TimeoutError:
            return None
        return None

    # ---- the driver -------------------------------------------------------
    async def _run(self) -> None:
        try:
            await self._broadcast()
            while self.state.phase is not Phase.FINISHED:
                await self._step()
                await self._persist()
            await self._settle()
            await self._broadcast()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("runtime crashed for match %s", self.code)

    async def _step(self) -> None:
        seat = self.state.current
        bot_seat = self._is_bot_seat(seat)

        # Throttle bot-only tables when nobody is watching (RAM/CPU on a 1GB box).
        if bot_seat and not self._human_watching():
            await asyncio.sleep(settings.BOT_TABLE_IDLE_SECONDS)

        if self.state.phase is Phase.ROLL:
            if not bot_seat:
                self._deadline = time.time() + settings.TURN_TIMEOUT_SECONDS
                await self._broadcast()  # push the countdown to the client
                got = await self._drain_for(seat, "roll", settings.TURN_TIMEOUT_SECONDS)
                self._deadline = None
                if got is None:
                    logger.info("seat %s timed out on roll (%s)", seat, self.code)
            else:
                await self._think()
            register_roll(self.state, roll_die(self._rng))
            await self._broadcast()
            await asyncio.sleep(settings.ROLL_REVEAL_SECONDS)  # hold on the die face
            return

        if self.state.phase is Phase.MOVE:
            moves = legal_moves(self.state)
            if not moves:
                await self._broadcast()  # show the die + "no moves" state
                await asyncio.sleep(settings.NO_MOVE_SECONDS)
                apply_move(self.state, None)
                await self._broadcast()
                return

            chosen = None
            if not bot_seat:
                self._deadline = time.time() + settings.TURN_TIMEOUT_SECONDS
                await self._broadcast()  # push the countdown to the client
                got = await self._drain_for(seat, "move", settings.TURN_TIMEOUT_SECONDS)
                self._deadline = None
                if got is not None:
                    idx = got.get("token_index")
                    chosen = next((m for m in moves if m.token_index == idx), None)
            else:
                await self._think()
            if chosen is None:
                chosen = choose_move(self.state, moves, self._rng)
            apply_move(self.state, chosen)
            await self._broadcast()
            await asyncio.sleep(settings.MOVE_SETTLE_SECONDS)  # let the glide finish

    async def _think(self) -> None:
        await asyncio.sleep(self._rng.uniform(settings.BOT_THINK_MIN, settings.BOT_THINK_MAX))

    # ---- rendering / persistence -----------------------------------------
    def render(self, _user_id: int | None = None) -> dict:
        return {
            "type": "state",
            "code": self.code,
            "state": self.state.to_dict(),
            "seat_user": {str(k): v for k, v in self.seat_user.items()},
            "legal_moves": [m.to_dict() for m in legal_moves(self.state)],
            # turn clock: client shows a countdown from `now` to `deadline` (unix secs)
            "deadline": self._deadline,
            "now": time.time(),
            "turn_seconds": settings.TURN_TIMEOUT_SECONDS,
        }

    async def _broadcast(self) -> None:
        await hub.send_personalised(self.code, self.render)

    async def _persist(self) -> None:
        async with SessionLocal() as session:
            m = await session.get(Match, self.match_id)
            if m is None:
                return
            m.state = self.state.to_dict()
            if self.state.phase is not Phase.FINISHED:
                m.status = MatchStatus.PLAYING
            await session.commit()

    async def _settle(self) -> None:
        """Write final placements, pay the pot to the winner, bump stats."""
        from app.models import User

        async with SessionLocal() as session:
            m = await session.get(Match, self.match_id)
            if m is None:
                return
            m.status = MatchStatus.FINISHED
            m.state = self.state.to_dict()

            pot = self.entry_fee * len(self.seat_user)
            for place, seat in enumerate(self.state.ranking, start=1):
                for s in m.seats:
                    if s.seat_index == seat:
                        s.place = place
                uid = self.seat_user.get(seat)
                if uid is None:
                    continue
                user = await session.get(User, uid)
                if user is None:
                    continue
                user.games_played += 1
                if place == 1:
                    user.games_won += 1
                    user.coins += pot
                    user.xp += 100
                else:
                    user.xp += 25
            await session.commit()
        logger.info("match %s finished, ranking=%s", self.code, self.state.ranking)
