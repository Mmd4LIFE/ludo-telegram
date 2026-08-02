"""Admin-tunable runtime config, backed by the append-only ``app_configs`` table.

Only whitelisted keys are settable. The current value of a key is the newest non-deleted
row; if none exists, the code default (from ``settings``) applies. The game runtime reads
a snapshot of these once when a match starts, so changes take effect for new games.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.models import AppConfig


# key -> display metadata + default (pulled from settings) + integer bounds
TUNABLE: dict[str, dict] = {
    "COULDVE_PER_CARD": {
        "label": "Missed knocks per bonus card",
        "help": "Pass up this many captures in a game to earn a fantasy-card draw.",
        "default": settings.COULDVE_PER_CARD, "min": 1, "max": 10,
    },
    "TURN_TIMEOUT_SECONDS": {
        "label": "Turn timer (seconds)",
        "help": "How long a player has to roll or move before it's auto-played.",
        "default": settings.TURN_TIMEOUT_SECONDS, "min": 5, "max": 120,
    },
    "MAX_MISSED_TURNS": {
        "label": "Timeouts before auto-kick",
        "help": "Miss this many rolls in a row and you're removed from the game.",
        "default": settings.MAX_MISSED_TURNS, "min": 1, "max": 10,
    },
    "CARD_PICK_SECONDS": {
        "label": "Card pick time (seconds)",
        "help": "How long a player has to pick a fantasy card / target.",
        "default": settings.CARD_PICK_SECONDS, "min": 5, "max": 60,
    },
}


async def current_values(session: AsyncSession) -> dict[str, int]:
    """Resolve every tunable key to its current int value (DB latest, else code default)."""
    resolved: dict[str, int] = {k: int(m["default"]) for k, m in TUNABLE.items()}
    rows = (
        await session.execute(
            select(AppConfig)
            .where(AppConfig.key.in_(list(TUNABLE)), AppConfig.deleted_at.is_(None))
            .order_by(AppConfig.id)
        )
    ).scalars().all()
    for row in rows:  # ordered by id → the last one wins (newest value)
        try:
            resolved[row.key] = int(row.value)
        except (TypeError, ValueError):
            pass
    return resolved


async def runtime_config() -> dict[str, int]:
    """A fresh snapshot of the tunables, in its own session (called at match start)."""
    async with SessionLocal() as session:
        return await current_values(session)
