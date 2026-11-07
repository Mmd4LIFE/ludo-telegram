"""Process-local cache of enabled fantasy cards.

Avoids a DB round-trip on every home/could've card draw and every Fantasy Arena cast.
Call ``invalidate()`` after admin changes to the cards table so the next draw refreshes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Card

logger = logging.getLogger("ludo.cards")


@dataclass(frozen=True)
class CardInfo:
    id: str
    effect: str
    rarity: str
    cost: int
    fantasy_only: bool


_lock = asyncio.Lock()
_cards: dict[str, CardInfo] | None = None


async def catalog() -> dict[str, CardInfo]:
    """Every enabled card, keyed by id."""
    global _cards
    if _cards is not None:
        return _cards
    async with _lock:
        if _cards is not None:
            return _cards
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(
                        Card.id, Card.effect, Card.rarity, Card.cost, Card.fantasy_only
                    ).where(Card.enabled.is_(True))
                )
            ).all()
        _cards = {
            cid: CardInfo(
                id=cid, effect=eff, rarity=rar or "common",
                cost=int(cost if cost is not None else 1), fantasy_only=bool(fo),
            )
            for cid, eff, rar, cost, fo in rows
        }
        logger.debug("card catalog loaded: %d enabled", len(_cards))
        return _cards


async def enabled_effects(mode: str = "classic") -> dict[str, str]:
    """Return ``{card_id: effect}`` for the cards a game of ``mode`` may draw.

    Arena-only cards (``fantasy_only``) never appear in a classic draw.
    """
    cards = await catalog()
    return {
        cid: c.effect
        for cid, c in cards.items()
        if mode == "fantasy" or not c.fantasy_only
    }


def invalidate() -> None:
    """Drop the cache so the next ``catalog()`` reloads from the DB."""
    global _cards
    _cards = None
