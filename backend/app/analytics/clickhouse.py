"""Batched, retryable copy of score events into ClickHouse.

The game commits each roll to Postgres and to ``analytics_outbox`` in the same
transaction. A background worker drains that queue in batches about once a
second — the rate MergeTree likes — and deletes a row only after ClickHouse
accepts it. A crash or a down server just leaves the row to be sent again.
ReplacingMergeTree collapses a repeated source id.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.models import AnalyticsCursor, AnalyticsOutbox, DiceRoll, Knockout

logger = logging.getLogger("ludo.analytics")

_BATCH = 2000
_LEASE = timedelta(seconds=60)
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

_client: httpx.AsyncClient | None = None
_task: asyncio.Task | None = None
_schema_ready = False


def enabled() -> bool:
    return bool(settings.CLICKHOUSE_HOST)


def ch_time(dt: datetime | None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def backoff_seconds(attempts: int) -> int:
    """1s, 2s, 4s, … capped at 5 minutes. Never give up on the row."""
    return min(300, 2 ** min(max(attempts, 0), 8))


def roll_payload(row: DiceRoll) -> dict:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "user_id": row.user_id,
        "seat": row.seat,
        "value": row.value,
        "turn": row.turn,
        "created_at": ch_time(row.created_at),
    }


def knock_payload(row: Knockout) -> dict:
    return {
        "id": row.id,
        "match_id": row.match_id,
        "turn": row.turn,
        "attacker_user_id": row.attacker_user_id,
        "attacker_seat": row.attacker_seat,
        "victim_user_id": row.victim_user_id or 0,
        "victim_seat": row.victim_seat,
        "taken": 1 if row.taken else 0,
        "created_at": ch_time(row.created_at),
    }


def queue_rolls(session: AsyncSession, rows: list[DiceRoll]) -> None:
    _queue(session, "roll", [(r.id, roll_payload(r)) for r in rows])


def queue_knocks(session: AsyncSession, rows: list[Knockout]) -> None:
    _queue(session, "knock", [(r.id, knock_payload(r)) for r in rows])


def _queue(session: AsyncSession, kind: str, items: list[tuple[int, dict]]) -> None:
    if not enabled() or not items:
        return
    now = datetime.now(timezone.utc)
    session.add_all(
        AnalyticsOutbox(
            kind=kind,
            source_id=source_id,
            payload=payload,
            attempts=0,
            next_attempt_at=now,
        )
        for source_id, payload in items
    )


def fold_face_counts(rows: list[tuple[int, int]]) -> dict[int, dict[int, int]]:
    """seat -> {face: count}. Every saved roll counts, including one later rerolled."""
    out: dict[int, dict[int, int]] = {}
    for seat, value in rows:
        hist = out.setdefault(int(seat), {})
        face = int(value)
        hist[face] = hist.get(face, 0) + 1
    return out


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task, _client
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
    if _client is not None:
        await _client.aclose()
        _client = None


async def _loop() -> None:
    while True:
        try:
            if enabled():
                if not _schema_ready:
                    await ensure_schema()
                await _backfill("roll", DiceRoll, roll_payload)
                await _backfill("knock", Knockout, knock_payload)
                await _flush_batch()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("analytics flush failed", exc_info=True)
        await asyncio.sleep(1 if enabled() else 5)


async def ensure_schema() -> None:
    global _schema_ready
    if not enabled():
        return
    db = settings.CLICKHOUSE_DB
    for statement in _SCHEMA.format(db=db).split(";"):
        sql = statement.strip()
        if sql:
            await _exec(sql)
    _schema_ready = True


async def _backfill(kind: str, model, payload_of) -> None:
    async with SessionLocal() as session:
        cursor = await session.get(AnalyticsCursor, kind)
        created = cursor is None
        if cursor is None:
            cursor = AnalyticsCursor(stream=kind, last_id=0)
            session.add(cursor)
            await session.flush()
        rows = (
            await session.execute(
                select(model)
                .where(model.id > cursor.last_id)
                .order_by(model.id)
                .limit(_BATCH)
            )
        ).scalars().all()
        if not rows:
            if created:
                await session.commit()
            return
        await session.execute(
            pg_insert(AnalyticsOutbox)
            .values(
                [
                    {"kind": kind, "source_id": row.id, "payload": payload_of(row)}
                    for row in rows
                ]
            )
            .on_conflict_do_nothing(constraint="uq_analytics_outbox_source")
        )
        cursor.last_id = rows[-1].id
        await session.commit()


async def _flush_batch() -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        ids = (
            await session.execute(
                select(AnalyticsOutbox.id)
                .where(AnalyticsOutbox.next_attempt_at <= now)
                .order_by(AnalyticsOutbox.id)
                .limit(_BATCH)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        if not ids:
            return
        rows = (
            await session.execute(
                select(AnalyticsOutbox).where(AnalyticsOutbox.id.in_(ids))
            )
        ).scalars().all()
        claimed = [(r.id, r.kind, dict(r.payload), r.attempts) for r in rows]
        await session.execute(
            update(AnalyticsOutbox)
            .where(AnalyticsOutbox.id.in_(ids))
            .values(next_attempt_at=now + _LEASE)
        )
        await session.commit()

    rolls = [payload for _id, kind, payload, _n in claimed if kind == "roll"]
    knocks = [payload for _id, kind, payload, _n in claimed if kind == "knock"]
    try:
        await _insert("rolls", rolls)
        await _insert("knocks", knocks)
    except Exception:
        await _reschedule(claimed)
        raise
    async with SessionLocal() as session:
        await session.execute(delete(AnalyticsOutbox).where(AnalyticsOutbox.id.in_(ids)))
        await session.commit()


async def _reschedule(claimed: list[tuple]) -> None:
    now = datetime.now(timezone.utc)
    delay = backoff_seconds(max(attempts for _i, _k, _p, attempts in claimed) + 1)
    async with SessionLocal() as session:
        await session.execute(
            update(AnalyticsOutbox)
            .where(AnalyticsOutbox.id.in_([row_id for row_id, _k, _p, _n in claimed]))
            .values(
                attempts=AnalyticsOutbox.attempts + 1,
                next_attempt_at=now + timedelta(seconds=delay),
            )
        )
        await session.commit()


async def _insert(table: str, rows: list[dict]) -> None:
    if not rows:
        return
    body = "\n".join(json.dumps(row, default=str) for row in rows)
    await _exec(
        f"INSERT INTO {settings.CLICKHOUSE_DB}.{table} FORMAT JSONEachRow",
        body=body,
    )


async def _exec(query: str, body: str = "") -> None:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    res = await _client.post(
        f"http://{settings.CLICKHOUSE_HOST}:{settings.CLICKHOUSE_PORT}/",
        params={"query": query},
        content=body.encode(),
        headers={
            "X-ClickHouse-User": settings.CLICKHOUSE_USER,
            "X-ClickHouse-Key": settings.CLICKHOUSE_PASSWORD,
        },
    )
    if res.status_code >= 300:
        raise RuntimeError(f"clickhouse {res.status_code}: {res.text[:300]}")
