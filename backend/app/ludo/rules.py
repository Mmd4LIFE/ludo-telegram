"""The rules: dice, legal-move generation, applying a move, turn advancement.

Everything here is a pure function of (state, die). The RNG is injectable so the engine
is fully deterministic under test — the operational layer passes a seeded ``random.Random``
(or a scripted die sequence) and gets reproducible games.

Turn flow
---------
A turn is two phases: ROLL then MOVE.

    ROLL  -> caller rolls the die (roll_die + register_roll), state moves to MOVE
    MOVE  -> caller picks one of legal_moves() and calls apply_move()

apply_move() returns an ApplyResult describing what happened (captures, a token reaching
home, whether the same player rolls again) and leaves the state either back at ROLL for
the same player (extra turn) or at ROLL for the next active player.

Extra turn is granted when the die was a 6, or the move captured an opponent, or the move
sent a token home. Sixes loop with no cap: each six earns another roll, so a streak of
sixes is a streak of extra turns (no triple-six forfeit).
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from app.ludo.board import (
    BASE,
    HOME,
    TOKENS_PER_PLAYER,
    Color,
    absolute_square,
    neutral_star_owner,
    safe_owner,
)
from app.ludo.state import GameState, Move, Phase, PlayerState


# --- setup ------------------------------------------------------------------
def initial_state(num_players: int = 4, *, seat_colors: list[Color] | None = None) -> GameState:
    """A fresh game. 2–4 players seated on the standard colours (opposite corners for 2)."""
    if not 2 <= num_players <= 4:
        raise ValueError("Ludo supports 2 to 4 players")
    if seat_colors is None:
        # 2p sit opposite (RED/YELLOW); 3p RED/GREEN/YELLOW; 4p all.
        defaults = {
            2: [Color.RED, Color.YELLOW],
            3: [Color.RED, Color.GREEN, Color.YELLOW],
            4: [Color.RED, Color.GREEN, Color.YELLOW, Color.BLUE],
        }
        seat_colors = defaults[num_players]
    if len(seat_colors) != num_players or len(set(seat_colors)) != num_players:
        raise ValueError("seat_colors must be a distinct colour per player")
    return GameState(players=[PlayerState(color=c) for c in seat_colors])


# --- dice -------------------------------------------------------------------
def roll_die(rng: random.Random | None = None) -> int:
    """Roll a fair d6. Pass a seeded Random for determinism."""
    r = rng or random
    return r.randint(1, 6)


def register_roll(state: GameState, die: int) -> None:
    """Record a die value on the state and count consecutive sixes.

    Mutates ``state`` in place, moving to MOVE with this die. ``consecutive_sixes`` is
    tracked for reference but no longer gates anything: every six grants another roll (see
    ``apply_move``), so a streak of sixes just keeps paying out.
    """
    if state.phase is not Phase.ROLL:
        raise ValueError(f"cannot roll in phase {state.phase}")
    if not 1 <= die <= 6:
        raise ValueError("die must be 1..6")

    if die == 6:
        state.consecutive_sixes += 1
    else:
        state.consecutive_sixes = 0

    # roll_face is the true face (drives release + six logic); die is the movement value,
    # which the operational layer may double (Twin Dice) before generating moves.
    state.roll_face = die
    state.die = die
    state.phase = Phase.MOVE


def _six_pays_extra(state: GameState) -> bool:
    """A six ALWAYS grants another roll — consecutive sixes loop with no cap and no
    forfeit. The bonus keys off the rolled FACE, not the (possibly doubled) movement."""
    return state.roll_face == 6


def _can_release(state: GameState) -> bool:
    """Leaving base needs a rolled six (the face — Twin Dice's doubling doesn't grant it)."""
    return (state.roll_face if state.roll_face is not None else state.die) == 6


def _blocked_squares(state: GameState, mover: Color) -> set[int]:
    """Absolute cells a mover may not land on this turn — a rival's active Toll-Gate star.
    Effects are keyed by SEAT, so we resolve each tolling seat's colour to its star cell."""
    from app.ludo.board import START_OFFSET, MAIN_TRACK_LEN

    blocked: set[int] = set()
    for seat, p in enumerate(state.players):
        if p.color == mover:
            continue
        if state.eff("toll", seat) > 0:
            blocked.add((START_OFFSET[p.color] + 8) % MAIN_TRACK_LEN)
    return blocked


# --- legal moves ------------------------------------------------------------
def legal_moves(state: GameState, die: int | None = None) -> list[Move]:
    """All legal moves for the current player given the current (or supplied) die.

    Returns an empty list when the player cannot move (e.g. rolled a 3 with every token
    stuck in base); the caller then passes the turn via ``apply_move(state, None)``.
    """
    explicit = die is not None
    die = die if die is not None else state.die
    if die is None or state.phase is not Phase.MOVE:
        return []

    # release needs a face-6: for the live die use roll_face; for a hypothetical die
    # (bot look-ahead passing an explicit value) fall back to that value.
    can_release = die == 6 if explicit else _can_release(state)
    blocked = set() if explicit else _blocked_squares(state, state.current_player.color)

    player = state.current_player
    moves: list[Move] = []
    for idx, prog in enumerate(player.tokens):
        if prog == BASE:
            # a release always lands on your OWN start, which is never a rival's toll star
            if can_release:
                moves.append(_build_move(state, idx, BASE, 0))
            continue
        if prog == HOME:
            continue
        dst = prog + die
        if dst > HOME:
            continue  # must land exactly on HOME; overshoot is illegal
        if blocked:
            sq = absolute_square(player.color, dst)
            if sq is not None and sq in blocked:
                continue  # a rival's Toll-Gate star blocks landing here
        moves.append(_build_move(state, idx, prog, dst))
    return moves


def _build_move(state: GameState, idx: int, src: int, dst: int) -> Move:
    player = state.current_player
    captures = _captures_at(state, player.color, dst)
    return Move(
        token_index=idx,
        src=src,
        dst=dst,
        releases_from_base=(src == BASE),
        reaches_home=(dst == HOME),
        captures=tuple(captures),
    )


def _captures_at(state: GameState, mover: Color, dst: int) -> list[tuple[int, int]]:
    """Opponent (seat, token) pairs captured by a mover landing on progress ``dst``.

    No capture when landing off the ring (home column). A defender is protected only when
    it sits on a safe square owned by *its own* colour — so you capture intruders on your
    own star but cannot capture the owner sitting on theirs. A blockade of two or more
    same-colour tokens is never captured (bounced; full blockade rules are a ROADMAP item).
    """
    dst_sq = absolute_square(mover, dst)
    if dst_sq is None:
        return []
    owner = safe_owner(dst_sq)               # start-square sanctuary colour, or None
    nstar = neutral_star_owner(dst_sq)       # neutral-star colour, or None
    active = set(state.active_stars)         # colours whose neutral stars are live

    hits: dict[int, tuple[list[int], Color]] = {}
    for seat, p in enumerate(state.players):
        if p.color == mover:
            continue
        for tok_idx, prog in enumerate(p.tokens):
            if absolute_square(p.color, prog) == dst_sq:
                hits.setdefault(seat, ([], p.color))[0].append(tok_idx)

    captured: list[tuple[int, int]] = []
    for seat, (toks, color) in hits.items():
        if owner == color:
            # a colour is always safe on its own start square
            continue
        if nstar == color and color.value in active:
            # …and safe on its own neutral star once that colour has activated stars
            continue
        if state.eff("shield", seat) > 0:
            # an Aegis/Bulwark shield makes this seat's tokens uncapturable
            continue
        if len(toks) >= 2:
            # a blockade of >=2 same-colour tokens is not captured
            continue
        captured.append((seat, toks[0]))
    return captured


# --- applying a move --------------------------------------------------------
@dataclass
class ApplyResult:
    moved: Move | None                 # None if the turn was passed (no legal move)
    captured: list[tuple[int, int]]    # (seat, token_index) sent back to base
    reached_home: bool
    extra_turn: bool
    game_over: bool


def apply_move(state: GameState, move: Move | None) -> ApplyResult:
    """Apply ``move`` (or pass the turn if ``move`` is None) and advance play.

    ``move`` must come from ``legal_moves(state)``. Passing (None) is only valid when
    there are no legal moves. Mutates ``state`` in place.
    """
    if state.phase is not Phase.MOVE:
        raise ValueError(f"cannot move in phase {state.phase}")
    die = state.die
    assert die is not None

    if move is None:
        if legal_moves(state):
            raise ValueError("cannot pass: legal moves exist")
        # A six still earns another roll even when it cannot be played — you keep the reward.
        extra = _six_pays_extra(state)
        _end_turn(state, extra_turn=extra)
        return ApplyResult(None, [], False, extra, state.phase is Phase.FINISHED)

    player = state.current_player

    # move the token
    player.tokens[move.token_index] = move.dst

    # send captured opponents back to base — unless a Second Wind saves the token (a
    # one-shot buff that negates the very next knock-home and is consumed doing so).
    real_captures: list[tuple[int, int]] = []
    for seat, tok_idx in move.captures:
        if state.eff("second_chance", seat) > 0:
            state.set_eff("second_chance", seat, 0)
            continue
        state.players[seat].tokens[tok_idx] = BASE
        real_captures.append((seat, tok_idx))

    reached_home = move.dst == HOME

    # did this player just finish (all four home)?
    if player.all_home() and player.finished_at is None:
        player.finished_at = state.turn
        state.ranking.append(state.current)

    # Extra turn on a six (always, looping) or a REAL capture (a saved knock doesn't pay).
    # Reaching home no longer grants a bonus roll — the reward is a fantasy-card draw,
    # handled by the operational layer (see MatchRuntime), not the pure engine.
    extra_turn = _six_pays_extra(state) or bool(real_captures)
    _end_turn(state, extra_turn=extra_turn)

    return ApplyResult(
        moved=move,
        captured=real_captures,
        reached_home=reached_home,
        extra_turn=extra_turn,
        game_over=state.phase is Phase.FINISHED,
    )


# --- turn machinery ---------------------------------------------------------
def _remaining_players(state: GameState) -> int:
    return sum(1 for p in state.players if not p.all_home())


def _end_turn(state: GameState, *, extra_turn: bool) -> None:
    """Finish the current action: check game-over, then set up the next ROLL."""
    state.die = None

    # Game ends when at most one player still has tokens to bring home.
    if _remaining_players(state) <= 1:
        # append the straggler to the ranking so placements are complete
        for seat, p in enumerate(state.players):
            if seat not in state.ranking:
                state.ranking.append(seat)
        state.phase = Phase.FINISHED
        return

    if extra_turn and not state.current_player.all_home():
        # Same player rolls again. Keep consecutive_sixes across the extra turn so a
        # streak of sixes accumulates toward the triple-six forfeit (register_roll
        # already reset it to 0 whenever the granting roll was not a 6).
        state.phase = Phase.ROLL
        return

    _advance_to_next_player(state)


def _tick_turn_start(state: GameState, seat: int) -> None:
    """A round of the seat's timed buffs elapses at the start of its turn."""
    for key in ("shield", "toll"):
        if state.eff(key, seat) > 0:
            state.add_eff(key, seat, -1)


def _advance_to_next_player(state: GameState) -> None:
    state.consecutive_sixes = 0
    state.phase = Phase.ROLL
    state.turn += 1
    n = len(state.players)
    seat = state.current
    # Walk forward to the next active seat, SKIPPING frozen ones (Freeze/Deep Freeze) and
    # consuming one frozen turn each time we pass them. Bounded — freezes only ever count
    # down, so this terminates.
    guard = 0
    while guard < n * 8:
        guard += 1
        seat = (seat + 1) % n
        if state.players[seat].all_home():
            continue
        if state.eff("skip", seat) > 0:
            state.add_eff("skip", seat, -1)   # consume a frozen turn, skip them
            continue
        state.current = seat
        _tick_turn_start(state, seat)
        return
    # fail-safe: hand the turn to the next active seat regardless of freezes
    seat = state.current
    for _ in range(n):
        seat = (seat + 1) % n
        if not state.players[seat].all_home():
            state.current = seat
            _tick_turn_start(state, seat)
            return
    # everyone home — game over (defensive; _end_turn should have caught this)
    state.phase = Phase.FINISHED
