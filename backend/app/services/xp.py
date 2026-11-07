"""Awarding XP: daily cap, once-only grants, and coin rewards on level-up.

Everything runs inside the caller's transaction so XP, level, and the level-up coins
commit (or roll back) together with the match state that earned them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LevelReward, User, XpEvent
from app.services import ledger
from app.services.leveling import level_for_xp, level_reward

PLACEMENT = "placement"
# In-game action XP. Only these count toward (and are limited by) the daily cap.
ACTION_KINDS = ("knock", "home", "card", "couldve")

DEFAULT_DAILY_CAP = 600


@dataclass
class AwardResult:
    gained: int = 0
    level_before: int = 1
    level_after: int = 1
    coins: int = 0
    capped: bool = False
    levels: list[int] = field(default_factory=list)

    @property
    def leveled_up(self) -> bool:
        return self.level_after > self.level_before


def _day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def daily_cap(cfg: dict | None) -> int:
    return max(0, int((cfg or {}).get("XP_DAILY_CAP", DEFAULT_DAILY_CAP)))


async def action_xp_today(session: AsyncSession, user_id: int) -> int:
    """Capped (action) XP this user has earned since 00:00 UTC."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(XpEvent.amount), 0)).where(
                XpEvent.user_id == user_id,
                XpEvent.kind.in_(ACTION_KINDS),
                XpEvent.created_at >= _day_start(),
            )
        )
    ).scalar_one()
    return int(total or 0)


async def award(
    session: AsyncSession,
    user_id: int,
    amount: int,
    kind: str,
    *,
    match_id: int | None = None,
    ref: str | None = None,
    cfg: dict | None = None,
) -> AwardResult:
    """Grant ``amount`` XP of ``kind``. A non-null ``ref`` makes the grant once-only."""
    user = (
        await session.execute(select(User).where(User.id == user_id).with_for_update())
    ).scalar_one_or_none()
    if user is None or user.is_bot:
        return AwardResult()
    before = int(user.level or 1)
    result = AwardResult(level_before=before, level_after=before)
    amount = int(amount)
    if amount <= 0:
        return result

    if ref is not None:
        seen = (
            await session.execute(
                select(XpEvent.id).where(XpEvent.user_id == user_id, XpEvent.ref == ref)
            )
        ).first()
        if seen is not None:
            return result

    if kind in ACTION_KINDS:
        room = daily_cap(cfg) - await action_xp_today(session, user_id)
        if room <= 0:
            result.capped = True
            return result
        if amount > room:
            amount = room
            result.capped = True

    event = XpEvent(
        user_id=user_id, match_id=match_id, kind=kind, amount=amount, ref=ref,
        created_at=datetime.now(timezone.utc),
    )
    session.add(event)
    await session.flush()  # SessionLocal has autoflush off; later cap/ref reads must see it
    from app.analytics import clickhouse as analytics
    analytics.queue_xp(session, [event])
    user.xp = int(user.xp or 0) + amount
    result.gained = amount

    after = max(before, level_for_xp(user.xp))
    if after > before:
        paid = set(
            (
                await session.execute(
                    select(LevelReward.level).where(
                        LevelReward.user_id == user_id,
                        LevelReward.level > before,
                        LevelReward.level <= after,
                    )
                )
            ).scalars()
        )
        for n in range(before + 1, after + 1):
            result.levels.append(n)
            if n in paid:
                continue
            coins = level_reward(n, cfg)
            session.add(LevelReward(user_id=user_id, level=n, coins=coins))
            await session.flush()
            if coins:
                await ledger.credit(
                    session, user_id, coins,
                    reason="level_up",
                    ref_type="level",
                    ref_id=n,
                    meta={"level": n, "match_id": match_id},
                )
                result.coins += coins
        user.level = after
    result.level_after = after
    return result
