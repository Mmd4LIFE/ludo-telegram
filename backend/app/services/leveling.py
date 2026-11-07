"""The XP curve: total XP -> level, progress inside a level, and level-up coin rewards.

This is the only place levels are computed. The API returns ``progress()`` so the client
never re-derives the curve. Pure functions, no I/O.
"""
from __future__ import annotations

MAX_LEVEL = 50

# Coin reward defaults, overridable by the LEVEL_REWARD_* tunables.
DEFAULT_REWARD_BASE = 50
DEFAULT_REWARD_STEP = 10
MILESTONE_EVERY = 5
MILESTONE_MULT = 3


def xp_for_level(n: int) -> int:
    """Total XP needed to reach level ``n`` (level 1 needs 0)."""
    n = max(1, min(MAX_LEVEL, int(n)))
    return round(100 * (n - 1) ** 1.5)


def level_for_xp(xp: int) -> int:
    """The level a player with ``xp`` total experience has reached."""
    xp = max(0, int(xp or 0))
    level = 1
    while level < MAX_LEVEL and xp >= xp_for_level(level + 1):
        level += 1
    return level


def progress(xp: int) -> dict:
    """``{level, xp, into, needed, next_at, max}`` for a progress bar.

    ``into`` is XP earned inside the current level, ``needed`` is the size of that level.
    At the max level ``needed`` is 0 and ``next_at`` is None.
    """
    xp = max(0, int(xp or 0))
    level = level_for_xp(xp)
    start = xp_for_level(level)
    if level >= MAX_LEVEL:
        return {"level": level, "xp": xp, "into": xp - start, "needed": 0,
                "next_at": None, "max": True}
    nxt = xp_for_level(level + 1)
    return {"level": level, "xp": xp, "into": xp - start, "needed": nxt - start,
            "next_at": nxt, "max": False}


def level_reward(n: int, cfg: dict | None = None) -> int:
    """Coins paid once for reaching level ``n``. Every 5th level is a milestone bonus."""
    if n <= 1:
        return 0
    cfg = cfg or {}
    base = int(cfg.get("LEVEL_REWARD_BASE", DEFAULT_REWARD_BASE))
    step = int(cfg.get("LEVEL_REWARD_STEP", DEFAULT_REWARD_STEP))
    coins = base + step * n
    if n % MILESTONE_EVERY == 0:
        coins *= MILESTONE_MULT
    return max(0, coins)


def roadmap(xp: int, count: int = 10, cfg: dict | None = None) -> list[dict]:
    """The next ``count`` levels with the XP each needs and its coin reward."""
    level = level_for_xp(xp)
    out = []
    for n in range(level + 1, min(MAX_LEVEL, level + count) + 1):
        out.append({"level": n, "xp": xp_for_level(n), "coins": level_reward(n, cfg),
                    "milestone": n % MILESTONE_EVERY == 0})
    return out
