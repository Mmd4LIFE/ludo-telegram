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
    # --- economy payouts (coins) -------------------------------------------
    "PAY_1ST_4": {
        "label": "1st place (4 humans)",
        "help": "Coins for finishing 1st when 4 humans played.",
        "default": 100, "min": 0, "max": 10000,
    },
    "PAY_1ST_3": {
        "label": "1st place (3 humans)",
        "help": "Coins for finishing 1st when 3 humans played.",
        "default": 75, "min": 0, "max": 10000,
    },
    "PAY_1ST_2": {
        "label": "1st place (2 humans)",
        "help": "Coins for finishing 1st when 2 humans played.",
        "default": 50, "min": 0, "max": 10000,
    },
    "PAY_2ND_4": {
        "label": "2nd place (4 humans)",
        "help": "Coins for finishing 2nd when 4 humans played.",
        "default": 75, "min": 0, "max": 10000,
    },
    "PAY_2ND_3": {
        "label": "2nd place (3 humans)",
        "help": "Coins for finishing 2nd when 3 humans played.",
        "default": 50, "min": 0, "max": 10000,
    },
    "PAY_3RD_4": {
        "label": "3rd place (4 humans)",
        "help": "Coins for finishing 3rd when 4 humans played.",
        "default": 50, "min": 0, "max": 10000,
    },
    "PAY_COULDVE": {
        "label": "Could've pass bonus",
        "help": "Coins granted each time a player passes up a legal capture.",
        "default": 10, "min": 0, "max": 1000,
    },
    "REROLL_COST": {
        "label": "Reroll cost",
        "help": "Coins to re-roll the die once per turn (after reveal, before move).",
        "default": 50, "min": 0, "max": 10000,
    },
    # --- coin sinks --------------------------------------------------------
    "PEEK_COST": {
        "label": "Peek cost",
        "help": "Reveal the four face-down cards before picking.",
        "default": 25, "min": 0, "max": 10000,
    },
    "INSURANCE_COST": {
        "label": "Insurance cost",
        "help": "Pay once per turn; if you get knocked, receive the insurance payout.",
        "default": 40, "min": 0, "max": 10000,
    },
    "INSURANCE_PAYOUT": {
        "label": "Insurance payout",
        "help": "Coins refunded when an insured player is knocked home.",
        "default": 30, "min": 0, "max": 10000,
    },
    "HINT_COST": {
        "label": "Move hint cost",
        "help": "Highlight the recommended legal move this turn.",
        "default": 30, "min": 0, "max": 10000,
    },
    "BUY_CARD_COST": {
        "label": "Buy bonus card",
        "help": "Spend coins to draw a fantasy card without waiting for could've.",
        "default": 100, "min": 0, "max": 10000,
    },
    "BOOST_DOUBLE_X3": {"label": "Boost Twin Dice ×3", "help": "", "default": 40, "min": 0, "max": 10000},
    "BOOST_DOUBLE_X4": {"label": "Boost Twin Dice ×4", "help": "", "default": 80, "min": 0, "max": 10000},
    "BOOST_STEPS_5": {"label": "Boost +5 steps", "help": "", "default": 30, "min": 0, "max": 10000},
    "BOOST_STEPS_8": {"label": "Boost +8 steps", "help": "", "default": 60, "min": 0, "max": 10000},
    "BOOST_RECALL_6": {"label": "Boost recall 6", "help": "", "default": 40, "min": 0, "max": 10000},
    "BOOST_RECALL_8": {"label": "Boost recall 8", "help": "", "default": 70, "min": 0, "max": 10000},
    "BOOST_SHIELD_EXTRA": {"label": "Boost shield rounds", "help": "", "default": 50, "min": 0, "max": 10000},
    "BOOST_SHIELD_ALL_EXTRA": {"label": "Boost mega-shield rounds", "help": "", "default": 50, "min": 0, "max": 10000},
    "BOOST_TELEPORT_2": {"label": "Boost teleport 2 stars", "help": "", "default": 60, "min": 0, "max": 10000},
    "BOOST_SUMMON_2": {"label": "Boost summon 2", "help": "", "default": 80, "min": 0, "max": 10000},
    "BOOST_JACKPOT_250": {"label": "Boost jackpot 250", "help": "", "default": 50, "min": 0, "max": 10000},
    # --- XP / levels -------------------------------------------------------
    "XP_PLACE_1": {
        "label": "XP: 1st place",
        "help": "XP for winning (full 4-human table; fewer humans pay 67-83%).",
        "default": 120, "min": 0, "max": 10000,
    },
    "XP_PLACE_2": {"label": "XP: 2nd place", "help": "XP for finishing 2nd.", "default": 80, "min": 0, "max": 10000},
    "XP_PLACE_3": {"label": "XP: 3rd place", "help": "XP for finishing 3rd.", "default": 55, "min": 0, "max": 10000},
    "XP_PLACE_4": {
        "label": "XP: last place",
        "help": "Completion XP for finishing last (never for leavers or kicked players).",
        "default": 35, "min": 0, "max": 10000,
    },
    "XP_KNOCK": {"label": "XP per knock", "help": "Action XP for sending a rival home.", "default": 15, "min": 0, "max": 1000},
    "XP_HOME": {"label": "XP per piece home", "help": "Action XP for bringing a piece home.", "default": 20, "min": 0, "max": 1000},
    "XP_CARD": {"label": "XP per card played", "help": "Action XP for playing a fantasy card.", "default": 10, "min": 0, "max": 1000},
    "XP_COULDVE": {"label": "XP per passed knock", "help": "Action XP for passing up a legal capture.", "default": 3, "min": 0, "max": 1000},
    "XP_CAST": {"label": "XP per card cast", "help": "Fantasy Arena: action XP for casting a card from your hand.", "default": 5, "min": 0, "max": 1000},
    "XP_DAILY_CAP": {
        "label": "Daily action XP cap",
        "help": "Most action XP a player can earn per UTC day. Placement XP is never capped.",
        "default": 600, "min": 0, "max": 100000,
    },
    "XP_BOT_TABLE_PCT": {
        "label": "Bot table XP (%)",
        "help": "Percent of normal XP earned in games against house bots.",
        "default": 25, "min": 0, "max": 100,
    },
    "LEVEL_REWARD_BASE": {
        "label": "Level-up coins (base)",
        "help": "Coins for a level-up = base + step x level; every 5th level pays triple.",
        "default": 50, "min": 0, "max": 100000,
    },
    "LEVEL_REWARD_STEP": {
        "label": "Level-up coins (per level)",
        "help": "Extra coins per level number added to the level-up reward.",
        "default": 10, "min": 0, "max": 100000,
    },
    # --- Fantasy Arena -----------------------------------------------------
    "FANTASY_MANA_MAX": {
        "label": "Arena: max mana",
        "help": "Mana pool ceiling. +1 per own turn, +1 per capture, +2 per piece home.",
        "default": 10, "min": 1, "max": 50,
    },
    "FANTASY_HAND_MAX": {
        "label": "Arena: hand size",
        "help": "Most cards a player can hold; a full hand discards its oldest card.",
        "default": 5, "min": 1, "max": 10,
    },
    "FANTASY_DRAW_EVERY": {
        "label": "Arena: draw every N turns",
        "help": "A card is drawn at the start of every Nth own turn (Sage: every 2nd).",
        "default": 3, "min": 1, "max": 10,
    },
    "FANTASY_EVENT_EVERY": {
        "label": "Arena: realm event every N turns",
        "help": "A realm event (Eclipse, Tailwind, Fog, Harvest, Quake) fires every N global turns. 0 disables.",
        "default": 8, "min": 0, "max": 100,
    },
    "FANTASY_COST_COMMON": {"label": "Arena: common card cost", "help": "Mana to cast a common card (when the card has no cost of its own).", "default": 1, "min": 0, "max": 20},
    "FANTASY_COST_UNCOMMON": {"label": "Arena: uncommon card cost", "help": "Mana to cast an uncommon card.", "default": 2, "min": 0, "max": 20},
    "FANTASY_COST_RARE": {"label": "Arena: rare card cost", "help": "Mana to cast a rare card.", "default": 3, "min": 0, "max": 20},
    "FANTASY_COST_EPIC": {"label": "Arena: epic card cost", "help": "Mana to cast an epic card.", "default": 4, "min": 0, "max": 20},
    # --- shop / Stars ------------------------------------------------------
    "COINS_PER_STAR": {
        "label": "Coins per Telegram Star",
        "help": "How many coins one Star buys. Packs are 50/100/250/500 Stars × this rate.",
        "default": 50, "min": 1, "max": 10000,
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
