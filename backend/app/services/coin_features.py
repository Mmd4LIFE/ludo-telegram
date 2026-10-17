"""Coin sinks: card boosts, peek, insurance, hint, buy-card, dice-skin shop.

Costs are admin-tunable via ``appconfig.TUNABLE`` keys listed on each option.
"""
from __future__ import annotations

# effect_id -> list of boost tiers (applied after pick, before resolve)
CARD_BOOSTS: dict[str, list[dict]] = {
    "double_dice": [
        {
            "id": "x3",
            "label": "×3 movement (2 rolls)",
            "cost_key": "BOOST_DOUBLE_X3",
            "default_cost": 40,
            "params": {"mult": 3, "charges": 2},
        },
        {
            "id": "x4",
            "label": "×4 movement (2 rolls)",
            "cost_key": "BOOST_DOUBLE_X4",
            "default_cost": 80,
            "params": {"mult": 4, "charges": 2},
        },
    ],
    "boost": [
        {
            "id": "p5",
            "label": "+5 steps",
            "cost_key": "BOOST_STEPS_5",
            "default_cost": 30,
            "params": {"steps": 5},
        },
        {
            "id": "p8",
            "label": "+8 steps",
            "cost_key": "BOOST_STEPS_8",
            "default_cost": 60,
            "params": {"steps": 8},
        },
    ],
    "recall": [
        {
            "id": "r6",
            "label": "Send back 6",
            "cost_key": "BOOST_RECALL_6",
            "default_cost": 40,
            "params": {"steps": 6},
        },
        {
            "id": "r8",
            "label": "Send back 8",
            "cost_key": "BOOST_RECALL_8",
            "default_cost": 70,
            "params": {"steps": 8},
        },
    ],
    "shield": [
        {
            "id": "s5",
            "label": "+2 rounds (5 total)",
            "cost_key": "BOOST_SHIELD_EXTRA",
            "default_cost": 50,
            "params": {"rounds": 5},
        },
    ],
    "shield_all": [
        {
            "id": "sa7",
            "label": "+2 rounds (7 total)",
            "cost_key": "BOOST_SHIELD_ALL_EXTRA",
            "default_cost": 50,
            "params": {"rounds": 7},
        },
    ],
    "teleport": [
        {
            "id": "t2",
            "label": "Skip two stars",
            "cost_key": "BOOST_TELEPORT_2",
            "default_cost": 60,
            "params": {"warps": 2},
        },
    ],
    "summon": [
        {
            "id": "su2",
            "label": "Free 2 from base",
            "cost_key": "BOOST_SUMMON_2",
            "default_cost": 80,
            "params": {"count": 2},
        },
    ],
    "coins": [
        {
            "id": "j250",
            "label": "Jackpot 250",
            "cost_key": "BOOST_JACKPOT_250",
            "default_cost": 50,
            "params": {"coins": 250},
        },
    ],
}

# Dice skins: classic is free; others must be unlocked with coins.
DICE_SKIN_PRICES: dict[str, int] = {
    "classic": 0,
    "mint": 150,
    "ocean": 200,
    "night": 200,
    "gold": 250,
    "ruby": 300,
}

# Default costs for non-boost sinks (overridden by TUNABLE / cfg)
DEFAULT_PEEK_COST = 25
DEFAULT_INSURANCE_COST = 40
DEFAULT_INSURANCE_PAYOUT = 30
DEFAULT_HINT_COST = 30
DEFAULT_BUY_CARD_COST = 100


def boost_options_for(effect: str, cfg: dict) -> list[dict]:
    """Public boost list for the client (id, label, cost)."""
    out: list[dict] = []
    for tier in CARD_BOOSTS.get(effect, []):
        cost = int(cfg.get(tier["cost_key"], tier["default_cost"]))
        out.append({"id": tier["id"], "label": tier["label"], "cost": max(0, cost)})
    return out


def boost_params(effect: str, boost_id: str | None) -> dict:
    if not boost_id:
        return {}
    for tier in CARD_BOOSTS.get(effect, []):
        if tier["id"] == boost_id:
            return dict(tier["params"])
    return {}


def boost_cost(effect: str, boost_id: str, cfg: dict) -> int:
    for tier in CARD_BOOSTS.get(effect, []):
        if tier["id"] == boost_id:
            return max(0, int(cfg.get(tier["cost_key"], tier["default_cost"])))
    return 0
