"""A simple, pure heuristic bot.

Given a state and the legal moves, ``choose_move`` returns one move (or None when there
are none). It is deterministic given the same inputs and an optional RNG for tie-breaks,
so bot games are reproducible under test. The heuristic is intentionally readable — it is
the baseline "house bot" that keeps lobbies alive, not a solver. See ROADMAP for a
stronger expectiminimax bot.

Priority, high to low:
    1. Capture an opponent (prefer capturing a token that is further along).
    2. Send a token home.
    3. Release a token from base on a 6 (get pieces into play).
    4. Move the token that is furthest along its path (race to finish),
       lightly preferring to land on a safe square.
"""
from __future__ import annotations

import random

from app.ludo.board import HOME, is_safe, absolute_square
from app.ludo.state import GameState, Move


def _capture_value(state: GameState, move: Move) -> int:
    """How far along the captured opponents were (bigger = more painful for them)."""
    total = 0
    for seat, tok_idx in move.captures:
        total += state.players[seat].tokens[tok_idx]
    return total


def _lands_safe(color, move: Move) -> bool:
    sq = absolute_square(color, move.dst)
    return sq is not None and is_safe(sq)


def choose_move(
    state: GameState, moves: list[Move], rng: random.Random | None = None
) -> Move | None:
    if not moves:
        return None
    r = rng or random
    color = state.current_player.color

    def score(m: Move) -> tuple:
        return (
            1 if m.captures else 0,
            _capture_value(state, m),
            1 if m.reaches_home else 0,
            1 if m.releases_from_base else 0,
            1 if _lands_safe(color, m) else 0,
            m.dst,               # prefer advancing the furthest token
        )

    best = max(score(m) for m in moves)
    top = [m for m in moves if score(m) == best]
    return r.choice(top)
