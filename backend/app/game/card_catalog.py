"""Process-local cache of enabled fantasy cards (id -> effect).

Avoids a DB round-trip on every home/could've card draw. Call ``invalidate()`` after
admin changes to the cards table so the next draw refreshes.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Card

logger = logging.getLogger("ludo.cards")

_lock = asyncio.Lock()
_effects: dict[str, str] | None = None


async def enabled_effects() -> dict[str, str]:
    """Return ``{card_id: effect}`` for all enabled cards."""
    global _effects
    if _effects is not None:
        return _effects
    async with _lock:
        if _effects is not None:
            return _effects
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Card.id, Card.effect).where(Card.enabled.is_(True))
                )
            ).all()
        _effects = {cid: eff for cid, eff in rows}
        logger.debug("card catalog loaded: %d enabled", len(_effects))
        return _effects


def invalidate() -> None:
    """Drop the cache so the next ``enabled_effects()`` reloads from the DB."""
    global _effects
    _effects = None
