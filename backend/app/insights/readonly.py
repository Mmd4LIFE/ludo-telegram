"""Read-only SQL execution for Insights and the admin native-query tab.

Queries run on a separate asyncpg pool logged in as a dedicated, non-superuser role
(``INSIGHTS_RO_ROLE``) that only holds SELECT, with private columns excluded at the
column-privilege level. On top of that every query runs inside a READ ONLY transaction
with a statement timeout, and rows are pulled through a cursor so a huge result never
lands in memory.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from uuid import UUID

import asyncpg
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.insights import sqlguard
from app.insights.sqlguard import SqlRejected

logger = logging.getLogger("ludo.insights.sql")

# Never readable through Insights / native SQL / the data browser.
# users.referred_by stores the referrer's Telegram id, so it is as private as telegram_id.
REDACTED_COLUMNS: dict[str, set[str]] = {
    "users": {"telegram_id", "referred_by"},
    "transfers": {"claim_token"},
    "star_payments": {"invoice_payload"},
}

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SECRET = re.compile(r"^[A-Za-z0-9_\-]{12,128}$")
MAX_CELL_CHARS = 2000


class SqlError(RuntimeError):
    pass


@dataclass
class SqlResult:
    columns: list[dict] = field(default_factory=list)   # [{"name", "type"}]
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0

    def as_dict(self, max_rows: int | None = None) -> dict:
        rows = self.rows if max_rows is None else self.rows[:max_rows]
        return {
            "columns": self.columns,
            "rows": rows,
            "row_count": self.row_count,
            "truncated": self.truncated or len(rows) < len(self.rows),
            "duration_ms": self.duration_ms,
        }


def _role() -> str:
    role = settings.INSIGHTS_RO_ROLE.strip().lower()
    if not _IDENT.match(role):
        raise SqlError("INSIGHTS_RO_ROLE must be a plain lowercase identifier")
    return role


def _password() -> str:
    if settings.INSIGHTS_RO_PASSWORD:
        pw = settings.INSIGHTS_RO_PASSWORD
        if not _SECRET.match(pw):
            raise SqlError("INSIGHTS_RO_PASSWORD must be 12-128 chars of [A-Za-z0-9_-]")
        return pw
    return hmac.new(settings.SECRET_KEY.encode(), b"ludo-insights-ro", hashlib.sha256).hexdigest()[:32]


_role_error: str | None = None
_role_ready = False
_role_lock = asyncio.Lock()


async def ensure_role() -> None:
    """Create/sync the read-only role and its grants. Idempotent; safe on every boot."""
    global _role_ready, _role_error
    async with _role_lock:
        role, pw = _role(), _password()
        db = settings.POSTGRES_DB.replace('"', "")
        try:
            async with engine.begin() as conn:
                exists = (
                    await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
                ).scalar()
                if not exists:
                    await conn.execute(text(f'CREATE ROLE "{role}" LOGIN'))
                await conn.execute(text(
                    f'ALTER ROLE "{role}" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                    f"NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 6 PASSWORD '{pw}'"
                ))
                await conn.execute(text(f'ALTER ROLE "{role}" SET default_transaction_read_only = on'))
                await conn.execute(text(f"ALTER ROLE \"{role}\" SET statement_timeout = '60s'"))
                await conn.execute(text(f"ALTER ROLE \"{role}\" SET idle_in_transaction_session_timeout = '60s'"))
                await conn.execute(text(f'GRANT CONNECT ON DATABASE "{db}" TO "{role}"'))
                await conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
                await conn.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"'))
                await conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
                for table, hidden in REDACTED_COLUMNS.items():
                    cols = (
                        await conn.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
                            ),
                            {"t": table},
                        )
                    ).scalars().all()
                    if not cols:
                        continue
                    await conn.execute(text(f'REVOKE SELECT ON "{table}" FROM "{role}"'))
                    visible = [c for c in cols if c not in hidden]
                    if visible:
                        col_list = ", ".join(f'"{c}"' for c in visible)
                        await conn.execute(text(f'GRANT SELECT ({col_list}) ON "{table}" TO "{role}"'))
            _role_ready, _role_error = True, None
            logger.info("read-only role %s synced", role)
        except Exception as e:  # noqa: BLE001
            _role_ready = False
            _role_error = f"{type(e).__name__}: {e}"[:300]
            logger.warning("could not set up read-only role %s: %s", role, _role_error)


def role_status() -> dict:
    return {"role": settings.INSIGHTS_RO_ROLE, "ready": _role_ready, "error": _role_error}


_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def _get_pool() -> asyncpg.Pool:
    global _pool
    async with _pool_lock:
        if _pool is not None:
            return _pool
        if not _role_ready:
            await ensure_role()
        if not _role_ready:
            raise SqlError(f"Read-only database role isn't available: {_role_error}")
        _pool = await asyncpg.create_pool(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=_role(),
            password=_password(),
            database=settings.POSTGRES_DB,
            min_size=0,
            max_size=3,
            max_inactive_connection_lifetime=120,
            init=_init_conn,
            server_settings={"application_name": "ludo-insights", "default_transaction_read_only": "on"},
        )
        return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _cell(v):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return v if len(v) <= MAX_CELL_CHARS else v[:MAX_CELL_CHARS] + "…"
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (datetime, date, dtime)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return str(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(v)[:64].hex()
    if isinstance(v, (list, tuple)):
        return [_cell(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _cell(x) for k, x in v.items()}
    if isinstance(v, (ipaddress.IPv4Address, ipaddress.IPv6Address, ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return str(v)
    return str(v)


def _friendly(e: asyncpg.PostgresError, timeout_ms: int) -> str:
    msg = getattr(e, "message", None) or str(e)
    if isinstance(e, asyncpg.exceptions.QueryCanceledError):
        return f"Query took longer than {timeout_ms / 1000:g}s and was cancelled. Add filters or aggregate more."
    if isinstance(e, asyncpg.exceptions.ReadOnlySQLTransactionError):
        return "This runner is read-only — writes are not allowed."
    if isinstance(e, asyncpg.exceptions.InsufficientPrivilegeError):
        hidden = "; ".join(f"{t}.{c}" for t, cs in REDACTED_COLUMNS.items() for c in sorted(cs))
        return f"{msg}. Private columns ({hidden}) can't be read — list the columns you need instead of SELECT *."
    parts = [msg]
    detail, hint, pos = getattr(e, "detail", None), getattr(e, "hint", None), getattr(e, "position", None)
    if detail:
        parts.append(f"Detail: {detail}")
    if hint:
        parts.append(f"Hint: {hint}")
    if pos:
        parts.append(f"(at character {pos})")
    return " ".join(parts)


async def run(sql: str, *, max_rows: int = 200, timeout_ms: int | None = None) -> SqlResult:
    """Validate and run one read-only statement. Raises SqlRejected / SqlError."""
    stmt_sql = sqlguard.check(sql)
    max_rows = max(1, min(int(max_rows), settings.INSIGHTS_MAX_ROWS))
    timeout_ms = int(timeout_ms or settings.INSIGHTS_SQL_TIMEOUT_MS)
    pool = await _get_pool()
    t0 = time.perf_counter()

    async def _go() -> SqlResult:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                stmt = await conn.prepare(stmt_sql)
                attrs = stmt.get_attributes()
                if not attrs:
                    await stmt.fetch()
                    return SqlResult()
                cur = await stmt.cursor()
                recs = await cur.fetch(max_rows + 1)
                cols = [{"name": a.name, "type": a.type.name} for a in attrs]
                truncated = len(recs) > max_rows
                rows = [[_cell(v) for v in r.values()] for r in recs[:max_rows]]
                return SqlResult(columns=cols, rows=rows, row_count=len(rows), truncated=truncated)

    try:
        res = await asyncio.wait_for(_go(), timeout=timeout_ms / 1000 + 5)
    except asyncpg.PostgresError as e:
        raise SqlError(_friendly(e, timeout_ms)) from e
    except asyncio.TimeoutError as e:
        raise SqlError(f"Query took longer than {timeout_ms / 1000:g}s and was cancelled.") from e
    except (OSError, asyncpg.InterfaceError) as e:
        raise SqlError(f"Database connection failed: {e}") from e
    res.duration_ms = int((time.perf_counter() - t0) * 1000)
    return res


__all__ = ["REDACTED_COLUMNS", "SqlError", "SqlRejected", "SqlResult", "ensure_role", "role_status", "run", "close_pool"]
