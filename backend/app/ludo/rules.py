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
sent a token home. Rolling three 6s in a row forfeits the whole turn (handled in
register_roll, which is why rolling is a state transition and not a bare RNG call).
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
    """Record a die value on the state and handle the triple-six forfeit.

    Mutates ``state`` in place. On a third consecutive 6 the turn is forfeited: the die
    is discarded, the six-counter reset, and play advances to the next active player
    (state returns to ROLL). Otherwise the state moves to MOVE with this die.
    """
    if state.phase is not Phase.ROLL:
        raise ValueError(f"cannot roll in phase {state.phase}")
    if not 1 <= die <= 6:
        raise ValueError("die must be 1..6")

    if die == 6:
        state.consecutive_sixes += 1
    else:
        state.consecutive_sixes = 0

    if state.consecutive_sixes >= 3:
        # Third six in a row — whole turn is void (classic Ludo penalty).
        state.consecutive_sixes = 0
        state.die = None
        _advance_to_next_player(state)
        return

    state.die = die
    state.phase = Phase.MOVE


# --- legal moves ------------------------------------------------------------
def legal_moves(state: GameState, die: int | None = None) -> list[Move]:
    """All legal moves for the current player given the current (or supplied) die.

    Returns an empty list when the player cannot move (e.g. rolled a 3 with every token
    stuck in base); the caller then passes the turn via ``apply_move(state, None)``.
    """
    die = die if die is not None else state.die
    if die is None or state.phase is not Phase.MOVE:
        return []

    player = state.current_player
    moves: list[Move] = []
    for idx, prog in enumerate(player.tokens):
        if prog == BASE:
            if die == 6:
                moves.append(_build_move(state, idx, BASE, 0))
            continue
        if prog == HOME:
            continue
        dst = prog + die
        if dst > HOME:
            continue  # must land exactly on HOME; overshoot is illegal
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
    owner = safe_owner(dst_sq)  # colour this square protects, or None

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
            # the square's own colour is safe here; everyone else is exposed
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
        # No move possible. A 6 with no move still ends the turn (no free re-roll).
        _end_turn(state, extra_turn=False)
        return ApplyResult(None, [], False, False, state.phase is Phase.FINISHED)

    player = state.current_player

    # move the token
    player.tokens[move.token_index] = move.dst

    # send captured opponents back to base
    for seat, tok_idx in move.captures:
        state.players[seat].tokens[tok_idx] = BASE

    reached_home = move.dst == HOME

    # did this player just finish (all four home)?
    if player.all_home() and player.finished_at is None:
        player.finished_at = state.turn
        state.ranking.append(state.current)

    extra_turn = die == 6 or bool(move.captures) or reached_home
    _end_turn(state, extra_turn=extra_turn)

    return ApplyResult(
        moved=move,
        captured=list(move.captures),
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


def _advance_to_next_player(state: GameState) -> None:
    state.consecutive_sixes = 0
    state.phase = Phase.ROLL
    state.turn += 1
    n = len(state.players)
    seat = state.current
    for _ in range(n):
        seat = (seat + 1) % n
        if not state.players[seat].all_home():
            state.current = seat
            return
    # everyone home — game over (defensive; _end_turn should have caught this)
    state.phase = Phase.FINISHED
