"""Copy saved rolls and knocks into ClickHouse.

Postgres remains the source the scoreboard reads. ClickHouse is the analytics
copy (Metabase and other tools). Writes are best-effort: a missing server must
not stop a match.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger("ludo.analytics")

_SCHEMA = """
CREATE DATABASE IF NOT EXISTS {db};
CREATE TABLE IF NOT EXISTS {db}.rolls (
    id UInt64,
    match_id UInt64,
    user_id UInt64,
    seat UInt8,
    value UInt8,
    turn UInt32,
    created_at DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree ORDER BY id;
CREATE TABLE IF NOT EXISTS {db}.knocks (
    id UInt64,
    match_id UInt64,
    turn UInt32,
    attacker_user_id UInt64,
    attacker_seat UInt8,
    victim_user_id UInt64,
    victim_seat UInt8,
    taken UInt8,
    created_at DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree ORDER BY id
"""


def enabled() -> bool:
    return bool(settings.CLICKHOUSE_HOST)


def fold_face_counts(rows: list[tuple[int, int]]) -> dict[int, dict[int, int]]:
    """seat -> {face: count}. Every saved roll counts, including one later rerolled."""
    out: dict[int, dict[int, int]] = {}
    for seat, value in rows:
        hist = out.setdefault(int(seat), {})
        face = int(value)
        hist[face] = hist.get(face, 0) + 1
    return out


async def ensure_schema() -> None:
    if not enabled():
        return
    db = settings.CLICKHOUSE_DB
    for statement in _SCHEMA.format(db=db).split(";"):
        sql = statement.strip()
        if sql:
            await _exec(sql)


async def insert_rolls(rows: list[dict]) -> None:
    if not enabled() or not rows:
        return
    await _insert("rolls", rows)


async def insert_knocks(rows: list[dict]) -> None:
    if not enabled() or not rows:
        return
    await _insert("knocks", rows)


async def _insert(table: str, rows: list[dict]) -> None:
    import json

    body = "\n".join(json.dumps(row, default=str) for row in rows)
    await _exec(
        f"INSERT INTO {settings.CLICKHOUSE_DB}.{table} FORMAT JSONEachRow",
        body=body,
    )


async def _exec(query: str, body: str = "") -> None:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            res = await client.post(
                f"http://{settings.CLICKHOUSE_HOST}:{settings.CLICKHOUSE_PORT}/",
                params={"query": query},
                content=body.encode(),
                headers={
                    "X-ClickHouse-User": settings.CLICKHOUSE_USER,
                    "X-ClickHouse-Key": settings.CLICKHOUSE_PASSWORD,
                },
            )
            if res.status_code >= 300:
                logger.warning("clickhouse %s: %s", res.status_code, res.text[:300])
    except Exception:  # noqa: BLE001 — analytics must not break the match
        logger.warning("clickhouse unreachable", exc_info=True)
