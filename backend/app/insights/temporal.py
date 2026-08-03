"""Deterministic temporal resolution: a period token → a concrete ``[from, to)`` window.

Anchored to a fixed timezone (``INSIGHTS_TZ_OFFSET_MINUTES``) so "today" is unambiguous.
Never uses an LLM — the extractor only picks the token; this turns it into real datetimes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import settings

PERIODS = ("all", "today", "yesterday", "7d", "30d", "this_month", "this_week")


def _tz() -> timezone:
    return timezone(timedelta(minutes=settings.INSIGHTS_TZ_OFFSET_MINUTES))


def now() -> datetime:
    return datetime.now(_tz())


def resolve(period: str) -> tuple[datetime | None, datetime | None, str]:
    """Return (from, to, label). ``from``/``to`` are None for the all-time window."""
    tz = _tz()
    n = datetime.now(tz)
    start_today = n.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == "today":
        return start_today, start_today + timedelta(days=1), "today"
    if period == "yesterday":
        return start_today - timedelta(days=1), start_today, "yesterday"
    if period == "7d":
        return n - timedelta(days=7), n, "the last 7 days"
    if period == "30d":
        return n - timedelta(days=30), n, "the last 30 days"
    if period == "this_week":
        # ISO week: Monday 00:00 → now
        monday = start_today - timedelta(days=start_today.weekday())
        return monday, n, "this week"
    if period == "this_month":
        first = start_today.replace(day=1)
        return first, n, "this month"
    return None, None, "all time"


def label_range(frm: datetime | None, to: datetime | None, label: str) -> str:
    if frm is None:
        return "all time"
    return f"{label} ({frm.date().isoformat()} → {(to or now()).date().isoformat()})"
