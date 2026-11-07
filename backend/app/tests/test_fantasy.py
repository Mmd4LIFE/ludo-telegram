"""Fantasy Arena: mana, hands, draft, costs, events, champions, bots, privacy.

Run with:  cd backend && python -m app.tests.test_fantasy
Lobby/mode filtering needs the migrated database and is skipped when it isn't there.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from app.game.fantasy import (
    CHAMPIONS,
    DRAFT_ROUNDS,
    FantasyRules,
    init_state,
)
from app.ludo.board import BASE, Color
from app.ludo.rules import apply_move, initial_state, legal_moves, register_roll
from app.ludo.state import GameState, Phase


@dataclass(frozen=True)
class CardInfo:
    id: str
    effect: str
    rarity: str
    cost: int
    fantasy_only: bool


def _check(name: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(f"FAILED: {name}")
    print(f"  ok  {name}")


def _cards() -> dict[str, CardInfo]:
    specs = [
        ("summon", "summon", "common", 1, False),
        ("shield", "shield", "uncommon", 2, False),
        ("lock", "lock", "rare", 3, False),
        ("direct_kick", "direct_kick", "rare", 3, False),
        ("mana_surge", "mana_surge", "common", 1, True),
        ("pickpocket", "pickpocket", "uncommon", 2, True),
        ("hex", "hex", "rare", 3, True),
        ("time_warp", "time_warp", "epic", 4, True),
        ("freeze", "lock", "uncommon", 2, False),
    ]
    return {
        cid: CardInfo(id=cid, effect=eff, rarity=rar, cost=cost, fantasy_only=fo)
        for cid, eff, rar, cost, fo in specs
    }


def _rules(seed: int = 1, **cfg) -> FantasyRules:
    defaults = {
        "FANTASY_MANA_MAX": 10,
        "FANTASY_HAND_MAX": 5,
        "FANTASY_DRAW_EVERY": 3,
        "FANTASY_EVENT_EVERY": 8,
        "FANTASY_COST_COMMON": 1,
        "FANTASY_COST_UNCOMMON": 2,
        "FANTASY_COST_RARE": 3,
        "FANTASY_COST_EPIC": 4,
    }
    defaults.update(cfg)
    return FantasyRules(defaults, _cards(), random.Random(seed))


def _arena(n: int = 2, champions: dict[int, str] | None = None, seed: int = 1) -> tuple[GameState, FantasyRules]:
    s = initial_state(n)
    init_state(s, champions or {i: "knight" for i in range(n)}, rng=random.Random(seed))
    return s, _rules(seed)


def _classic_unchanged() -> None:
    s = initial_state(2)
    _check("classic GameState.fantasy is None", s.fantasy is None)
    d = s.to_dict()
    _check("classic serialises fantasy as None", d.get("fantasy") is None)
    s2 = GameState.from_dict(d)
    _check("classic round-trip stays None", s2.fantasy is None)


def _mana_gain_and_cap() -> None:
    s, fr = _arena(champions={0: "rogue", 1: "sage"})
    _check("non-knight starts at 0", fr.mana(s, 1) == 0)
    s2, fr2 = _arena(champions={0: "knight", 1: "rogue"})
    _check("knight starts with 2 mana", fr2.mana(s2, 0) == 2)
    gained = fr.gain_mana(s, 1, 3)
    _check("gain_mana returns the amount added", gained == 3)
    fr.gain_mana(s, 1, 20)
    _check("mana is capped at 10", fr.mana(s, 1) == 10)
    s.fantasy["mana"][0] = 3
    _check("rogue capture is +2", fr.on_capture(s, 0, 1) == 2)
    s.fantasy["mana"][1] = 0
    _check("home is +2", fr.on_home(s, 1) == 2)


def _draw_cadence_and_hand_cap() -> None:
    s, fr = _arena(champions={0: "sage", 1: "rogue"})
    s.phase = Phase.ROLL
    s.current = 0
    s.turn = 1
    r1 = fr.start_of_turn(s, 0)
    _check("sage does not draw on turn 1", r1["drew"] == [])
    s.turn = 2
    s.phase = Phase.ROLL
    r2 = fr.start_of_turn(s, 0)
    _check("sage draws on every 2nd own turn", len(r2["drew"]) == 1)

    s3, fr3 = _arena(champions={0: "rogue", 1: "knight"})
    s3.turn = 1
    fr3.start_of_turn(s3, 0)
    s3.turn = 2
    fr3.start_of_turn(s3, 0)
    s3.turn = 3
    r3 = fr3.start_of_turn(s3, 0)
    _check("default draw every 3rd own turn", len(r3["drew"]) == 1)

    s.fantasy["hand"][0] = ["a", "b", "c", "d", "e"]
    discarded = fr.add_to_hand(s, 0, "newest")
    _check("full hand discards the oldest card", discarded == ["a"])
    _check("newest sits at the end", s.fantasy["hand"][0][-1] == "newest")
    _check("hand stays at the cap", len(s.fantasy["hand"][0]) == 5)


def _costs_and_trickster() -> None:
    s, fr = _arena(champions={0: "trickster", 1: "knight"})
    _check("common cost is 1", fr.cost(s, 1, "mana_surge") == 1)
    _check("uncommon cost is 2", fr.cost(s, 1, "pickpocket") == 2)
    _check("rare cost is 3", fr.cost(s, 1, "hex") == 3)
    _check("epic cost is 4", fr.cost(s, 1, "time_warp") == 4)
    _check("trickster discounts a targeted card by 1", fr.cost(s, 0, "hex") == 2)
    _check("trickster does not discount untargeted", fr.cost(s, 0, "mana_surge") == 1)
    _check("knight shield bonus is 1 extra round", fr.shield_bonus(s, 1) == 1)
    _check("other champions have no shield bonus", fr.shield_bonus(s, 0) == 0)


def _draft_yields_three() -> None:
    s, fr = _arena()
    fr.start_draft(s)
    for _round in range(DRAFT_ROUNDS):
        pending = fr.draft_pending(s)
        _check(f"everyone still to pick in round {_round + 1}", set(pending) == {0, 1})
        for seat in pending:
            card = fr.draft_pick(s, seat, 0)
            _check("a pick returns a card", card is not None)
        done = fr.draft_advance(s)
        if _round < DRAFT_ROUNDS - 1:
            _check("draft continues", done is False)
        else:
            _check("draft finishes after 3 rounds", done is True)
    _check("each seat drafted 3 cards", all(len(fr.hand(s, i)) == 3 for i in (0, 1)))
    _check("draft flag is cleared", s.fantasy["draft"] is None and s.fantasy["drafted"] is True)


def _one_cast_per_turn() -> None:
    s, fr = _arena()
    s.phase = Phase.ROLL
    s.current = 0
    s.fantasy["hand"][0] = ["mana_surge", "hex"]
    s.fantasy["mana"][0] = 10
    _check("first cast is legal", fr.can_cast(s, 0, "mana_surge") is None)
    fr.pay(s, 0, "mana_surge")
    _check("second cast this turn is blocked", fr.can_cast(s, 0, "hex") == "already_cast")
    s.phase = Phase.MOVE
    _check("cannot cast outside ROLL", fr.can_cast(s, 0, "hex") == "not_your_roll")


def _fog_eclipse_tailwind() -> None:
    # YELLOW progress 34 lands on absolute 8 (RED's star). RED sits there.
    s = initial_state(2, seat_colors=[Color.RED, Color.YELLOW])
    init_state(s, {0: "knight", 1: "rogue"})
    s.players[0].tokens[0] = 8
    s.players[1].tokens[0] = 29
    s.current = 1
    s.fantasy["event"] = {"id": "fog", "until_turn": 99}
    register_roll(s, 5)
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("Fog blocks captures", moves and moves[0].captures == ())

    s = initial_state(2, seat_colors=[Color.RED, Color.YELLOW])
    init_state(s, {0: "knight", 1: "rogue"})
    s.players[0].tokens[0] = 8
    s.players[1].tokens[0] = 29
    s.current = 1
    s.fantasy["event"] = {"id": "eclipse", "until_turn": 99}
    register_roll(s, 5)
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("Eclipse makes the star safe for everyone", moves and moves[0].captures == ())

    s = initial_state(2)
    init_state(s, {0: "knight", 1: "rogue"})
    s.players[0].tokens[0] = 4
    s.fantasy["event"] = {"id": "tailwind", "until_turn": 99}
    register_roll(s, 3)
    moves = [m for m in legal_moves(s) if m.token_index == 0]
    _check("Tailwind adds +1 step", moves and moves[0].dst == 8)
    s2 = initial_state(2)
    init_state(s2, {0: "knight", 1: "rogue"})
    s2.fantasy["event"] = {"id": "tailwind", "until_turn": 99}
    register_roll(s2, 5)
    _check("Tailwind does not release on a 5 that becomes 6 movement", legal_moves(s2) == [])
    s3 = initial_state(2)
    init_state(s3, {0: "knight", 1: "rogue"})
    s3.fantasy["event"] = {"id": "tailwind", "until_turn": 99}
    register_roll(s3, 6)
    _check("a real six still releases under Tailwind", any(m.releases_from_base for m in legal_moves(s3)))


def _quake_harvest_hex_warp_pickpocket() -> None:
    s, fr = _arena()
    s.players[0].tokens = [5, 1, BASE, 51]
    s.players[1].tokens = [0, 10, BASE, BASE]
    eid = fr.apply_event(s, "quake")
    _check("quake id", eid == "quake")
    _check("quake steps a ring token back 2", s.players[0].tokens[0] == 3)
    _check("quake clamps at start (progress 0)", s.players[0].tokens[1] == 0)
    _check("quake leaves the yard alone", s.players[0].tokens[2] == BASE)
    _check("quake leaves the home column alone", s.players[0].tokens[3] == 51)
    _check("quake clamps a token already on its start", s.players[1].tokens[0] == 0)

    s, fr = _arena()
    s.fantasy["mana"] = {0: 1, 1: 1}
    fr.apply_event(s, "harvest")
    _check("harvest grants +2 mana", fr.mana(s, 0) == 3 and fr.mana(s, 1) == 3)

    s, fr = _arena()
    s.add_eff("hex", 0, 1)
    _check("hex caps a 6 at 3", fr.adjust_roll(s, 0, 6) == 3)
    _check("hex is one charge", s.eff("hex", 0) == 0)
    _check("no hex leaves the face alone", fr.adjust_roll(s, 0, 6) == 6)

    s, fr = _arena()
    s.current = 1
    s.phase = Phase.ROLL
    s.fantasy["time_warp"] = 0
    _check("time warp is owed", fr.consume_time_warp(s, 0) is True)
    _check("time warp clears", s.fantasy["time_warp"] is None)
    _check("time warp bumps the turn counter", s.turn == 1)

    s, fr = _arena()
    s.fantasy["hand"][0] = []
    s.fantasy["hand"][1] = ["shield", "hex"]
    hit = fr.apply_arena_effect(s, 0, "pickpocket", 1)
    _check("pickpocket names the rival", hit == 1)
    _check("pickpocket moves a card", len(fr.hand(s, 0)) == 1 and len(fr.hand(s, 1)) == 1)
    _check("the stolen card left the rival", fr.hand(s, 0)[0] in ("shield", "hex"))


def _bots_never_overpay() -> None:
    s, fr = _arena()
    s.phase = Phase.ROLL
    s.current = 0
    s.fantasy["hand"][0] = ["time_warp", "hex"]
    s.fantasy["mana"][0] = 0
    _check("bot holds when it cannot pay", fr.bot_pick_cast(s, 0) is None)
    s.fantasy["mana"][0] = 1
    s.fantasy["hand"][0] = ["mana_surge", "time_warp"]
    pick = fr.bot_pick_cast(s, 0)
    _check("bot only picks an affordable card", pick is None or fr.cost(s, 0, pick) <= 1)
    s.players[0].tokens = [BASE, BASE, BASE, BASE]
    s.fantasy["mana"][0] = 5
    s.fantasy["hand"][0] = ["summon"]
    _check("bot summons when everything is in the yard", fr.bot_pick_cast(s, 0) == "summon")


def _render_hides_hands() -> None:
    s, fr = _arena()
    s.fantasy["hand"][0] = ["hex", "mana_surge"]
    s.fantasy["hand"][1] = ["time_warp"]
    s.fantasy["mana"][0] = 4
    view = fr.view(s, 0)
    _check("viewer sees their own cards", [c["id"] for c in view["hand"]] == ["hex", "mana_surge"])
    _check("rivals are counts only", view["hand_counts"]["1"] == 1)
    _check("rival cards are not listed", all(c["id"] != "time_warp" for c in view["hand"]))
    hidden = fr.view(s, 1)
    _check("the other viewer sees only their card", [c["id"] for c in hidden["hand"]] == ["time_warp"])
    spect = fr.view(s, None)
    _check("a spectator gets an empty hand", spect["hand"] == [])
    _check("a spectator still sees counts", spect["hand_counts"]["0"] == 2)


def _fantasy_only_filter() -> None:
    cards = _cards()
    classic = {cid: c.effect for cid, c in cards.items() if not c.fantasy_only}
    arena = {cid: c.effect for cid, c in cards.items()}
    _check("classic draws skip fantasy_only", "hex" not in classic and "mana_surge" not in classic)
    _check("arena draws include fantasy_only", "hex" in arena and "summon" in arena)


def _play_fantasy_game(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.choice([2, 3, 4])
    s = initial_state(n)
    champs = {i: rng.choice(sorted(CHAMPIONS)) for i in range(n)}
    init_state(s, champs, rng=rng)
    fr = FantasyRules(
        {
            "FANTASY_MANA_MAX": 10, "FANTASY_HAND_MAX": 5, "FANTASY_DRAW_EVERY": 3,
            "FANTASY_EVENT_EVERY": 8,
        },
        _cards(),
        rng,
    )
    fr.start_draft(s)
    while s.fantasy.get("draft"):
        for seat in fr.draft_pending(s):
            fr.draft_pick(s, seat, None)
        fr.draft_advance(s)
    guard = 0
    while s.phase is not Phase.FINISHED:
        guard += 1
        if guard > 200_000:
            raise AssertionError(f"fantasy game {seed} did not terminate")
        seat = s.current
        if s.phase is Phase.ROLL:
            fr.start_of_turn(s, seat)
            pick = fr.bot_pick_cast(s, seat)
            if pick and fr.can_cast(s, seat, pick) is None:
                cost = fr.cost(s, seat, pick)
                assert cost <= fr.mana(s, seat)
                effect = fr.effect_of(pick)
                fr.pay(s, seat, pick)
                if effect in ("mana_surge", "pickpocket", "hex", "time_warp"):
                    fr.apply_arena_effect(s, seat, effect, None)
            face = rng.randint(1, 6)
            face = fr.adjust_roll(s, seat, face)
            register_roll(s, face)
            if s.roll_face == 6:
                fr.draw(s, seat, 1)
            continue
        if s.phase is not Phase.MOVE:
            continue
        from app.ludo.bots import choose_move

        moves = legal_moves(s)
        event = s.realm_event
        for m in moves:
            if m.releases_from_base:
                continue
            if s.eff("vertigo", s.current) > 0:
                continue
            step = s.die + (1 if event == "tailwind" else 0)
            assert m.dst - m.src == step, (m, s.die, event)
        res = apply_move(s, choose_move(s, moves, rng) if moves else None)
        if res.captured:
            fr.on_capture(s, seat, len(res.captured))
        if res.reached_home:
            fr.on_home(s, seat)
            fr.draw(s, seat, 1)
        if fr.consume_time_warp(s, seat):
            s.current = seat
            s.phase = Phase.ROLL
            s.die = None
            s.roll_face = None
    assert len(s.ranking) == len(s.players)


def test_fuzz_fantasy_games() -> None:
    for seed in range(40):
        _play_fantasy_game(seed)
    _check("40 random fantasy games terminated cleanly", True)


def run_all() -> None:
    for fn in (
        _classic_unchanged,
        _mana_gain_and_cap,
        _draw_cadence_and_hand_cap,
        _costs_and_trickster,
        _draft_yields_three,
        _one_cast_per_turn,
        _fog_eclipse_tailwind,
        _quake_harvest_hex_warp_pickpocket,
        _bots_never_overpay,
        _render_hides_hands,
        _fantasy_only_filter,
        test_fuzz_fantasy_games,
    ):
        print(f"\n{fn.__name__}:")
        fn()
    print("\nAll fantasy tests passed.")


# --- lobby filtering (needs DB) --------------------------------------------
def _db_available() -> bool:
    try:
        import psycopg2
        from app.config import settings
        psycopg2.connect(
            host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD, dbname=settings.POSTGRES_DB, connect_timeout=2,
        ).close()
        return True
    except Exception:  # noqa: BLE001
        return False


try:
    import pytest
except ImportError:  # pragma: no cover
    pytest = None  # type: ignore

if pytest is not None:
    needs_db = pytest.mark.skipif(not _db_available(), reason="no database")

    async def _lobby_scenario() -> None:
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy import select

        from app.database import SessionLocal
        from app.main import app
        from app.models import Match, MatchStatus

        async def login(client, tid, name):
            res = await client.post(
                "/api/auth/dev",
                json={"telegram_id": tid, "first_name": name, "username": name.lower()},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            return body["token"], body["user"]

        def auth(token):
            return {"Authorization": f"Bearer {token}"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token, user = await login(client, 920001, "ArenaHost")
            async with SessionLocal() as session:
                rows = (
                    await session.execute(
                        select(Match).where(
                            Match.created_by == user["id"],
                            Match.status.in_((MatchStatus.WAITING, MatchStatus.PLAYING)),
                        )
                    )
                ).scalars().all()
                for m in rows:
                    m.status = MatchStatus.ABANDONED
                await session.commit()

            classic = await client.post(
                "/api/matches", headers=auth(token),
                json={"max_players": 2, "is_public": True, "mode": "classic"},
            )
            fantasy = await client.post(
                "/api/matches", headers=auth(token),
                json={"max_players": 2, "is_public": True, "mode": "fantasy"},
            )
            assert classic.status_code == 200, classic.text
            assert fantasy.status_code == 200, fantasy.text
            assert classic.json()["mode"] == "classic"
            assert fantasy.json()["mode"] == "fantasy"
            assert fantasy.json()["seats"][0].get("champion") in (None, *CHAMPIONS)

            champ = await client.post(
                f"/api/matches/{fantasy.json()['code']}/champion",
                headers=auth(token),
                json={"champion": "sage"},
            )
            assert champ.status_code == 200, champ.text
            mine = next(s for s in champ.json()["seats"] if s["user_id"] == user["id"])
            assert mine["champion"] == "sage"

            cl = await client.get("/api/matches/lobby?mode=classic", headers=auth(token))
            fa = await client.get("/api/matches/lobby?mode=fantasy", headers=auth(token))
            assert cl.status_code == 200 and fa.status_code == 200
            classic_codes = {m["code"] for m in cl.json()["waiting"] + cl.json()["mine"]}
            fantasy_codes = {m["code"] for m in fa.json()["waiting"] + fa.json()["mine"]}
            assert classic.json()["code"] in classic_codes
            assert fantasy.json()["code"] not in classic_codes
            assert fantasy.json()["code"] in fantasy_codes
            assert classic.json()["code"] not in fantasy_codes

            arena = await client.get("/api/matches/arena", headers=auth(token))
            assert arena.status_code == 200, arena.text
            body = arena.json()
            assert {c["id"] for c in body["champions"]} == set(CHAMPIONS)

    @needs_db
    @pytest.mark.asyncio
    async def test_lobby_filters_by_mode():
        await _lobby_scenario()


if __name__ == "__main__":
    run_all()
