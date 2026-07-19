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
from app.ludo.state import Move


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
    _check("8 safe squares", len(SAFE_SQUARES) == 8)
    _check("starts are safe", all(is_safe(o) for o in (0, 13, 26, 39)))


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


def test_triple_six_forfeits() -> None:
    s = initial_state(4)
    # release a token so the player actually has moves between sixes
    register_roll(s, 6)
    apply_move(s, legal_moves(s)[0])            # token0 -> progress 0, extra turn
    register_roll(s, 6)                          # second six
    _check("second six counted", s.consecutive_sixes == 2)
    apply_move(s, legal_moves(s)[0])            # move, extra turn again
    register_roll(s, 6)                          # THIRD six -> forfeit
    _check("triple six forfeits the turn", s.current == 1 and s.phase is Phase.ROLL)
    _check("six counter reset after forfeit", s.consecutive_sixes == 0)


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


def test_safe_square_blocks_capture() -> None:
    s = initial_state(2, seat_colors=[Color.RED, Color.YELLOW])
    # YELLOW sits on absolute 8 (a star / safe square).
    # YELLOW progress p -> (26 + p) % 52 == 8 => p == 34.
    _check("setup: yellow on safe square 8", absolute_square(Color.YELLOW, 34) == 8 and is_safe(8))
    s.players[1].tokens[0] = 34
    s.players[0].tokens[0] = 5    # RED abs 5
    register_roll(s, 3)           # RED -> abs 8
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("landing on a safe square captures nothing", moves and moves[0].captures == ())


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
        test_triple_six_forfeits,
        test_capture_sends_home_and_grants_turn,
        test_safe_square_blocks_capture,
        test_must_land_home_exactly,
        test_fuzz_games,
    ):
        print(f"\n{fn.__name__}:")
        fn()
    print("\nAll engine tests passed.")


if __name__ == "__main__":
    run_all()
