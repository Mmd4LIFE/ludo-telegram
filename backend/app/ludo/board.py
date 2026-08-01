"""Board geometry and the coordinate model.

Coordinate model (per token, one integer ``progress``)
------------------------------------------------------
A token belongs to a colour and always sits at one of:

    BASE (= -1)      in the yard, not yet in play
    progress 0..50   on the shared 52-cell ring, RELATIVE to the token's own start
    progress 51..56  in that colour's private 6-cell home column
    HOME (= 56)      the final square — the token is finished

So a released token's journey is exactly ``0 -> 56`` (56 steps). To leave BASE you must
roll a 6, which places the token at ``progress 0`` (its own start square).

The ring is 52 cells indexed ``0..51`` in absolute (board) coordinates. Each colour
enters the ring at a fixed absolute cell (its start), 13 cells apart:

    RED    starts at absolute 0
    GREEN  starts at absolute 13
    YELLOW starts at absolute 26
    BLUE   starts at absolute 39

A token's absolute ring cell is ``(START_OFFSET[colour] + progress) % 52`` while
``0 <= progress <= 50``. At ``progress >= 51`` the token has turned into its private home
column and no longer has an absolute ring cell (home columns never collide, so no capture
can happen there).

Safe squares: by default ONLY the four coloured start cells are safe, each a private
sanctuary for its own colour. The four *neutral* star cells (eight steps past each start)
are NOT safe for anyone — they are inert until a player activates their colour's stars via
the "Active Stars" fantasy card, after which that colour is safe on its own neutral star.
Everywhere else on the ring, landing on a lone opponent captures it.
"""
from __future__ import annotations

from enum import IntEnum

# --- ring / column sizing ---------------------------------------------------
MAIN_TRACK_LEN = 52          # cells on the shared ring (absolute 0..51)
RING_CELLS = 51              # ring cells a token steps on (progress 0..50)
HOME_COLUMN_LEN = 6          # private home-stretch cells (progress 51..56)

BASE = -1                    # sentinel: token is in the yard
HOME = RING_CELLS + HOME_COLUMN_LEN - 1  # = 56, the finished square
TOKENS_PER_PLAYER = 4


class Color(IntEnum):
    """Player colours in clockwise seating order. Value doubles as the seat index."""

    RED = 0
    GREEN = 1
    YELLOW = 2
    BLUE = 3


# Absolute ring cell where each colour enters play (13 cells apart, clockwise).
START_OFFSET: dict[Color, int] = {
    Color.RED: 0,
    Color.GREEN: 13,
    Color.YELLOW: 26,
    Color.BLUE: 39,
}

# Default safe cells: ONLY the four coloured start cells. Each is a private sanctuary for
# its own colour — landing on your own start captures intruders, but you can't capture the
# owner sitting on theirs.
SAFE_SQUARES: frozenset[int] = frozenset(START_OFFSET.values())

SAFE_OWNER: dict[int, Color] = {off: color for color, off in START_OFFSET.items()}

# The four NEUTRAL star cells (8 steps past each start) and the colour each belongs to.
# They are NOT safe by default; a colour becomes safe on its own neutral star only after
# activating stars via the "Active Stars" fantasy card (tracked on GameState.active_stars).
NEUTRAL_STARS: dict[int, Color] = {
    (off + 8) % MAIN_TRACK_LEN: color for color, off in START_OFFSET.items()
}


def safe_owner(square: int) -> Color | None:
    """The colour a *default* safe square (a start) protects, or ``None``."""
    return SAFE_OWNER.get(square)


def neutral_star_owner(square: int) -> Color | None:
    """The colour a neutral star belongs to (safe only once that colour activates), else None."""
    return NEUTRAL_STARS.get(square)


def absolute_square(color: Color, progress: int) -> int | None:
    """Absolute ring cell (0..51) for a token, or ``None`` if it is off the ring.

    Off the ring means BASE (< 0) or inside the private home column (progress >= 51).
    Those positions can never share a cell with another token, so callers treat a
    ``None`` here as "not collidable".
    """
    if progress < 0 or progress > RING_CELLS - 1:  # BASE or home column / HOME
        return None
    return (START_OFFSET[color] + progress) % MAIN_TRACK_LEN


def is_safe(square: int) -> bool:
    """True if the given absolute ring cell is a star (capture-proof) square."""
    return square in SAFE_SQUARES


def in_home_column(progress: int) -> bool:
    return RING_CELLS <= progress <= HOME


def is_home(progress: int) -> bool:
    return progress == HOME
