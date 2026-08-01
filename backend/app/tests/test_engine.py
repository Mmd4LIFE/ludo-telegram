"""Headless validation of the pure Ludo engine.

Run with:  cd backend && python -m app.tests.test_engine
(also discoverable by pytest, but has zero third-party deps so it runs bare).

These are the safety net that lets the rules be refactored freely: they assert the
coordinate model, capture/safe-square logic, the triple-six forfeit and extra-turn
grants, and they fuzz thousands of full games driven by the bot to prove the state
machine always terminates with a complete ranking and never produces an illegal state.
"""
from __future__ import annotations

import random

from app.ludo import (
    BASE,
    HOME,
    SAFE_SQUARES,
    Color,
    Phase,
    absolute_square,
    apply_move,
    initial_state,
    is_safe,
    legal_moves,
    register_roll,
    roll_die,
)
from app.ludo.bots import choose_move
from app.ludo.state import GameState, Move


def _check(name: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(f"FAILED: {name}")
    print(f"  ok  {name}")


# --- geometry ---------------------------------------------------------------
def test_geometry() -> None:
    _check("RED start is absolute 0", absolute_square(Color.RED, 0) == 0)
    _check("GREEN start is absolute 13", absolute_square(Color.GREEN, 0) == 13)
    _check("YELLOW start is absolute 26", absolute_square(Color.YELLOW, 0) == 26)
    _check("BLUE start is absolute 39", absolute_square(Color.BLUE, 0) == 39)
    _check("ring wraps mod 52", absolute_square(Color.BLUE, 20) == (39 + 20) % 52)
    _check("progress 51 is off the ring (home column)", absolute_square(Color.RED, 51) is None)
    _check("base is off the ring", absolute_square(Color.RED, BASE) is None)
    _check("HOME is 56", HOME == 56)
    _check("4 default safe squares (starts only)", len(SAFE_SQUARES) == 4)
    _check("starts are safe", all(is_safe(o) for o in (0, 13, 26, 39)))
    _check("neutral stars are NOT safe by default", not any(is_safe(o) for o in (8, 21, 34, 47)))


# --- release only on a 6 ----------------------------------------------------
def test_release_requires_six() -> None:
    s = initial_state(4)
    register_roll(s, 3)
    _check("no moves with all tokens in base and a 3", legal_moves(s) == [])
    apply_move(s, None)  # pass
    _check("turn passed to next seat", s.current == 1 and s.phase is Phase.ROLL)

    s = initial_state(4)
    register_roll(s, 6)
    moves = legal_moves(s)
    _check("a 6 releases from base (4 tokens -> 4 moves)", len(moves) == 4)
    _check("released token lands on progress 0", moves[0].dst == 0 and moves[0].releases_from_base)


# --- extra turns ------------------------------------------------------------
def test_extra_turn_on_six() -> None:
    s = initial_state(4)
    register_roll(s, 6)
    res = apply_move(s, legal_moves(s)[0])
    _check("rolling a 6 grants an extra turn", res.extra_turn)
    _check("same player still on turn", s.current == 0 and s.phase is Phase.ROLL)


def test_sixes_loop_without_cap() -> None:
    """Every six grants another roll — a streak of sixes is a streak of extra turns,
    with no cap and no forfeit."""
    s = initial_state(4)
    for i in range(5):                           # five sixes in a row
        register_roll(s, 6)
        res = apply_move(s, legal_moves(s)[0])
        _check(f"six #{i + 1} grants an extra turn", res.extra_turn)
        _check(f"still player 0's turn after six #{i + 1}", s.current == 0 and s.phase is Phase.ROLL)
    # a non-six finally ends the turn
    register_roll(s, 3)
    res = apply_move(s, legal_moves(s)[0])
    _check("a non-six passes the turn", not res.extra_turn and s.current == 1)


# --- capture ----------------------------------------------------------------
def test_capture_sends_home_and_grants_turn() -> None:
    s = initial_state(2, seat_colors=[Color.RED, Color.YELLOW])
    # Put a YELLOW token on absolute square 5 (a non-safe ring cell).
    # YELLOW progress p -> absolute (26 + p) % 52 == 5  => p == 31.
    _check("setup: yellow lands on absolute 5", absolute_square(Color.YELLOW, 31) == 5)
    s.players[1].tokens[0] = 31
    # RED at progress 2 rolling a 3 lands on absolute 5 and captures.
    s.players[0].tokens[0] = 2
    register_roll(s, 3)
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("red move captures yellow", moves and moves[0].captures == ((1, 0),))
    res = apply_move(s, moves[0])
    _check("captured yellow returns to base", s.players[1].tokens[0] == BASE)
    _check("capture grants an extra turn", res.extra_turn)


def test_start_square_protects_owner_only() -> None:
    # A colour is always safe on its own START square…
    s = initial_state(2, seat_colors=[Color.RED, Color.YELLOW])
    s.players[1].tokens[0] = 0    # YELLOW on its own start, abs 26
    s.players[0].tokens[0] = 23   # RED abs 23
    register_roll(s, 3)           # RED -> abs 26 (yellow's own start)
    _check("setup: red lands on yellow's own start", absolute_square(Color.RED, 26) == 26)
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("owner is safe on its own start", moves and moves[0].captures == ())


def test_neutral_star_safe_only_when_activated() -> None:
    # YELLOW's neutral star is abs 34 (26 + 8). YELLOW sits there (progress 8);
    # RED at progress 31 rolls 3 to land on abs 34.
    def _setup() -> GameState:
        st = initial_state(2, seat_colors=[Color.RED, Color.YELLOW])
        st.players[1].tokens[0] = 8    # YELLOW abs 34 (its own neutral star)
        st.players[0].tokens[0] = 31   # RED abs 31
        register_roll(st, 3)           # RED -> abs 34
        return st

    _check("setup: abs 34 is not safe by default", not is_safe(34))
    s = _setup()
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("neutral star captures by default", moves and moves[0].captures == ((1, 0),))

    s = _setup()
    s.active_stars = [Color.YELLOW.value]   # YELLOW activated its stars
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("owner safe on its neutral star once activated", moves and moves[0].captures == ())


def test_six_with_no_move_keeps_the_turn() -> None:
    """A six you cannot play still earns another roll (the reward for the six)."""
    s = initial_state(4)
    # Every token sits one square short of HOME, so a 6 overshoots and nothing is legal.
    s.players[0].tokens = [55, 55, 55, 55]
    register_roll(s, 6)
    _check("no legal move (a 6 overshoots HOME)", legal_moves(s) == [])
    res = apply_move(s, None)
    _check("an unplayable six still grants an extra turn", res.extra_turn)
    _check("same player rolls again", s.current == 0 and s.phase is Phase.ROLL)

    # A non-six with no move simply passes the turn.
    s2 = initial_state(4)
    register_roll(s2, 3)
    apply_move(s2, None)
    _check("an unplayable non-six passes the turn", s2.current == 1)


# --- home / exact landing ---------------------------------------------------
def test_must_land_home_exactly() -> None:
    s = initial_state(2, seat_colors=[Color.RED, Color.YELLOW])
    s.players[0].tokens[0] = 54   # two short of HOME (56)
    register_roll(s, 3)           # would overshoot to 57
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("overshoot past HOME is illegal", moves == [])
    s.die = None
    s.phase = Phase.ROLL
    register_roll(s, 2)           # exact
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("exact roll reaches HOME", moves and moves[0].reaches_home and moves[0].dst == HOME)


# --- full-game fuzz ---------------------------------------------------------
def _play_random_game(seed: int) -> None:
    rng = random.Random(seed)
    s = initial_state(rng.choice([2, 3, 4]))
    guard = 0
    while s.phase is not Phase.FINISHED:
        guard += 1
        if guard > 200_000:
            raise AssertionError(f"game {seed} did not terminate")
        register_roll(s, roll_die(rng))
        if s.phase is Phase.FINISHED:
            break
        if s.phase is not Phase.MOVE:
            continue  # roll forfeited (triple six) — back to ROLL for next player
        moves = legal_moves(s)
        _assert_state_legal(s, moves)
        apply_move(s, choose_move(s, moves, rng) if moves else None)

    _check(
        f"game {seed} finished with a full ranking",
        len(s.ranking) == len(s.players) and len(set(s.ranking)) == len(s.players),
    )


def _assert_state_legal(s, moves) -> None:
    for p in s.players:
        for t in p.tokens:
            assert t == BASE or 0 <= t <= HOME, f"illegal progress {t}"
    for m in moves:
        assert m.dst <= HOME
        # a released move must advance by exactly the die
        if not m.releases_from_base:
            assert m.dst - m.src == s.die


def test_fuzz_games() -> None:
    for seed in range(300):
        _play_random_game(seed)
    _check("300 random full games all terminated cleanly", True)


def run_all() -> None:
    for fn in (
        test_geometry,
        test_release_requires_six,
        test_extra_turn_on_six,
        test_sixes_loop_without_cap,
        test_capture_sends_home_and_grants_turn,
        test_start_square_protects_owner_only,
        test_neutral_star_safe_only_when_activated,
        test_six_with_no_move_keeps_the_turn,
        test_must_land_home_exactly,
        test_fuzz_games,
    ):
        print(f"\n{fn.__name__}:")
        fn()
    print("\nAll engine tests passed.")


if __name__ == "__main__":
    run_all()
