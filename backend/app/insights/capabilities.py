"""The capability registry — the ONLY way the assistant touches data.

Each capability is a hand-written, typed, read-only unit of analytics. No capability ever
selects ``telegram_id`` (or any secret). The model never authors SQL; it only picks a
capability name and fills typed params, which the router validates before calling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User, Match, MatchSeat, MatchStatus, DiceRoll, Knockout, CardDraw,
)

Handler = Callable[[AsyncSession, dict], Awaitable[dict]]


@dataclass
class Capability:
    name: str
    summary: str
    params: dict
    handler: Handler
    examples: list[dict] = field(default_factory=list)


def _between(col, frm: datetime | None, to: datetime | None):
    conds = []
    if frm is not None:
        conds.append(col >= frm)
    if to is not None:
        conds.append(col < to)
    return conds


async def _count(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar_one() or 0)


# ---- handlers --------------------------------------------------------------
async def h_users_count(session: AsyncSession, p: dict) -> dict:
    stmt = select(func.count()).select_from(User).where(User.is_bot.is_(False))
    for c in _between(User.created_at, p.get("frm"), p.get("to")):
        stmt = stmt.where(c)
    return {"count": await _count(session, stmt)}


async def h_users_wins(session: AsyncSession, p: dict) -> dict:
    # a win = a finished match where this user placed 1st; time basis = when the match
    # finished (its last update, since matches carry no explicit finished_at yet).
    stmt = (
        select(func.count())
        .select_from(MatchSeat)
        .join(Match, Match.id == MatchSeat.match_id)
        .where(
            MatchSeat.user_id == p["user_id"],
            MatchSeat.place == 1,
            Match.status == MatchStatus.FINISHED,
        )
    )
    for c in _between(Match.updated_at, p.get("frm"), p.get("to")):
        stmt = stmt.where(c)
    return {"wins": await _count(session, stmt)}


async def h_users_live_matches(session: AsyncSession, p: dict) -> dict:
    stmt = (
        select(func.count(func.distinct(Match.id)))
        .select_from(Match)
        .join(MatchSeat, MatchSeat.match_id == Match.id)
        .where(MatchSeat.user_id == p["user_id"], Match.status == MatchStatus.PLAYING)
    )
    return {"live_matches": await _count(session, stmt)}


async def h_matches_by_status(session: AsyncSession, p: dict) -> dict:
    stmt = select(Match.status, func.count()).group_by(Match.status)
    for c in _between(Match.created_at, p.get("frm"), p.get("to")):
        stmt = stmt.where(c)
    counts = {s.value: 0 for s in MatchStatus}
    for st, n in (await session.execute(stmt)).all():
        counts[st.value if hasattr(st, "value") else str(st)] = int(n)
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


_EVENT_MODEL = {"dice_rolls": DiceRoll, "knockouts": Knockout, "card_draws": CardDraw}


async def h_events_count(session: AsyncSession, p: dict) -> dict:
    model = _EVENT_MODEL.get(p.get("kind", ""))
    if model is None:
        return {"count": 0, "kind": p.get("kind")}
    stmt = select(func.count()).select_from(model)
    for c in _between(model.created_at, p.get("frm"), p.get("to")):
        stmt = stmt.where(c)
    return {"count": await _count(session, stmt), "kind": p["kind"]}


async def h_economy_coins(session: AsyncSession, p: dict) -> dict:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(User.coins), 0)).where(User.is_bot.is_(False))
        )
    ).scalar_one()
    return {"coins": int(total or 0)}


async def h_leaderboard_top(session: AsyncSession, p: dict) -> dict:
    col = {"wins": User.games_won, "knockouts": User.captures_dealt}.get(p.get("metric", "wins"), User.games_won)
    n = max(1, min(int(p.get("n", 5)), 25))
    rows = (
        await session.execute(
            select(User.id, User.first_name, col.label("v"))
            .where(User.is_bot.is_(False))
            .order_by(col.desc())
            .limit(n)
        )
    ).all()
    return {
        "metric": p.get("metric", "wins"),
        "top": [{"id": r.id, "name": r.first_name or "Player", "value": int(r.v or 0)} for r in rows],
    }


# ---- registry --------------------------------------------------------------
REGISTRY: dict[str, Capability] = {
    c.name: c
    for c in [
        Capability("users.count", "Total (or newly-created) real players.",
                   {"period": "enum"}, h_users_count,
                   [{"q": "how many users do we have?", "call": {"period": "all"}}]),
        Capability("users.wins", "How many games a specific player won in a period.",
                   {"user_id": "int", "period": "enum"}, h_users_wins,
                   [{"q": "how many wins does Nila have last week?", "call": {"period": "7d"}}]),
        Capability("users.live_matches", "How many games a player is currently in.",
                   {"user_id": "int"}, h_users_live_matches, []),
        Capability("matches.count_by_status", "Matches by status (waiting/playing/finished/abandoned) in a period.",
                   {"period": "enum"}, h_matches_by_status,
                   [{"q": "how many matches finished today?", "call": {"period": "today"}}]),
        Capability("events.count", "Volume of dice rolls / knockouts / card draws in a period.",
                   {"kind": "enum", "period": "enum"}, h_events_count,
                   [{"q": "how many cards were drawn yesterday?", "call": {"kind": "card_draws", "period": "yesterday"}}]),
        Capability("economy.coins", "Total coins in circulation.", {}, h_economy_coins, []),
        Capability("leaderboard.top", "Top players by wins or knockouts.",
                   {"metric": "enum", "n": "int"}, h_leaderboard_top,
                   [{"q": "top 5 players by wins", "call": {"metric": "wins", "n": 5}}]),
    ]
}


def catalog() -> list[dict]:
    return [
        {"name": c.name, "summary": c.summary, "params": c.params, "examples": c.examples}
        for c in REGISTRY.values()
    ]
