"""Deterministic entity resolution: a name → candidate players.

Never an LLM. Tiered match (exact → prefix → contains) on first_name/username, bots
excluded. Returns the PUBLIC id + display fields only — ``telegram_id`` is never touched.
"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def resolve_users(session: AsyncSession, name: str, limit: int = 6) -> list[dict]:
    q = name.strip()
    if not q:
        return []
    base = select(
        User.id, User.first_name, User.username, User.level, User.games_won, User.last_seen_at
    ).where(User.is_bot.is_(False))

    lowered = q.lower()
    for cond in (
        or_(func.lower(User.first_name) == lowered, func.lower(User.username) == lowered),   # exact
        or_(func.lower(User.first_name).like(f"{lowered}%"), func.lower(User.username).like(f"{lowered}%")),  # prefix
        or_(User.first_name.ilike(f"%{q}%"), User.username.ilike(f"%{q}%")),                 # contains
    ):
        rows = (await session.execute(base.where(cond).limit(limit))).all()
        if rows:
            return [
                {
                    "id": r.id, "name": r.first_name or "Player", "username": r.username,
                    "level": r.level, "games_won": r.games_won,
                    "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
                }
                for r in rows
            ]
    return []
