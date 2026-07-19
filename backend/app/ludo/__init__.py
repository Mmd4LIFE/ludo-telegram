"""Pure Ludo engine — board geometry, dice, legal-move generation and rules.

This package has **no I/O**: no database, no network, no async, no Telegram. It is a
deterministic state machine that, given a game state and a die value, tells you the
legal moves and the result of applying one. That purity is what lets the whole engine
be validated by headless simulation (see ``app/tests/test_engine.py``) and reused
verbatim by the server runtime, the bots and (eventually) a replay tool.

The operational layer (``app.game.runtime``) owns the RNG, the clock, persistence and
the websocket fan-out; it drives this engine and never re-implements a rule.

Read ``docs/LUDO_RULES.md`` for the exact rules this encodes and ``docs/ENGINE.md`` for
the coordinate model.
"""
from app.ludo.board import (
    BASE,
    HOME,
    HOME_COLUMN_LEN,
    MAIN_TRACK_LEN,
    RING_CELLS,
    SAFE_OWNER,
    SAFE_SQUARES,
    START_OFFSET,
    Color,
    absolute_square,
    is_safe,
    safe_owner,
)
from app.ludo.state import GameState, Move, PlayerState, Phase
from app.ludo.rules import (
    ApplyResult,
    apply_move,
    initial_state,
    legal_moves,
    register_roll,
    roll_die,
)

__all__ = [
    "BASE",
    "HOME",
    "HOME_COLUMN_LEN",
    "MAIN_TRACK_LEN",
    "RING_CELLS",
    "SAFE_OWNER",
    "SAFE_SQUARES",
    "START_OFFSET",
    "Color",
    "absolute_square",
    "is_safe",
    "safe_owner",
    "GameState",
    "Move",
    "PlayerState",
    "Phase",
    "ApplyResult",
    "apply_move",
    "initial_state",
    "legal_moves",
    "register_roll",
    "roll_die",
]
