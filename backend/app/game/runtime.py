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
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

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
from app.ludo.board import BASE, HOME, TOKENS_PER_PLAYER, Color
from app.ludo.bots import choose_move
from app.ludo.rules import _captures_at
from app.appconfig import runtime_config, TUNABLE
from app.game import card_catalog
from app.game.connection import hub
from app.models import Match, MatchJoinRequest, MatchStatus, DiceRoll, Knockout, CardDraw
from app.services import ledger
from app.services.coin_features import (
    boost_cost,
    boost_options_for,
    boost_params,
)
from app.services.ledger import InsufficientFunds, placement_payout
from sqlalchemy import insert, select, update
from app.analytics import clickhouse as analytics

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
        self._queue: asyncio.Queue[tuple[int, dict]] = asyncio.Queue(
            maxsize=max(8, settings.ACTION_QUEUE_MAX)
        )
        self._controls: asyncio.Queue[
            tuple[Callable[["MatchRuntime"], Awaitable[Any]], asyncio.Future]
        ] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._started = False
        self._viewer_event = asyncio.Event()
        # wall-clock deadline for the current human action (unix seconds) — the client
        # renders a countdown against it; None when it's a bot's turn / no clock.
        self._deadline: float | None = None
        # user_ids who requested a rematch after the game finished
        self.rematch: set[int] = set()
        self.rematch_code: str | None = None  # set once when rematch match is created
        self._rematch_lock = asyncio.Lock()
        self._steps_since_persist = 0
        self._persist_dirty = False

        # lifetime-stat deltas, flushed to users' totals on every persist (see
        # _flush_stats). Keyed by user_id; only HUMAN seats are counted.
        self.stat_dice: dict[int, dict[int, int]] = {}   # uid -> {face: count}
        self.stat_dealt: dict[int, int] = {}             # uid -> captures they dealt
        self.stat_taken: dict[int, int] = {}             # uid -> captures they suffered
        self.stat_potential: dict[int, int] = {}         # uid -> captures they passed up

        # THIS game's running tally, keyed by SEAT (bots included) — never cleared, so it's
        # the source for the in-game scoreboard broadcast to viewers.
        self.game_dice: dict[int, dict[int, int]] = {}   # seat -> {face: count}
        self.game_dealt: dict[int, int] = {}             # seat -> captures dealt
        self.game_taken: dict[int, int] = {}             # seat -> captures suffered
        self.game_potential: dict[int, int] = {}         # seat -> captures passed up
        self.pot_reward: dict[int, int] = {}             # seat -> could've-cards already granted

        # append-only event rows buffered per step, bulk-inserted in _persist (humans only)
        self.buf_rolls: list[dict] = []
        self.buf_knocks: list[dict] = []
        self.buf_cards: list[dict] = []
        # the in-progress fantasy-card draw, surfaced to the client (None when idle)
        self._card: dict | None = None
        # admin-tunable config snapshot — defaults now, refreshed from the DB in _run()
        self.cfg: dict[str, int] = {k: int(m["default"]) for k, m in TUNABLE.items()}
        # (seat, effect) of each card played this game — for Mirror (copy an opponent's)
        self._recent_cards: list[tuple[int, str]] = []
        self.stat_coins: dict[int, int] = {}   # uid -> bonus coins to grant (Jackpot)
        self.stat_couldve_coins: dict[int, int] = {}  # uid -> PAY_COULDVE accruals
        self.stat_insurance: dict[int, int] = {}  # uid -> insurance payouts pending
        self._rerolled_this_turn = False
        self._insured: set[int] = set()  # user_ids with active knock insurance
        self._hint_token: int | None = None  # recommended token_index this move

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._started and self._task is not None and not self._task.done():
            return
        self._started = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        self._started = False
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def is_done(self) -> bool:
        return self._task is not None and self._task.done()

    def viewer_connected(self) -> None:
        """Wake a paused human game as soon as any viewer reconnects."""
        self._viewer_event.set()

    def submit(self, user_id: int, msg: dict) -> None:
        """Called by the ws route with a human action ({'type': 'roll'|'move', ...})."""
        try:
            self._queue.put_nowait((user_id, msg))
        except asyncio.QueueFull:
            # Drop oldest, keep the newest action (coalesce spam under load).
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait((user_id, msg))
            except asyncio.QueueFull:
                logger.warning("action queue full for match %s; dropping", self.code)

    async def run_control(
        self, callback: Callable[["MatchRuntime"], Awaitable[Any]]
    ) -> Any:
        """Run a membership/control mutation on the driver task.

        This keeps the runtime a single writer: controls execute between state
        transitions or while a human action wait is safely suspended.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self._controls.put((callback, future))
        self._viewer_event.set()  # also wake a room paused with no viewers
        return await future

    async def _execute_control(self, item=None) -> bool:
        if item is None:
            try:
                item = self._controls.get_nowait()
            except asyncio.QueueEmpty:
                return False
        callback, future = item
        if future.cancelled():
            return True
        try:
            future.set_result(await callback(self))
        except Exception as exc:  # noqa: BLE001 - deliver to the waiting REST request
            future.set_exception(exc)
        return True

    def install_late_player(
        self,
        *,
        state: GameState,
        seat: int,
        user_id: int,
        color: Color,
        name: str,
        level: int,
        skin: str,
    ) -> None:
        """Install the already-committed durable membership into this runtime."""
        self.state = state
        self.seat_user[seat] = user_id
        self.seat_is_bot[seat] = False
        self.seat_names[seat] = name or "Player"
        self.seat_levels[seat] = level or 1
        self.seat_skins[seat] = skin or "classic"
        self.seat_missed[seat] = 0
        self.game_dice.setdefault(seat, {})
        self.game_dealt.setdefault(seat, 0)
        self.game_taken.setdefault(seat, 0)
        self.game_potential.setdefault(seat, 0)
        self.pot_reward.setdefault(seat, 0)

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

    def knock_available(self, user_id: int) -> bool:
        """True when it's this user's move and a capturing move is legal — the moment the
        'Should I knock it?' poll is offered."""
        seat = next((s for s, u in self.seat_user.items() if u == user_id), None)
        if seat is None or self.state.current != seat or self.state.phase is not Phase.MOVE:
            return False
        return any(m.captures for m in legal_moves(self.state))

    def _human_watching(self) -> bool:
        return hub.has_viewers(self.code)

    async def _drain_for(
        self, seat: int, want: str | set[str], timeout: float
    ) -> dict | None:
        """Wait up to ``timeout`` for a matching action from the seat's human, else None."""
        wanted = {want} if isinstance(want, str) else set(want)
        deadline = timeout
        try:
            while deadline > 0:
                loop = asyncio.get_event_loop()
                t0 = loop.time()
                action_task = asyncio.create_task(self._queue.get())
                control_task = asyncio.create_task(self._controls.get())
                done, pending = await asyncio.wait(
                    {action_task, control_task},
                    timeout=deadline,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if not done:
                    return None
                deadline -= loop.time() - t0
                if control_task in done:
                    await self._execute_control(control_task.result())
                    if action_task in done:
                        # Preserve a simultaneous player action for the next drain.
                        self.submit(*action_task.result())
                    continue
                user_id, msg = action_task.result()
                if self.seat_user.get(seat) == user_id and msg.get("type") in wanted:
                    return msg
                # ignore stale/foreign input and keep waiting
        except asyncio.TimeoutError:
            return None
        return None

    # ---- the driver -------------------------------------------------------
    async def _run(self) -> None:
        try:
            # snapshot admin-tunable config once for this match's lifetime
            try:
                self.cfg = await runtime_config()
            except Exception:  # noqa: BLE001 — fall back to defaults if the store is unreachable
                logger.exception("could not load runtime config for %s; using defaults", self.code)
            try:
                await self._load_scores()
            except Exception:  # noqa: BLE001 — a score reload must not stop the match
                logger.exception("could not reload scores for %s", self.code)
            await self._broadcast()
            while self.state.phase is not Phase.FINISHED:
                while await self._execute_control():
                    pass
                # A viewer-less human match pauses instead of advancing timeouts or
                # being abandoned. Its durable state remains resumable indefinitely.
                if not self.is_bot_table and not self._human_watching():
                    if self._persist_dirty or self._has_event_buffers():
                        await self._persist()
                    self._deadline = None
                    self._viewer_event.clear()
                    # A late-join control may have set the event before this clear.
                    if self._human_watching() or not self._controls.empty():
                        continue
                    await self._viewer_event.wait()
                    await self._broadcast()
                    continue
                await self._step()
                self._steps_since_persist += 1
                self._persist_dirty = True
                if self._should_persist():
                    await self._persist()
            # final board + stats flush before settle payouts
            if self._persist_dirty or self._has_event_buffers():
                await self._persist()
            await self._settle()
            await self._broadcast()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("runtime crashed for match %s", self.code)

    def _has_event_buffers(self) -> bool:
        return bool(self.buf_rolls or self.buf_knocks or self.buf_cards)

    async def _wait_move_or_reroll(self, seat: int):
        """Accept move / reroll / insurance / hint during the move window."""
        remaining = float(self.cfg["TURN_TIMEOUT_SECONDS"])
        loop = asyncio.get_event_loop()
        end = loop.time() + remaining
        uid = self._human_uid(seat)
        while True:
            left = end - loop.time()
            if left <= 0:
                return None
            got = await self._drain_for(
                seat, {"move", "reroll", "insurance", "hint"}, left
            )
            if got is None:
                return None
            mtype = got.get("type")
            if mtype == "reroll":
                if await self._try_reroll(seat):
                    self._deadline = end
                    await self._broadcast()
                continue
            if mtype == "insurance" and uid is not None:
                await self._try_insurance(uid)
                await self._broadcast()
                continue
            if mtype == "hint" and uid is not None:
                await self._try_hint(seat, uid)
                await self._broadcast()
                continue
            moves = legal_moves(self.state)
            idx = got.get("token_index")
            return next((m for m in moves if m.token_index == idx), None)

    async def _try_insurance(self, uid: int) -> bool:
        if uid in self._insured:
            return False
        cost = max(0, int(self.cfg.get("INSURANCE_COST", 40)))
        if not await self._coin_debit(uid, cost, "insurance"):
            return False
        self._insured.add(uid)
        await hub.broadcast(
            self.code, {"type": "insured", "user_id": uid, "payout": int(self.cfg.get("INSURANCE_PAYOUT", 30))}
        )
        return True

    async def _try_hint(self, seat: int, uid: int) -> bool:
        if self._hint_token is not None:
            return False
        cost = max(0, int(self.cfg.get("HINT_COST", 30)))
        if not await self._coin_debit(uid, cost, "hint", {"seat": seat}):
            return False
        moves = legal_moves(self.state)
        if not moves:
            return False
        chosen = choose_move(self.state, moves, self._rng)
        self._hint_token = chosen.token_index if chosen else moves[0].token_index
        await hub.broadcast(
            self.code,
            {"type": "hint", "user_id": uid, "token_index": self._hint_token},
        )
        return True

    async def buy_bonus_card(self, user_id: int) -> bool:
        """Spend coins to draw a fantasy card (could've shortcut)."""
        if self._card is not None or self.state.phase is Phase.FINISHED:
            return False
        seat = next((s for s, u in self.seat_user.items() if u == user_id), None)
        if seat is None or seat in self.removed_seats:
            return False
        cost = max(0, int(self.cfg.get("BUY_CARD_COST", 100)))
        if not await self._coin_debit(user_id, cost, "buy_card"):
            return False
        # Run the draw as a nested sequence (safe when called from the driver loop).
        await self._draw_card(seat, self.state.turn, reason="bought")
        return True

    async def _try_reroll(self, seat: int) -> bool:
        """Debit REROLL_COST and replace the current die. At most once per turn."""
        if self._rerolled_this_turn or self.state.phase is not Phase.MOVE:
            return False
        uid = self._human_uid(seat)
        if uid is None:
            return False
        cost = max(0, int(self.cfg.get("REROLL_COST", 50)))
        if cost > 0 and not await self._coin_debit(
            uid, cost, "reroll", {"seat": seat, "turn": self.state.turn}
        ):
            return False

        # A reroll changes the face used for the move. It does not erase the roll
        # already saved: the scoreboard counts every face, same as dice_rolls.
        # register_roll() only accepts the ROLL phase, and this seat is already in MOVE.
        face = roll_die(self._rng)
        self.state.roll_face = face
        self.state.die = face
        if face == 6:
            self.state.consecutive_sixes += 1
        else:
            self.state.consecutive_sixes = 0
        self._note_roll(seat, face, uid)
        self._rerolled_this_turn = True
        self._hint_token = None
        await asyncio.sleep(settings.ROLL_REVEAL_SECONDS)
        return True

    async def _coin_debit(self, uid: int, amount: int, reason: str, meta: dict | None = None) -> bool:
        if amount <= 0:
            return True
        try:
            async with SessionLocal() as session:
                await ledger.debit(
                    session, uid, amount, reason=reason,
                    ref_type="match", ref_id=self.match_id, meta=meta,
                )
                await session.commit()
            return True
        except InsufficientFunds:
            await hub.broadcast(
                self.code, {"type": "error", "code": "insufficient_coins", "user_id": uid}
            )
            return False
        except Exception:  # noqa: BLE001
            logger.exception("coin debit failed match=%s user=%s reason=%s", self.code, uid, reason)
            return False

    async def _coin_credit(self, uid: int, amount: int, reason: str, meta: dict | None = None) -> None:
        if amount <= 0:
            return
        try:
            async with SessionLocal() as session:
                await ledger.credit(
                    session, uid, amount, reason=reason,
                    ref_type="match", ref_id=self.match_id, meta=meta,
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("coin credit failed match=%s user=%s", self.code, uid)

    def _note_roll(self, seat: int, face: int, uid: int | None) -> None:
        """Count a face in the live score and, for a human, queue it for the roll log."""
        self.seat_last_die[seat] = face
        hist = self.game_dice.setdefault(seat, {})
        hist[face] = hist.get(face, 0) + 1
        if uid is None:
            return
        lifetime = self.stat_dice.setdefault(uid, {})
        lifetime[face] = lifetime.get(face, 0) + 1
        self.buf_rolls.append({
            "match_id": self.match_id, "user_id": uid, "seat": seat,
            "value": face, "turn": self.state.turn,
        })

    async def _load_scores(self) -> None:
        """Rebuild the scoreboard from saved rolls so a restart cannot drop a face."""
        async with SessionLocal() as session:
            rolls = (
                await session.execute(
                    select(DiceRoll.seat, DiceRoll.value).where(
                        DiceRoll.match_id == self.match_id
                    )
                )
            ).all()
            knocks = (
                await session.execute(
                    select(
                        Knockout.attacker_seat,
                        Knockout.victim_seat,
                        Knockout.taken,
                    ).where(Knockout.match_id == self.match_id)
                )
            ).all()
        self.game_dice = analytics.fold_face_counts([(s, v) for s, v in rolls])
        for row in self.buf_rolls:
            hist = self.game_dice.setdefault(row["seat"], {})
            hist[row["value"]] = hist.get(row["value"], 0) + 1
        self.game_dealt = {}
        self.game_taken = {}
        self.game_potential = {}
        for attacker, victim, taken in knocks:
            if taken:
                self.game_dealt[attacker] = self.game_dealt.get(attacker, 0) + 1
                self.game_taken[victim] = self.game_taken.get(victim, 0) + 1
            else:
                self.game_potential[attacker] = self.game_potential.get(attacker, 0) + 1
        for row in self.buf_knocks:
            if row.get("taken"):
                seat = row["attacker_seat"]
                self.game_dealt[seat] = self.game_dealt.get(seat, 0) + 1
                victim = row["victim_seat"]
                self.game_taken[victim] = self.game_taken.get(victim, 0) + 1
            else:
                seat = row["attacker_seat"]
                self.game_potential[seat] = self.game_potential.get(seat, 0) + 1

    def _should_persist(self) -> bool:
        """Persist on a cadence, when event buffers grow, or when the game ends."""
        # Human rooms are recoverable after a backend restart. Persist every completed
        # transition so the durable board never trails several moves behind.
        if not self.is_bot_table:
            return True
        n = max(1, settings.PERSIST_EVERY_N_STEPS)
        if self.state.phase is Phase.FINISHED:
            return True
        if self._steps_since_persist >= n:
            return True
        # keep event inserts from growing unbounded between flushes
        if len(self.buf_rolls) + len(self.buf_knocks) + len(self.buf_cards) >= 24:
            return True
        return False

    async def _step(self) -> None:
        seat = self.state.current
        bot_seat = self._is_bot_seat(seat)

        # Throttle bot-only tables when nobody is watching (RAM/CPU on a 1GB box).
        if bot_seat and not self._human_watching():
            await asyncio.sleep(settings.BOT_TABLE_IDLE_SECONDS)

        if self.state.phase is Phase.ROLL:
            self._hint_token = None
            if not bot_seat:
                self._deadline = time.time() + self.cfg["TURN_TIMEOUT_SECONDS"]
                await self._broadcast()  # push the countdown to the client
                uid = self._human_uid(seat)
                remaining = float(self.cfg["TURN_TIMEOUT_SECONDS"])
                loop = asyncio.get_event_loop()
                end = loop.time() + remaining
                got = None
                while True:
                    left = end - loop.time()
                    if left <= 0:
                        break
                    got = await self._drain_for(
                        seat, {"roll", "buy_card", "insurance"}, left
                    )
                    if got is None:
                        break
                    if got.get("type") == "buy_card" and uid is not None:
                        await self.buy_bonus_card(uid)
                        self._deadline = end
                        await self._broadcast()
                        continue
                    if got.get("type") == "insurance" and uid is not None:
                        await self._try_insurance(uid)
                        await self._broadcast()
                        continue
                    break
                self._deadline = None
                if got is None or got.get("type") != "roll":
                    self.seat_missed[seat] = self.seat_missed.get(seat, 0) + 1
                    logger.info(
                        "seat %s timed out on roll (%s), missed=%d",
                        seat, self.code, self.seat_missed[seat],
                    )
                    if self.seat_missed[seat] >= self.cfg["MAX_MISSED_TURNS"]:
                        await self._kick(seat)
                else:
                    self.seat_missed[seat] = 0
            else:
                await self._think()
            register_roll(self.state, roll_die(self._rng))
            # Twin Dice: multiply MOVEMENT (face/six logic keep the real face)
            if self.state.eff("double", seat) > 0 and self.state.roll_face is not None:
                mult = self.state.eff("double_mult", seat) or 2
                self.state.die = self.state.roll_face * max(2, mult)
                self.state.add_eff("double", seat, -1)
                if self.state.eff("double", seat) <= 0:
                    self.state.set_eff("double_mult", seat, 0)
            if self.state.roll_face is not None:
                self._note_roll(seat, self.state.roll_face, self._human_uid(seat))
            self._rerolled_this_turn = False
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
                self._deadline = time.time() + self.cfg["TURN_TIMEOUT_SECONDS"]
                await self._broadcast()  # push the countdown to the client
                chosen = await self._wait_move_or_reroll(seat)
                self._deadline = None
                moves = legal_moves(self.state)  # may have changed after a paid reroll
            else:
                await self._think()
            if chosen is None:
                chosen = choose_move(self.state, moves, self._rng)
            # Every opponent token that COULD be captured on this move (across all legal
            # moves), captured here before the state advances.
            targets = {cap for m in moves for cap in m.captures}
            turn_no = self.state.turn
            mover = self._human_uid(seat)
            res = apply_move(self.state, chosen)
            if res.captured:
                # actual knocks
                self.game_dealt[seat] = self.game_dealt.get(seat, 0) + len(res.captured)
                if mover is not None:
                    self.stat_dealt[mover] = self.stat_dealt.get(mover, 0) + len(res.captured)
                for cseat, _tok in res.captured:
                    self.game_taken[cseat] = self.game_taken.get(cseat, 0) + 1
                    victim = self._human_uid(cseat)
                    if victim is not None:
                        self.stat_taken[victim] = self.stat_taken.get(victim, 0) + 1
                        if victim in self._insured:
                            self._insured.discard(victim)
                            pay = max(0, int(self.cfg.get("INSURANCE_PAYOUT", 30)))
                            if pay:
                                self.stat_insurance[victim] = (
                                    self.stat_insurance.get(victim, 0) + pay
                                )
                    if mover is not None:
                        self.buf_knocks.append({
                            "match_id": self.match_id, "turn": turn_no,
                            "attacker_user_id": mover, "attacker_seat": seat,
                            "victim_user_id": victim, "victim_seat": cseat, "taken": True,
                        })
            couldve_due = False
            if mover is not None and not res.captured and targets:
                # a capture was legal but the player did something else — potential knocks
                n = len(targets)
                self.game_potential[seat] = self.game_potential.get(seat, 0) + n
                self.stat_potential[mover] = self.stat_potential.get(mover, 0) + n
                pay = max(0, int(self.cfg.get("PAY_COULDVE", 10))) * n
                if pay:
                    self.stat_couldve_coins[mover] = self.stat_couldve_coins.get(mover, 0) + pay
                for vseat, _tok in targets:
                    self.buf_knocks.append({
                        "match_id": self.match_id, "turn": turn_no,
                        "attacker_user_id": mover, "attacker_seat": seat,
                        "victim_user_id": self._human_uid(vseat), "victim_seat": vseat,
                        "taken": False,
                    })
                # every 3rd could've in a game earns a card draw
                due = self.game_potential[seat] // self.cfg["COULDVE_PER_CARD"]
                if due > self.pot_reward.get(seat, 0):
                    self.pot_reward[seat] = due
                    couldve_due = True
            await self._broadcast()
            await asyncio.sleep(settings.MOVE_SETTLE_SECONDS)  # let the glide finish

            # Bringing a token home earns a fantasy-card draw (humans only) — the reward
            # that replaced the old "reach home = extra roll". No draw once the game is over.
            if res.reached_home and mover is not None and self.state.phase is not Phase.FINISHED:
                if self.state.players[seat].all_home():
                    # this was their LAST token — they're done, so a self-buff is wasted.
                    # Only offer disruptive cards (that hit rivals); skip if none apply.
                    await self._draw_card(seat, turn_no, pool=self._OFFENSIVE, reason="home")
                else:
                    await self._draw_card(seat, turn_no, reason="home")

            # …and passing up 3 knocks in a game earns a consolation card draw
            if couldve_due and self.state.phase is not Phase.FINISHED:
                await self._draw_card(seat, turn_no, reason="couldve")

    async def _draw_card(
        self, seat: int, turn_no: int, pool: frozenset[str] | None = None, reason: str = "home"
    ) -> None:
        """Offer four cards; optional peek/boost spend; then target + apply."""
        uid = self._human_uid(seat)
        if uid is None:
            return
        effects = await card_catalog.enabled_effects()
        ids = [cid for cid in effects if pool is None or effects[cid] in pool]
        if len(ids) < 4:
            return
        options = self._rng.sample(ids, 4)
        peeked = False

        self._card = {
            "seat": seat, "stage": "pick", "reason": reason,
            "peek_cost": int(self.cfg.get("PEEK_COST", 25)),
        }
        self._deadline = time.time() + self.cfg["CARD_PICK_SECONDS"]
        await self._broadcast()

        # pick or peek
        remaining = float(self.cfg["CARD_PICK_SECONDS"])
        loop = asyncio.get_event_loop()
        end = loop.time() + remaining
        got = None
        while True:
            left = end - loop.time()
            if left <= 0:
                break
            got = await self._drain_for(seat, {"pick_card", "peek"}, left)
            if got is None:
                break
            if got.get("type") == "peek" and not peeked:
                cost = max(0, int(self.cfg.get("PEEK_COST", 25)))
                if await self._coin_debit(uid, cost, "peek"):
                    peeked = True
                    self._card = {
                        **self._card,
                        "peeked": True,
                        "options": options,
                    }
                    self._deadline = end
                    await self._broadcast()
                continue
            break
        self._deadline = None

        if got is not None and got.get("type") == "pick_card":
            try:
                idx = int(got.get("index", 0))
            except (TypeError, ValueError):
                idx = 0
        else:
            idx = self._rng.randint(0, 3)
        idx = max(0, min(3, idx))
        picked = options[idx]
        effect = effects[picked]

        self._card = {
            "seat": seat, "stage": "reveal", "options": options,
            "picked": idx, "reason": reason,
        }
        await self._broadcast()
        await asyncio.sleep(settings.CARD_REVEAL_SECONDS)

        target: int | None = None
        targets = self._valid_targets(seat, effect)
        if targets:
            if len(targets) == 1:
                target = targets[0]
            else:
                self._card = {
                    "seat": seat, "stage": "target", "options": options,
                    "picked": idx, "targets": targets, "reason": reason,
                }
                self._deadline = time.time() + self.cfg["CARD_PICK_SECONDS"]
                await self._broadcast()
                got = await self._drain_for(seat, "pick_target", self.cfg["CARD_PICK_SECONDS"])
                self._deadline = None
                chosen = None
                if got is not None:
                    try:
                        chosen = int(got.get("seat"))
                    except (TypeError, ValueError):
                        chosen = None
                target = chosen if chosen in targets else self._leading_among(seat, targets)

        # optional coin boost
        boost_id: str | None = None
        boosts = boost_options_for(effect, self.cfg)
        if boosts:
            self._card = {
                "seat": seat, "stage": "boost", "options": options,
                "picked": idx, "target": target, "reason": reason,
                "boosts": boosts,
            }
            self._deadline = time.time() + self.cfg["CARD_PICK_SECONDS"]
            await self._broadcast()
            got = await self._drain_for(
                seat, {"boost_card", "skip_boost"}, self.cfg["CARD_PICK_SECONDS"]
            )
            self._deadline = None
            if got and got.get("type") == "boost_card":
                bid = str(got.get("boost_id") or "")
                cost = boost_cost(effect, bid, self.cfg)
                if bid and cost >= 0 and await self._coin_debit(
                    uid, cost, "card_boost", {"card": picked, "boost_id": bid}
                ):
                    boost_id = bid

        params = boost_params(effect, boost_id)

        self._card = {
            "seat": seat, "stage": "result", "options": options,
            "picked": idx, "target": target, "reason": reason,
            "boost_id": boost_id,
        }
        await self._broadcast()
        await asyncio.sleep(settings.CARD_RESULT_DELAY)

        affected = self._apply_card(seat, effect, target, params)
        self.buf_cards.append({
            "match_id": self.match_id, "user_id": uid, "seat": seat,
            "options": options, "picked": picked, "turn": turn_no,
        })
        if affected is not None and affected != target:
            self._card = {**self._card, "target": affected}
        await self._broadcast()
        await asyncio.sleep(settings.CARD_REVEAL_SECONDS)
        self._card = None
        await self._broadcast()

    # cards whose effect lands on a chosen opponent
    _TARGETING = frozenset({"lock", "lock2", "recall", "swap"})
    # disruptive cards a finished player may still play (no self-benefit, hits rivals)
    _OFFENSIVE = frozenset({"lock", "lock2", "recall", "toll"})

    def _valid_targets(self, seat: int, effect: str) -> list[int] | None:
        """Seats a targeting card may hit, or None if the card isn't targeted. [] means
        targeted but nobody's eligible (the effect will simply no-op)."""
        if effect == "mirror":
            mirrored = next((e for s, e in reversed(self._recent_cards) if s != seat), None)
            return self._valid_targets(seat, mirrored) if mirrored else None
        if effect not in self._TARGETING:
            return None
        opps = [s for s, p in enumerate(self.state.players) if s != seat and not p.all_home()]
        if effect in ("recall", "swap"):
            opps = [s for s in opps if self._ring_tokens(s)]
            if effect == "swap" and not self._ring_tokens(seat):
                return []
        return opps

    def _leading_among(self, seat: int, targets: list[int]) -> int | None:
        best, score = None, -1
        for s in targets:
            tot = sum(t for t in self.state.players[s].tokens if t >= 0)
            if tot > score:
                best, score = s, tot
        return best

    # ---- fantasy-card effects --------------------------------------------
    def _apply_card(
        self,
        seat: int,
        effect: str,
        target: int | None = None,
        boost: dict | None = None,
    ) -> int | None:
        """Apply a drawn card's effect. ``boost`` holds paid upgrade params."""
        boost = boost or {}
        if effect != "mirror":
            self._recent_cards.append((seat, effect))
        st = self.state

        if effect == "extra_roll":
            self._grant_turn(seat)
        elif effect == "active_stars":
            c = st.players[seat].color.value
            if c not in st.active_stars:
                st.active_stars.append(c)
        elif effect == "shield":
            st.set_eff("shield", seat, int(boost.get("rounds", 3)))
        elif effect == "shield_all":
            st.set_eff("shield", seat, int(boost.get("rounds", 5)))
        elif effect == "double_dice":
            charges = int(boost.get("charges", 2))
            mult = int(boost.get("mult", 2))
            st.set_eff("double", seat, charges)
            st.set_eff("double_mult", seat, max(2, mult))
        elif effect == "second_chance":
            st.set_eff("second_chance", seat, 1)
        elif effect == "toll":
            st.set_eff("toll", seat, 2)
        elif effect == "steal_turn":
            self._grant_turn(seat)
        elif effect == "boost":
            self._advance_own_token(seat, int(boost.get("steps", 3)))
        elif effect == "summon":
            for _ in range(max(1, int(boost.get("count", 1)))):
                self._release_from_base(seat)
        elif effect == "teleport":
            for _ in range(max(1, int(boost.get("warps", 1)))):
                self._warp_to_next_star(seat)
        elif effect == "coins":
            uid = self._human_uid(seat)
            if uid is not None:
                self.stat_coins[uid] = self.stat_coins.get(uid, 0) + int(boost.get("coins", 150))
        elif effect in ("lock", "lock2"):
            tgt = target if target is not None else self._leading_rival(seat)
            if tgt is not None:
                st.add_eff("skip", tgt, 1 if effect == "lock" else 2)
            return tgt
        elif effect == "recall":
            tgt = target if target is not None else self._leading_rival(seat)
            if tgt is not None:
                self._recall_seat(tgt, int(boost.get("steps", 4)))
            return tgt
        elif effect == "swap":
            tgt = target if target is not None else self._leading_rival(seat)
            if tgt is not None:
                self._swap_seats(seat, tgt)
            return tgt
        elif effect == "mirror":
            mirrored = next(
                (eff for s, eff in reversed(self._recent_cards) if s != seat), None
            )
            if mirrored:
                return self._apply_card(seat, mirrored, target, boost)
        return None

    def _grant_turn(self, seat: int) -> None:
        st = self.state
        if st.phase is not Phase.FINISHED and not st.players[seat].all_home():
            st.current = seat
            st.phase = Phase.ROLL
            st.die = None
            st.roll_face = None
            st.consecutive_sixes = 0

    def _ring_tokens(self, seat: int) -> list[int]:
        """Token indices of a seat that are on the shared ring (0..50), lead first."""
        toks = [(i, p) for i, p in enumerate(self.state.players[seat].tokens) if 0 <= p <= 50]
        toks.sort(key=lambda t: t[1], reverse=True)
        return [i for i, _ in toks]

    def _leading_rival(self, seat: int) -> int | None:
        """The active opponent with the most total progress (their tokens summed)."""
        best, best_score = None, -1
        for s, p in enumerate(self.state.players):
            if s == seat or p.all_home():
                continue
            score = sum(t for t in p.tokens if t >= 0)
            if score > best_score:
                best, best_score = s, score
        return best

    def _capture_at(self, seat: int, prog: int) -> None:
        """Send any capturable opponents sharing ``seat``'s token square back to base
        (respects shields / safe squares via the engine's capture rule)."""
        color = self.state.players[seat].color
        for vseat, vtok in _captures_at(self.state, color, prog):
            if self.state.eff("second_chance", vseat) > 0:
                self.state.set_eff("second_chance", vseat, 0)
                continue
            self.state.players[vseat].tokens[vtok] = BASE
            self.game_dealt[seat] = self.game_dealt.get(seat, 0) + 1
            mover = self._human_uid(seat)
            if mover is not None:
                self.stat_dealt[mover] = self.stat_dealt.get(mover, 0) + 1
            self.game_taken[vseat] = self.game_taken.get(vseat, 0) + 1
            vuid = self._human_uid(vseat)
            if vuid is not None:
                self.stat_taken[vuid] = self.stat_taken.get(vuid, 0) + 1
                if vuid in self._insured:
                    self._insured.discard(vuid)
                    pay = max(0, int(self.cfg.get("INSURANCE_PAYOUT", 30)))
                    if pay:
                        self.stat_insurance[vuid] = self.stat_insurance.get(vuid, 0) + pay

    def _advance_own_token(self, seat: int, steps: int) -> None:
        for i in self._ring_tokens(seat):
            dst = self.state.players[seat].tokens[i] + steps
            if dst <= HOME:
                self.state.players[seat].tokens[i] = dst
                if dst <= 50:
                    self._capture_at(seat, dst)
                return

    def _release_from_base(self, seat: int) -> None:
        for i, p in enumerate(self.state.players[seat].tokens):
            if p == BASE:
                self.state.players[seat].tokens[i] = 0
                self._capture_at(seat, 0)
                return

    def _warp_to_next_star(self, seat: int) -> None:
        # jump the lead ring token forward to the next star square (own start/neutral) ahead
        from app.ludo.board import START_OFFSET, NEUTRAL_STARS
        color = self.state.players[seat].color
        star_progs = sorted(
            {(sq - START_OFFSET[color]) % 52 for sq in list(START_OFFSET.values()) + list(NEUTRAL_STARS)}
        )
        for i in self._ring_tokens(seat):
            prog = self.state.players[seat].tokens[i]
            ahead = next((sp for sp in star_progs if 0 < sp <= 50 and sp > prog), None)
            if ahead is not None:
                self.state.players[seat].tokens[i] = ahead
                self._capture_at(seat, ahead)
                return

    def _recall_seat(self, rival: int, steps: int) -> None:
        """Send a rival's lead ring token back ``steps`` (floored at their start)."""
        for i in self._ring_tokens(rival):
            self.state.players[rival].tokens[i] = max(0, self.state.players[rival].tokens[i] - steps)
            return

    def _swap_seats(self, seat: int, rival: int) -> None:
        """Swap the board positions of the drawer's lead ring token and the rival's."""
        from app.ludo.board import START_OFFSET
        mine = self._ring_tokens(seat)
        theirs = self._ring_tokens(rival)
        if not mine or not theirs:
            return
        mi, ti = mine[0], theirs[0]
        my_color, rv_color = self.state.players[seat].color, self.state.players[rival].color
        my_abs = (START_OFFSET[my_color] + self.state.players[seat].tokens[mi]) % 52
        rv_abs = (START_OFFSET[rv_color] + self.state.players[rival].tokens[ti]) % 52
        new_mine = (rv_abs - START_OFFSET[my_color]) % 52
        new_rival = (my_abs - START_OFFSET[rv_color]) % 52
        if 0 <= new_mine <= 50 and 0 <= new_rival <= 50:
            self.state.players[seat].tokens[mi] = new_mine
            self.state.players[rival].tokens[ti] = new_rival

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
            "seat_potential": {str(k): v for k, v in self.game_potential.items()},
            "card": self._card,   # in-progress fantasy-card draw (None when idle)
            "removed_seats": sorted(self.removed_seats),
            "legal_moves": [m.to_dict() for m in legal_moves(self.state)],
            # turn clock: client shows a countdown from `now` to `deadline` (unix secs)
            "deadline": self._deadline,
            "now": time.time(),
            "turn_seconds": self.cfg["TURN_TIMEOUT_SECONDS"],
            "reroll_cost": int(self.cfg.get("REROLL_COST", 50)),
            "can_reroll": (
                self.state.phase is Phase.MOVE
                and not self._rerolled_this_turn
            ),
            "peek_cost": int(self.cfg.get("PEEK_COST", 25)),
            "insurance_cost": int(self.cfg.get("INSURANCE_COST", 40)),
            "insurance_payout": int(self.cfg.get("INSURANCE_PAYOUT", 30)),
            "hint_cost": int(self.cfg.get("HINT_COST", 30)),
            "buy_card_cost": int(self.cfg.get("BUY_CARD_COST", 100)),
            "insured": sorted(self._insured),
            "hint_token": self._hint_token,
            "can_insurance": self.state.phase in (Phase.ROLL, Phase.MOVE),
            "can_hint": self.state.phase is Phase.MOVE and self._hint_token is None,
            "can_buy_card": self._card is None and self.state.phase is not Phase.FINISHED,
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
            # Same transaction as the outbox row, so a roll cannot be saved
            # without a ClickHouse copy queued behind it.
            stamped = datetime.now(timezone.utc)
            if self.buf_rolls:
                roll_objs = [DiceRoll(**row, created_at=stamped) for row in self.buf_rolls]
                session.add_all(roll_objs)
            else:
                roll_objs = []
            if self.buf_knocks:
                knock_objs = [Knockout(**row, created_at=stamped) for row in self.buf_knocks]
                session.add_all(knock_objs)
            else:
                knock_objs = []
            if self.buf_cards:
                await session.execute(insert(CardDraw), self.buf_cards)
                self.buf_cards = []
            if roll_objs or knock_objs:
                await session.flush()
                analytics.queue_rolls(session, roll_objs)
                analytics.queue_knocks(session, knock_objs)
            # Fold dice/capture deltas into lifetime totals in the same commit.
            await self._flush_stats(session)
            # Late joining closes for good once anyone has finished.
            if self.state.ranking:
                await self._expire_pending(session)
            await session.commit()
            self.buf_rolls = []
            self.buf_knocks = []
        self._steps_since_persist = 0
        self._persist_dirty = False

    async def _expire_pending(self, session) -> None:
        now = datetime.now(timezone.utc)
        await session.execute(
            update(MatchJoinRequest)
            .where(
                MatchJoinRequest.match_id == self.match_id,
                MatchJoinRequest.status == "pending",
            )
            .values(status="expired", resolved_at=now)
        )

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
        if self._persist_dirty or self._has_event_buffers():
            await self._persist()
        async with SessionLocal() as session:
            m = await session.get(Match, self.match_id)
            if m is not None and m.status is not MatchStatus.FINISHED:
                m.status = MatchStatus.ABANDONED
            await self._flush_stats(session)
            await self._expire_pending(session)
            await session.commit()
        logger.info("match %s abandoned (no viewers)", self.code)

    async def claim_rematch(self, user_id: int, create) -> tuple[str | None, list[int], list[int]]:
        """Record a rematch vote. Returns (ready_code|None, votes, human_ids).

        ``create`` is an async callable ``() -> Match`` used at most once per runtime
        so concurrent WS rematch messages cannot spawn duplicate matches.
        """
        async with self._rematch_lock:
            human_ids = sorted({uid for uid in self.seat_user.values() if uid})
            if self.rematch_code:
                return self.rematch_code, sorted(self.rematch), human_ids
            self.rematch.add(user_id)
            votes = sorted(self.rematch)
            if human_ids and self.rematch.issuperset(set(human_ids)):
                new_match = await create()
                self.rematch_code = new_match.code
                return self.rematch_code, votes, human_ids
            return None, votes, human_ids

    async def _flush_stats(self, session) -> None:
        """Fold the dice + capture deltas accumulated SINCE THE LAST FLUSH into each
        human's lifetime totals, then clear them. Called from _persist after every step
        (so profiles update live) and once more on abandon; clearing makes it safe to call
        repeatedly without double-counting.

        ``dice_hist`` is JSONB keyed by the string faces "1".."6"; we reassign the dict so
        SQLAlchemy detects the change (in-place JSONB mutation isn't tracked).
        """
        uids = (
            set(self.stat_dice) | set(self.stat_dealt) | set(self.stat_taken)
            | set(self.stat_potential) | set(self.stat_coins)
            | set(self.stat_couldve_coins) | set(self.stat_insurance)
        )
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
            user.potential_knocks = (user.potential_knocks or 0) + self.stat_potential.get(uid, 0)
            if self.stat_coins.get(uid):
                await ledger.credit(
                    session,
                    uid,
                    int(self.stat_coins[uid]),
                    reason="jackpot_card",
                    ref_type="match",
                    ref_id=self.match_id,
                )
            if self.stat_couldve_coins.get(uid):
                await ledger.credit(
                    session,
                    uid,
                    int(self.stat_couldve_coins[uid]),
                    reason="couldve",
                    ref_type="match",
                    ref_id=self.match_id,
                )
            if self.stat_insurance.get(uid):
                await ledger.credit(
                    session,
                    uid,
                    int(self.stat_insurance[uid]),
                    reason="insurance_payout",
                    ref_type="match",
                    ref_id=self.match_id,
                )

        # deltas are now persisted (within this session's pending commit) — reset them so
        # the next flush only applies what's new.
        self.stat_dice = {}
        self.stat_dealt = {}
        self.stat_taken = {}
        self.stat_potential = {}
        self.stat_coins = {}
        self.stat_couldve_coins = {}
        self.stat_insurance = {}

    async def _settle(self) -> None:
        """Write final placements, pay placement rewards via ledger, bump stats."""
        from app.models import User

        human_count = sum(1 for uid in self.seat_user.values() if uid)
        async with SessionLocal() as session:
            m = await session.get(Match, self.match_id)
            if m is None:
                return
            m.status = MatchStatus.FINISHED
            m.state = self.state.to_dict()

            # Optional entry-fee pot still goes to 1st human when fees were collected.
            pot = self.entry_fee * human_count if self.entry_fee else 0

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
                    user.xp += 100
                    if pot:
                        await ledger.credit(
                            session,
                            uid,
                            pot,
                            reason="entry_fee",
                            ref_type="match",
                            ref_id=self.match_id,
                            meta={"kind": "pot_win"},
                        )
                else:
                    user.xp += 25
                pay = placement_payout(self.cfg, place, human_count)
                if pay:
                    await ledger.credit(
                        session,
                        uid,
                        pay,
                        reason="placement",
                        ref_type="match",
                        ref_id=self.match_id,
                        meta={"place": place, "humans": human_count},
                    )
            await self._expire_pending(session)
            await session.commit()
        logger.info("match %s finished, ranking=%s", self.code, self.state.ranking)
