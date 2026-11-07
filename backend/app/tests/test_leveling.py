"""Leveling: the XP curve, runtime XP rules, and (with a database) awarding."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.ludo.board import Color
from app.ludo.rules import initial_state
from app.services.leveling import (
    MAX_LEVEL,
    level_for_xp,
    level_reward,
    progress,
    roadmap,
    xp_for_level,
)


# --- curve ------------------------------------------------------------------
def test_curve_is_monotonic_and_round_trips():
    prev = -1
    for n in range(1, MAX_LEVEL + 1):
        need = xp_for_level(n)
        assert need > prev
        prev = need
        assert level_for_xp(need) == n
        if n > 1:
            assert level_for_xp(need - 1) == n - 1
    assert xp_for_level(1) == 0
    assert xp_for_level(2) == 100


def test_max_level_clamps():
    assert level_for_xp(10**9) == MAX_LEVEL
    p = progress(10**9)
    assert p["max"] is True and p["needed"] == 0 and p["next_at"] is None


def test_progress_inside_a_level():
    p = progress(150)  # level 2 starts at 100, level 3 at 283
    assert p["level"] == 2
    assert p["into"] == 50
    assert p["needed"] == xp_for_level(3) - xp_for_level(2)
    assert p["next_at"] == xp_for_level(3)
    assert progress(0)["level"] == 1


def test_rewards_and_milestones():
    assert level_reward(1) == 0
    assert level_reward(2) == 50 + 10 * 2
    assert level_reward(5) == (50 + 10 * 5) * 3
    cfg = {"LEVEL_REWARD_BASE": 0, "LEVEL_REWARD_STEP": 1}
    assert level_reward(4, cfg) == 4
    road = roadmap(0, 5)
    assert [r["level"] for r in road] == [2, 3, 4, 5, 6]
    assert road[3]["milestone"] is True


def test_backfill_levels_match_existing_xp():
    # Old rule: +100 per win, +25 otherwise. 10 wins + 20 losses = 1500 XP.
    assert level_for_xp(1500) == 7
    assert level_for_xp(25) == 1


# --- runtime rules ----------------------------------------------------------
def _runtime(is_bot_table: bool = False):
    from app.game.runtime import MatchRuntime

    seats = [
        SimpleNamespace(seat_index=0, color="RED", user_id=11, is_bot=False),
        SimpleNamespace(seat_index=1, color="YELLOW", user_id=None, is_bot=True),
    ]
    match = SimpleNamespace(
        id=9, code="LVL01", max_players=2, entry_fee=0, is_bot_table=is_bot_table,
        state=initial_state(2, seat_colors=[Color.RED, Color.YELLOW]).to_dict(),
        seats=seats,
    )
    return MatchRuntime(match)


def test_action_xp_is_queued_for_humans_only():
    rt = _runtime()
    rt._note_xp(rt._human_uid(0), "knock", 2)
    rt._note_xp(rt._human_uid(1), "knock")   # bot seat -> uid None
    rt._note_xp(11, "home")
    assert rt.stat_xp == {11: {"knock": 2 * rt.cfg["XP_KNOCK"], "home": rt.cfg["XP_HOME"]}}


def test_bot_table_scales_xp():
    rt = _runtime(is_bot_table=True)
    assert rt._xp_scaled(100) == rt.cfg["XP_BOT_TABLE_PCT"]
    assert _runtime()._xp_scaled(100) == 100


def test_placement_xp_by_place_and_table_size():
    rt = _runtime()
    cfg = rt.cfg
    assert rt.placement_xp(1, 4, 4) == cfg["XP_PLACE_1"]
    assert rt.placement_xp(2, 4, 4) == cfg["XP_PLACE_2"]
    assert rt.placement_xp(3, 4, 4) == cfg["XP_PLACE_3"]
    assert rt.placement_xp(4, 4, 4) == cfg["XP_PLACE_4"]
    # in a 2-human game, 2nd is last place and smaller tables pay 67%
    assert rt.placement_xp(2, 2, 2) == round(cfg["XP_PLACE_4"] * 4 / 6)
    assert rt.placement_xp(1, 2, 2) == round(cfg["XP_PLACE_1"] * 4 / 6)


def test_level_up_note_is_recorded():
    from app.services.xp import AwardResult

    rt = _runtime()
    rt._record_award(11, "placement", AwardResult(gained=120, level_before=1, level_after=2,
                                                  coins=70, levels=[2]))
    rt._record_award(11, "knock", AwardResult(gained=0, capped=True))
    assert rt.game_xp == {11: {"placement": 120}}
    assert 11 in rt.xp_capped
    assert rt._level_ups == [{"user_id": 11, "level": 2, "coins": 70, "levels": [2]}]
    payload = rt.render()
    assert payload["seat_xp"] == {"0": {"placement": 120}}
    assert payload["seat_level_ups"] == {"0": 1}
    assert payload["xp_capped_seats"] == [0]


def test_xp_payload_for_clickhouse():
    from datetime import datetime, timezone

    from app.analytics.clickhouse import xp_payload
    from app.models import XpEvent

    stamped = datetime(2026, 11, 7, 19, 0, tzinfo=timezone.utc)
    row = xp_payload(XpEvent(
        id=12, user_id=32, match_id=None, kind="placement", amount=120,
        ref="placement:9", created_at=stamped,
    ))
    assert row["match_id"] == 0 and row["ref"] == "placement:9"
    assert row["amount"] == 120 and row["created_at"] == "2026-11-07 19:00:00.000"


# --- awarding (needs the migrated database) ----------------------------------
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


needs_db = pytest.mark.skipif(not _db_available(), reason="no database")


async def _award_scenario() -> None:
    from sqlalchemy import delete, func, select

    from app.database import SessionLocal
    from app.models import BalanceHistory, LevelReward, User, XpEvent
    from app.services import xp as xp_service

    cfg = {"XP_DAILY_CAP": 50, "LEVEL_REWARD_BASE": 50, "LEVEL_REWARD_STEP": 10}
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.telegram_id == 920001))).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=920001, first_name="Leveler")
            s.add(user)
            await s.flush()
        await s.execute(delete(XpEvent).where(XpEvent.user_id == user.id))
        await s.execute(delete(LevelReward).where(LevelReward.user_id == user.id))
        user.xp, user.level = 0, 1
        await s.commit()
        uid = user.id

    async with SessionLocal() as s:
        # action XP stops at the daily cap
        r1 = await xp_service.award(s, uid, 40, "knock", cfg=cfg)
        r2 = await xp_service.award(s, uid, 40, "knock", cfg=cfg)
        r3 = await xp_service.award(s, uid, 40, "home", cfg=cfg)
        assert (r1.gained, r2.gained, r3.gained) == (40, 10, 0)
        assert r2.capped and r3.capped
        # placement is never capped, and crosses levels 2 and 3 in one grant
        r4 = await xp_service.award(s, uid, 300, "placement", match_id=None, ref="placement:t1", cfg=cfg)
        assert r4.gained == 300
        assert r4.levels == [2, 3] and r4.level_after == 3
        assert r4.coins == level_reward(2, cfg) + level_reward(3, cfg)
        # a settle retry with the same ref is a no-op
        r5 = await xp_service.award(s, uid, 300, "placement", ref="placement:t1", cfg=cfg)
        assert r5.gained == 0
        await s.commit()

    async with SessionLocal() as s:
        user = await s.get(User, uid)
        assert user.xp == 350 and user.level == 3
        paid = (
            await s.execute(
                select(func.count()).select_from(BalanceHistory).where(
                    BalanceHistory.user_id == uid, BalanceHistory.reason == "level_up"
                )
            )
        ).scalar_one()
        assert paid >= 2
        rewards = (
            await s.execute(select(LevelReward.level).where(LevelReward.user_id == uid))
        ).scalars().all()
        assert sorted(rewards) == [2, 3]


@needs_db
def test_award_caps_levels_and_is_idempotent():
    from app.database import engine

    async def run() -> None:
        try:
            await _award_scenario()
        finally:
            await engine.dispose()  # pooled connections must not outlive this loop

    asyncio.run(run())
