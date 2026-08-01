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
from app.ludo.board import HOME, TOKENS_PER_PLAYER, Color
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
        self.is_bot_table = match.is_bot_table
        # seat index -> user_id (None = house bot)
        self.seat_user: dict[int, int | None] = {}
        self.seat_is_bot: dict[int, bool] = {}
        self.seat_names: dict[int, str] = {}   # seat -> display name (filled on connect)
        self.seat_levels: dict[int, int] = {}  # seat -> level (filled on connect)
        self.seat_skins: dict[int, str] = {}   # seat -> dice skin (filled on connect)
        self.seat_last_die: dict[int, int] = {}  # seat -> the face they last rolled
        self.seat_missed: dict[int, int] = {}    # seat -> consecutive roll timeouts
        self.kicked: set[int] = set()            # user_ids auto-kicked for inactivity
        self.removed_seats: set[int] = set()     # seats whose player was removed
        for s in match.seats:
            self.seat_user[s.seat_index] = s.user_id
            self.seat_is_bot[s.seat_index] = s.is_bot
            self.seat_names[s.seat_index] = "Bot" if s.is_bot else "Player"
            self.seat_levels[s.seat_index] = 1
            self.seat_skins[s.seat_index] = "classic"

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
        # user_ids who requested a rematch after the game finished
        self.rematch: set[int] = set()

        # lifetime-stat deltas, flushed to users' totals on every persist (see
        # _flush_stats). Keyed by user_id; only HUMAN seats are counted.
        self.stat_dice: dict[int, dict[int, int]] = {}   # uid -> {face: count}
        self.stat_dealt: dict[int, int] = {}             # uid -> captures they dealt
        self.stat_taken: dict[int, int] = {}             # uid -> captures they suffered

        # THIS game's running tally, keyed by SEAT (bots included) — never cleared, so it's
        # the source for the in-game scoreboard broadcast to viewers.
        self.game_dice: dict[int, dict[int, int]] = {}   # seat -> {face: count}
        self.game_dealt: dict[int, int] = {}             # seat -> captures dealt
        self.game_taken: dict[int, int] = {}             # seat -> captures suffered

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def is_done(self) -> bool:
        return self._task is not None and self._task.done()

    def submit(self, user_id: int, msg: dict) -> None:
        """Called by the ws route with a human action ({'type': 'roll'|'move', ...})."""
        self._queue.put_nowait((user_id, msg))

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _seat_colors(match: Match) -> list[Color]:
        return [Color[s.color] for s in sorted(match.seats, key=lambda x: x.seat_index)]

    def _is_bot_seat(self, seat: int) -> bool:
        return self.seat_is_bot.get(seat, True) or self.seat_user.get(seat) is None

    def _human_uid(self, seat: int) -> int | None:
        """The user_id at ``seat`` if it's a real human (not a house bot), else None."""
        if self.seat_is_bot.get(seat):
            return None
        return self.seat_user.get(seat)

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
                # A human match with nobody watching shouldn't keep grinding through
                # 20s turn-timeouts on a tiny box. Give a grace window for a reconnect,
                # then abandon it and let the janitor reap the runtime.
                if not self.is_bot_table and not self._human_watching():
                    await asyncio.sleep(settings.IDLE_SEAT_GRACE_SECONDS)
                    if not self._human_watching():
                        await self._abandon()
                        return
                    continue
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
                    self.seat_missed[seat] = self.seat_missed.get(seat, 0) + 1
                    logger.info(
                        "seat %s timed out on roll (%s), missed=%d",
                        seat, self.code, self.seat_missed[seat],
                    )
                    if self.seat_missed[seat] >= settings.MAX_MISSED_TURNS:
                        await self._kick(seat)
                else:
                    self.seat_missed[seat] = 0
            else:
                await self._think()
            register_roll(self.state, roll_die(self._rng))
            if self.state.die is not None:
                self.seat_last_die[seat] = self.state.die
                ghist = self.game_dice.setdefault(seat, {})
                ghist[self.state.die] = ghist.get(self.state.die, 0) + 1
                uid = self._human_uid(seat)
                if uid is not None:
                    hist = self.stat_dice.setdefault(uid, {})
                    hist[self.state.die] = hist.get(self.state.die, 0) + 1
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
            res = apply_move(self.state, chosen)
            if res.captured:
                self.game_dealt[seat] = self.game_dealt.get(seat, 0) + len(res.captured)
                mover = self._human_uid(seat)
                if mover is not None:
                    self.stat_dealt[mover] = self.stat_dealt.get(mover, 0) + len(res.captured)
                for cseat, _tok in res.captured:
                    self.game_taken[cseat] = self.game_taken.get(cseat, 0) + 1
                    victim = self._human_uid(cseat)
                    if victim is not None:
                        self.stat_taken[victim] = self.stat_taken.get(victim, 0) + 1
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
            "seat_names": {str(k): v for k, v in self.seat_names.items()},
            "seat_levels": {str(k): v for k, v in self.seat_levels.items()},
            "seat_skins": {str(k): v for k, v in self.seat_skins.items()},
            "seat_last_die": {str(k): v for k, v in self.seat_last_die.items()},
            # this game's per-seat scoreboard (dice histogram + captures)
            "seat_rolls": {
                str(k): {str(f): n for f, n in v.items()} for k, v in self.game_dice.items()
            },
            "seat_dealt": {str(k): v for k, v in self.game_dealt.items()},
            "seat_taken": {str(k): v for k, v in self.game_taken.items()},
            "removed_seats": sorted(self.removed_seats),
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
            # Fold any dice/capture deltas accumulated since the last persist into the
            # players' lifetime totals in the SAME commit — this is what makes an in-game
            # profile card reflect the current game's rolls (rather than only updating when
            # the whole game ends). _persist runs after every step, so deltas flush promptly.
            await self._flush_stats(session)
            await session.commit()

    async def _kick(self, seat: int) -> None:
        """A human who keeps missing rolls is REMOVED (not replaced by a bot): their
        pieces are cleared off the board and their turns are skipped for the rest of the
        game, and their client is told to return to the lobby.

        Clearing = send all four tokens to HOME. That makes the engine treat the seat as
        out of the running (its turns are skipped and it can never win — it's only added
        to the ranking as a straggler at game over, i.e. last), without touching the pure
        engine's rules. The client hides a removed seat entirely.
        """
        uid = self.seat_user.get(seat)
        if uid is None:
            return
        self.state.players[seat].tokens = [HOME] * TOKENS_PER_PLAYER
        self.removed_seats.add(seat)
        self.seat_missed[seat] = 0
        self.kicked.add(uid)
        await hub.broadcast(self.code, {"type": "kicked", "user_id": uid})
        logger.info("seat %s (user %s) removed from %s for inactivity", seat, uid, self.code)

    async def _abandon(self) -> None:
        """Mark a viewer-less human match abandoned so it stops using the box."""
        async with SessionLocal() as session:
            m = await session.get(Match, self.match_id)
            if m is not None and m.status is not MatchStatus.FINISHED:
                m.status = MatchStatus.ABANDONED
            await self._flush_stats(session)
            await session.commit()
        logger.info("match %s abandoned (no viewers)", self.code)

    async def _flush_stats(self, session) -> None:
        """Fold the dice + capture deltas accumulated SINCE THE LAST FLUSH into each
        human's lifetime totals, then clear them. Called from _persist after every step
        (so profiles update live) and once more on abandon; clearing makes it safe to call
        repeatedly without double-counting.

        ``dice_hist`` is JSONB keyed by the string faces "1".."6"; we reassign the dict so
        SQLAlchemy detects the change (in-place JSONB mutation isn't tracked).
        """
        uids = set(self.stat_dice) | set(self.stat_dealt) | set(self.stat_taken)
        if not uids:
            return
        from app.models import User

        for uid in uids:
            user = await session.get(User, uid)
            if user is None:
                continue
            hist = dict(user.dice_hist or {})
            for face, n in self.stat_dice.get(uid, {}).items():
                key = str(face)
                hist[key] = int(hist.get(key, 0)) + n
            user.dice_hist = hist
            user.captures_dealt = (user.captures_dealt or 0) + self.stat_dealt.get(uid, 0)
            user.captures_taken = (user.captures_taken or 0) + self.stat_taken.get(uid, 0)

        # deltas are now persisted (within this session's pending commit) — reset them so
        # the next flush only applies what's new.
        self.stat_dice = {}
        self.stat_dealt = {}
        self.stat_taken = {}

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
            # dice/capture deltas were already flushed by the final _persist; nothing to
            # add here beyond the placement/XP bumps above.
            await session.commit()
        logger.info("match %s finished, ranking=%s", self.code, self.state.ranking)
