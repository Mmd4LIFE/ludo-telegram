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
from app.ludo.board import BASE, HOME, TOKENS_PER_PLAYER, Color
from app.ludo.bots import choose_move
from app.ludo.rules import _captures_at
from app.game.connection import hub
from app.models import Match, MatchStatus, DiceRoll, Knockout, Card, CardDraw
from sqlalchemy import insert, select

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
        self.stat_potential: dict[int, int] = {}         # uid -> captures they passed up

        # THIS game's running tally, keyed by SEAT (bots included) — never cleared, so it's
        # the source for the in-game scoreboard broadcast to viewers.
        self.game_dice: dict[int, dict[int, int]] = {}   # seat -> {face: count}
        self.game_dealt: dict[int, int] = {}             # seat -> captures dealt
        self.game_taken: dict[int, int] = {}             # seat -> captures suffered
        self.game_potential: dict[int, int] = {}         # seat -> captures passed up

        # append-only event rows buffered per step, bulk-inserted in _persist (humans only)
        self.buf_rolls: list[dict] = []
        self.buf_knocks: list[dict] = []
        self.buf_cards: list[dict] = []
        # the in-progress fantasy-card draw, surfaced to the client (None when idle)
        self._card: dict | None = None
        # (seat, effect) of each card played this game — for Mirror (copy an opponent's)
        self._recent_cards: list[tuple[int, str]] = []
        self.stat_coins: dict[int, int] = {}   # uid -> bonus coins to grant (Jackpot)

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
            # Twin Dice: double the MOVEMENT of this roll (face/six logic keep the real face)
            if self.state.eff("double", seat) > 0 and self.state.roll_face is not None:
                self.state.die = self.state.roll_face * 2
                self.state.add_eff("double", seat, -1)
            if self.state.roll_face is not None:
                face = self.state.roll_face
                self.seat_last_die[seat] = face          # show the true face, not the doubled value
                ghist = self.game_dice.setdefault(seat, {})
                ghist[face] = ghist.get(face, 0) + 1
                uid = self._human_uid(seat)
                if uid is not None:
                    hist = self.stat_dice.setdefault(uid, {})
                    hist[face] = hist.get(face, 0) + 1
                    self.buf_rolls.append({
                        "match_id": self.match_id, "user_id": uid, "seat": seat,
                        "value": face, "turn": self.state.turn,
                    })
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
                    if mover is not None:
                        self.buf_knocks.append({
                            "match_id": self.match_id, "turn": turn_no,
                            "attacker_user_id": mover, "attacker_seat": seat,
                            "victim_user_id": victim, "victim_seat": cseat, "taken": True,
                        })
            elif mover is not None and targets:
                # a capture was legal but the player did something else — potential knocks
                self.game_potential[seat] = self.game_potential.get(seat, 0) + len(targets)
                self.stat_potential[mover] = self.stat_potential.get(mover, 0) + len(targets)
                for vseat, _tok in targets:
                    self.buf_knocks.append({
                        "match_id": self.match_id, "turn": turn_no,
                        "attacker_user_id": mover, "attacker_seat": seat,
                        "victim_user_id": self._human_uid(vseat), "victim_seat": vseat,
                        "taken": False,
                    })
            await self._broadcast()
            await asyncio.sleep(settings.MOVE_SETTLE_SECONDS)  # let the glide finish

            # Bringing a token home earns a fantasy-card draw (humans only) — the reward
            # that replaced the old "reach home = extra roll".
            if res.reached_home and mover is not None:
                await self._draw_card(seat, turn_no)

    async def _draw_card(self, seat: int, turn_no: int) -> None:
        """Offer four random cards face-down, wait for the player's pick (auto-pick on
        timeout), reveal all four, apply the pick, and log the draw."""
        uid = self._human_uid(seat)
        if uid is None:
            return
        # the catalog lives in the DB (seeded by migration) — read the enabled ids + effects
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Card.id, Card.effect).where(Card.enabled.is_(True))
                )
            ).all()
        effects = {cid: eff for cid, eff in rows}
        if len(effects) < 4:
            return   # catalog too small to offer a draw (shouldn't happen)
        options = self._rng.sample(list(effects.keys()), 4)

        self._card = {"seat": seat, "stage": "pick"}   # face-down; ids withheld until reveal
        self._deadline = time.time() + settings.CARD_PICK_SECONDS
        await self._broadcast()
        got = await self._drain_for(seat, "pick_card", settings.CARD_PICK_SECONDS)
        self._deadline = None

        if got is not None:
            try:
                idx = int(got.get("index", 0))
            except (TypeError, ValueError):
                idx = 0
        else:
            idx = self._rng.randint(0, 3)
        idx = max(0, min(3, idx))
        picked = options[idx]
        effect = effects[picked]

        # 1) reveal the drawn card to everyone
        self._card = {"seat": seat, "stage": "reveal", "options": options, "picked": idx}
        await self._broadcast()
        await asyncio.sleep(settings.CARD_REVEAL_SECONDS)

        # 2) if the card hits an opponent and there's a choice, let the drawer pick one
        target: int | None = None
        targets = self._valid_targets(seat, effect)
        if targets:
            if len(targets) == 1:
                target = targets[0]
            else:
                self._card = {
                    "seat": seat, "stage": "target", "options": options,
                    "picked": idx, "targets": targets,
                }
                self._deadline = time.time() + settings.CARD_PICK_SECONDS
                await self._broadcast()
                got = await self._drain_for(seat, "pick_target", settings.CARD_PICK_SECONDS)
                self._deadline = None
                chosen = None
                if got is not None:
                    try:
                        chosen = int(got.get("seat"))
                    except (TypeError, ValueError):
                        chosen = None
                target = chosen if chosen in targets else self._leading_among(seat, targets)

        # 3) apply the effect (to the chosen/auto target) and log the draw
        affected = self._apply_card(seat, effect, target)
        self.buf_cards.append({
            "match_id": self.match_id, "user_id": uid, "seat": seat,
            "options": options, "picked": picked, "turn": turn_no,
        })

        # 4) show the resolved result (who was hit) to everyone
        self._card = {
            "seat": seat, "stage": "result", "options": options,
            "picked": idx, "target": affected,
        }
        await self._broadcast()
        await asyncio.sleep(settings.CARD_REVEAL_SECONDS)
        self._card = None
        await self._broadcast()

    # cards whose effect lands on a chosen opponent
    _TARGETING = frozenset({"lock", "lock2", "recall", "swap"})

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
    def _apply_card(self, seat: int, effect: str, target: int | None = None) -> int | None:
        """Apply a drawn card's effect. ``target`` is the opponent seat the drawer chose
        (or None → auto). Returns the seat actually affected (for the result banner), or
        None for a self-only card. Every effect is wired; Mirror replays an opponent's."""
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
            st.set_eff("shield", seat, 3)                 # your tokens uncapturable, 3 rounds
        elif effect == "shield_all":
            st.set_eff("shield", seat, 5)                 # a longer sanctuary
        elif effect == "double_dice":
            st.set_eff("double", seat, 2)                 # next 2 rolls doubled
        elif effect == "second_chance":
            st.set_eff("second_chance", seat, 1)
        elif effect == "toll":
            st.set_eff("toll", seat, 2)                   # your star blocks rivals ~1 round
        elif effect == "steal_turn":
            self._grant_turn(seat)                        # jump the queue: take a turn now
        elif effect == "boost":
            self._advance_own_token(seat, 3)
        elif effect == "summon":
            self._release_from_base(seat)
        elif effect == "teleport":
            self._warp_to_next_star(seat)
        elif effect == "coins":
            uid = self._human_uid(seat)
            if uid is not None:
                self.stat_coins[uid] = self.stat_coins.get(uid, 0) + 150
        elif effect in ("lock", "lock2"):
            tgt = target if target is not None else self._leading_rival(seat)
            if tgt is not None:
                st.add_eff("skip", tgt, 1 if effect == "lock" else 2)
            return tgt
        elif effect == "recall":
            tgt = target if target is not None else self._leading_rival(seat)
            if tgt is not None:
                self._recall_seat(tgt, 4)
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
                return self._apply_card(seat, mirrored, target)
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
            # Bulk-insert the roll/knock events buffered since the last persist (humans only).
            if self.buf_rolls:
                await session.execute(insert(DiceRoll), self.buf_rolls)
                self.buf_rolls = []
            if self.buf_knocks:
                await session.execute(insert(Knockout), self.buf_knocks)
                self.buf_knocks = []
            if self.buf_cards:
                await session.execute(insert(CardDraw), self.buf_cards)
                self.buf_cards = []
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
        uids = (
            set(self.stat_dice) | set(self.stat_dealt) | set(self.stat_taken)
            | set(self.stat_potential) | set(self.stat_coins)
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
                user.coins = (user.coins or 0) + self.stat_coins[uid]

        # deltas are now persisted (within this session's pending commit) — reset them so
        # the next flush only applies what's new.
        self.stat_dice = {}
        self.stat_dealt = {}
        self.stat_taken = {}
        self.stat_potential = {}
        self.stat_coins = {}

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
