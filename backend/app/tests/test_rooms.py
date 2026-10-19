"""Late-join insertion and room-summary rules that do not need a live database."""
from __future__ import annotations

from types import SimpleNamespace

from app.analytics.clickhouse import fold_face_counts
from app.ludo.board import BASE, Color
from app.ludo.rules import initial_state, next_turn_seat
from app.ludo.state import Phase, PlayerState
from app.models.match import MatchStatus


def test_score_counts_every_saved_roll():
    # Five saved sixes must stay five. A reroll used to subtract one only in memory
    # after the row was already stored, so the board showed 4.
    rows = [(2, 6), (2, 6), (2, 6), (2, 6), (2, 6), (2, 3)]
    hist = fold_face_counts(rows)
    assert hist[2][6] == 5
    assert hist[2][3] == 1
    assert sum(hist[2].values()) == 6


def test_late_player_starts_in_yard_without_stealing_the_turn():
    state = initial_state(3, seat_colors=[Color.BLUE, Color.RED, Color.GREEN])
    state.current = 1
    state.phase = Phase.MOVE
    state.die = 4
    state.turn = 17
    before = state.clone()

    joined = state.clone()
    joined.players.append(PlayerState(color=Color.YELLOW))

    assert joined.current == before.current == 1
    assert joined.phase is Phase.MOVE
    assert joined.die == 4
    assert joined.turn == 17
    assert joined.players[3].tokens == [BASE, BASE, BASE, BASE]
    assert [p.color for p in before.players] == [Color.BLUE, Color.RED, Color.GREEN]
    # Yellow sits between green and blue, so the live turn stays on red.
    assert next_turn_seat(joined, 1) == 2
    assert next_turn_seat(joined, 2) == 3
    assert next_turn_seat(joined, 3) == 0


def test_runtime_installs_the_committed_player_in_place():
    from app.game.runtime import MatchRuntime

    seats = [
        SimpleNamespace(seat_index=0, color="BLUE", user_id=11, is_bot=False),
        SimpleNamespace(seat_index=1, color="RED", user_id=22, is_bot=False),
    ]
    state = initial_state(2, seat_colors=[Color.BLUE, Color.RED])
    state.current = 0
    state.die = 6
    match = SimpleNamespace(
        id=7,
        code="ROOM1",
        max_players=4,
        entry_fee=0,
        is_bot_table=False,
        state=state.to_dict(),
        seats=seats,
    )
    rt = MatchRuntime(match)
    new_state = rt.state.clone()
    new_state.players.append(PlayerState(color=Color.GREEN))
    rt.install_late_player(
        state=new_state,
        seat=2,
        user_id=33,
        color=Color.GREEN,
        name="Late",
        level=4,
        skin="gold",
    )
    assert rt.state.current == 0
    assert rt.state.die == 6
    assert rt.state.players[2].tokens == [BASE] * 4
    assert rt.seat_user[2] == 33
    assert rt.seat_is_bot[2] is False
    assert rt.seat_names[2] == "Late"
    assert rt.seat_user[0] == 11


def test_joinable_closes_after_a_finisher_and_for_bot_tables():
    from app.api.routes_matches import _available_colors, _has_finisher, _joinable

    waiting = SimpleNamespace(
        status=MatchStatus.WAITING,
        is_bot_table=False,
        max_players=4,
        state=None,
        seats=[SimpleNamespace(user_id=1, is_bot=False, color="BLUE")],
    )
    assert _joinable(waiting)
    assert _available_colors(waiting) == ["RED", "GREEN", "YELLOW"]

    playing = SimpleNamespace(
        status=MatchStatus.PLAYING,
        is_bot_table=False,
        max_players=4,
        state={"ranking": [], "players": [{"finished_at": None}]},
        seats=[
            SimpleNamespace(user_id=1, is_bot=False, color="BLUE"),
            SimpleNamespace(user_id=2, is_bot=False, color="RED"),
        ],
    )
    assert _joinable(playing)
    playing.state = {"ranking": [0], "players": [{"finished_at": 12}]}
    assert _has_finisher(playing)
    assert not _joinable(playing)

    bots = SimpleNamespace(
        status=MatchStatus.PLAYING,
        is_bot_table=True,
        max_players=4,
        state=None,
        seats=[SimpleNamespace(user_id=None, is_bot=True, color="BLUE")],
    )
    assert not _joinable(bots)
