"""Database knowledge for the Insights assistant: schema introspection + RAG store.

Chunks live in ``insights_knowledge``:

* ``table:<name>``   (auto)     columns, types, FKs, curated column notes, live categorical values
* ``doc:<topic>``    (curated)  business definitions / conventions from ``catalog.TOPICS``
* ``example:<hash>`` (curated)  verified question → SQL pairs from ``catalog.EXAMPLES``
* ``learned:<id>``   (learned)  answers an admin marked as good (question → SQL)
* ``note:<id>``      (admin)    free-text definitions an admin taught the assistant

Each chunk gets an OpenAI embedding (stored as JSONB — no pgvector needed at this size).
Retrieval is hybrid: cosine similarity on embeddings blended with a BM25 keyword score,
falling back to keywords alone when embeddings are unavailable.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from collections import Counter

import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import Base
from app.insights import catalog, llm
from app.insights.readonly import REDACTED_COLUMNS
from app.models import InsightsKnowledge

logger = logging.getLogger("ludo.insights.knowledge")

_INTERNAL_PREFIX = "insights_"
_PG = postgresql.dialect()


# --- timezone helpers ---------------------------------------------------------

def tz_offset() -> str:
    m = int(settings.INSIGHTS_TZ_OFFSET_MINUTES)
    sign = "-" if m < 0 else "+"
    m = abs(m)
    return f"{sign}{m // 60:02d}:{m % 60:02d}"


def tz_interval() -> str:
    return f"INTERVAL '{tz_offset()}'"


def fill(s: str) -> str:
    return s.replace("{TZ}", tz_interval())


# --- schema introspection ------------------------------------------------------

def tables() -> list[sa.Table]:
    ts = list(Base.metadata.sorted_tables)
    return sorted(ts, key=lambda t: (t.name.startswith(_INTERNAL_PREFIX), t.name))


def visible_columns(table: sa.Table) -> list[sa.Column]:
    hidden = REDACTED_COLUMNS.get(table.name, set())
    return [c for c in table.columns if c.name not in hidden]


def col_type(c: sa.Column) -> str:
    if isinstance(c.type, sa.Enum):
        return f"enum {c.type.name}(" + ", ".join(f"'{v}'" for v in c.type.enums) + ")"
    try:
        s = c.type.compile(dialect=_PG)
    except Exception:  # noqa: BLE001
        s = str(c.type)
    s = s.lower()
    return (
        s.replace("timestamp with time zone", "timestamptz")
        .replace("timestamp without time zone", "timestamp")
        .replace("character varying", "varchar")
    )


def _fk(c: sa.Column) -> str | None:
    for fk in c.foreign_keys:
        return f"{fk.column.table.name}.{fk.column.name}"
    return None


def table_info(name: str) -> dict | None:
    t = Base.metadata.tables.get(name)
    if t is None:
        return None
    notes = catalog.TABLE_DOCS.get(name, {})
    cols = [
        {
            "name": c.name,
            "type": col_type(c),
            "nullable": bool(c.nullable),
            "pk": bool(c.primary_key),
            "fk": _fk(c),
            "note": notes.get(c.name, ""),
        }
        for c in visible_columns(t)
    ]
    referenced_by = sorted(
        f"{o.name}.{c.name}"
        for o in Base.metadata.tables.values()
        for c in o.columns
        for fk in c.foreign_keys
        if fk.column.table.name == name
    )
    return {
        "name": name,
        "doc": notes.get("_", ""),
        "internal": name.startswith(_INTERNAL_PREFIX),
        "columns": cols,
        "hidden_columns": sorted(REDACTED_COLUMNS.get(name, set())),
        "referenced_by": referenced_by,
    }


_estimates: dict = {"at": 0.0, "rows": {}}


async def row_counts(session: AsyncSession, max_age: float = 600) -> dict[str, int]:
    """Row count per table: exact for small tables, planner estimate for big ones."""
    if _estimates["rows"] and time.monotonic() - _estimates["at"] < max_age:
        return _estimates["rows"]
    res = await session.execute(text(
        "SELECT c.relname, c.reltuples::bigint FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    ))
    est = {r[0]: int(r[1]) for r in res}
    known = set(Base.metadata.tables)
    out: dict[str, int] = {}
    for name, n in est.items():
        if name not in known:
            continue
        if n < 200_000:
            n = int((await session.execute(text(f'SELECT count(*) FROM "{name}"'))).scalar() or 0)
        out[name] = n
    _estimates.update(at=time.monotonic(), rows=out)
    return out


def compact_schema(counts: dict[str, int] | None = None) -> str:
    """Every table and column in one dense block — always sent to the model."""
    counts = counts or {}
    lines = []
    for t in tables():
        parts = []
        for c in visible_columns(t):
            s = f"{c.name} {col_type(c)}"
            if c.primary_key:
                s += " pk"
            fk = _fk(c)
            if fk:
                s += f" →{fk}"
            parts.append(s)
        n = counts.get(t.name)
        size = f" ~{n:,} rows" if n is not None else ""
        tag = " [internal]" if t.name.startswith(_INTERNAL_PREFIX) else ""
        lines.append(f"{t.name}{tag}{size}: " + ", ".join(parts))
    return "\n".join(lines)


async def categorical_values(session: AsyncSession) -> dict[tuple[str, str], list[tuple[str, int]]]:
    out: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for table, col in catalog.CATEGORICAL:
        t = Base.metadata.tables.get(table)
        if t is None or col not in t.columns:
            continue
        try:
            res = await session.execute(text(
                f'SELECT "{col}"::text AS v, count(*) AS n FROM "{table}" '
                f'WHERE "{col}" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 40'
            ))
            out[(table, col)] = [(r[0], int(r[1])) for r in res]
        except Exception:  # noqa: BLE001
            await session.rollback()
    return out


# --- chunk building -------------------------------------------------------------

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _table_chunk(name: str, values: dict) -> dict:
    info = table_info(name)
    assert info is not None
    lines = [f"Table {name}" + (" (internal)" if info["internal"] else "") + (f" — {info['doc']}" if info["doc"] else "")]
    lines.append("Columns:")
    for c in info["columns"]:
        flags = []
        if c["pk"]:
            flags.append("PK")
        if c["fk"]:
            flags.append(f"FK → {c['fk']}")
        if c["nullable"] and not c["pk"]:
            flags.append("nullable")
        line = f"- {c['name']} {c['type']}" + (f" [{', '.join(flags)}]" if flags else "")
        if c["note"]:
            line += f": {c['note']}"
        lines.append(line)
    if info["hidden_columns"]:
        lines.append("Private (not selectable): " + ", ".join(info["hidden_columns"]))
    for (t, col), vals in values.items():
        if t == name and vals:
            lines.append(f"Values of {col}: " + ", ".join(v for v, _ in vals))
    if info["referenced_by"]:
        lines.append("Referenced by: " + ", ".join(info["referenced_by"]))
    return {"key": f"table:{name}", "kind": "table", "source": "auto", "title": f"Table {name}", "content": "\n".join(lines)}


def build_chunks(values: dict) -> list[dict]:
    chunks = [_table_chunk(t.name, values) for t in tables()]
    for key, title, body in catalog.TOPICS:
        chunks.append({"key": key, "kind": "doc", "source": "curated", "title": title, "content": fill(body)})
    for q, sql in catalog.EXAMPLES:
        chunks.append({
            "key": "example:" + _hash(q)[:16], "kind": "example", "source": "curated",
            "title": q, "content": f"Q: {q}\nSQL:\n{fill(sql)}",
        })
    return chunks


# --- sync + embeddings --------------------------------------------------------------

async def sync(session: AsyncSession) -> dict:
    """Rebuild auto/curated chunks, drop stale ones, embed anything new or changed."""
    chunks = build_chunks(await categorical_values(session))
    rows = (
        await session.execute(select(InsightsKnowledge).where(InsightsKnowledge.source.in_(("auto", "curated"))))
    ).scalars().all()
    existing = {r.key: r for r in rows}
    seen: set[str] = set()
    changed = 0
    for ch in chunks:
        seen.add(ch["key"])
        h = _hash(ch["title"] + "\n" + ch["content"])
        row = existing.get(ch["key"])
        if row is None:
            session.add(InsightsKnowledge(content_hash=h, **ch))
            changed += 1
        elif row.content_hash != h:
            row.title, row.content, row.kind, row.content_hash = ch["title"], ch["content"], ch["kind"], h
            row.embedding, row.embed_model = None, ""
            changed += 1
    removed = 0
    for key, row in existing.items():
        if key not in seen:
            await session.delete(row)
            removed += 1
    await session.commit()
    embedded = await embed_pending(session)
    invalidate()
    return {"chunks": len(chunks), "changed": changed, "removed": removed, "embedded": embedded}


async def embed_pending(session: AsyncSession) -> int:
    if not llm.available():
        return 0
    model = settings.OPENAI_EMBED_MODEL
    rows = (
        await session.execute(
            select(InsightsKnowledge).where(
                InsightsKnowledge.enabled.is_(True),
                sa.or_(InsightsKnowledge.embedding.is_(None), InsightsKnowledge.embed_model != model),
            )
        )
    ).scalars().all()
    if not rows:
        return 0
    try:
        vecs = await llm.embed([f"{r.title}\n{r.content}" for r in rows])
    except llm.LLMError as e:
        logger.warning("embedding failed, retrieval falls back to keywords: %s", e)
        return 0
    for r, v in zip(rows, vecs):
        r.embedding, r.embed_model = v, model
    await session.commit()
    invalidate()
    return len(rows)


# --- retrieval ------------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9_]+|[\u0600-\u06ff]+")
_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "by", "and", "or", "is", "are", "was", "were",
    "how", "many", "much", "what", "which", "who", "do", "does", "did", "we", "have", "has", "with",
    "me", "show", "list", "give", "from", "at", "as", "be", "it", "this", "that", "select", "where",
    "q", "sql", "count", "group", "order", "limit", "not",
}


def tokenize(s: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(s.lower()):
        parts = [tok] + ([p for p in tok.split("_") if p] if "_" in tok else [])
        for p in parts:
            if p not in _STOP and len(p) > 1:
                out.append(p[:-1] if len(p) > 4 and p.endswith("s") and not p.endswith("ss") else p)
    return out


_version = 0
_cache: dict = {"version": -1, "items": [], "idf": {}, "avglen": 1.0}
_qcache: dict[str, list[float]] = {}


def invalidate() -> None:
    global _version
    _version += 1


async def _items(session: AsyncSession) -> list[dict]:
    if _cache["version"] == _version:
        return _cache["items"]
    version = _version
    rows = (
        await session.execute(select(InsightsKnowledge).where(InsightsKnowledge.enabled.is_(True)))
    ).scalars().all()
    items = []
    df: Counter = Counter()
    for r in rows:
        toks = Counter(tokenize(f"{r.title}\n{r.content}"))
        df.update(toks.keys())
        vec = r.embedding if r.embed_model == settings.OPENAI_EMBED_MODEL else None
        items.append({
            "id": r.id, "key": r.key, "kind": r.kind, "source": r.source,
            "title": r.title, "content": r.content, "tf": toks, "len": sum(toks.values()) or 1,
            "vec": vec, "norm": math.sqrt(sum(x * x for x in vec)) if vec else 0.0,
        })
    n = max(len(items), 1)
    _cache.update(
        version=version,
        items=items,
        idf={t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()},
        avglen=sum(i["len"] for i in items) / n,
    )
    return items


async def get_chunk(session: AsyncSession, key: str) -> str | None:
    return next((i["content"] for i in await _items(session) if i["key"] == key), None)


def _bm25(item: dict, q: list[str], idf: dict, avglen: float, k1: float = 1.4, b: float = 0.6) -> float:
    s = 0.0
    for t in set(q):
        tf = item["tf"].get(t)
        if tf:
            s += idf.get(t, 0.0) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * item["len"] / avglen))
    return s


async def _query_vec(query: str) -> list[float] | None:
    if not llm.available():
        return None
    if query in _qcache:
        return _qcache[query]
    try:
        vec = (await llm.embed([query]))[0]
    except llm.LLMError as e:
        logger.info("query embedding failed: %s", e)
        return None
    if len(_qcache) > 256:
        _qcache.clear()
    _qcache[query] = vec
    return vec


async def retrieve(
    session: AsyncSession,
    query: str,
    *,
    k: int = 8,
    kinds: set[str] | None = None,
    per_kind: dict[str, int] | None = None,
) -> list[dict]:
    items = await _items(session)
    if kinds:
        items = [i for i in items if i["kind"] in kinds]
    if not items:
        return []
    q = tokenize(query)
    kw = [_bm25(i, q, _cache["idf"], _cache["avglen"]) for i in items]
    kw_max = max(kw) or 1.0
    qvec = await _query_vec(query) if any(i["vec"] for i in items) else None
    qnorm = math.sqrt(sum(x * x for x in qvec)) if qvec else 0.0

    scored = []
    for it, kwi in zip(items, kw):
        kwn = kwi / kw_max
        if qvec and it["vec"] and qnorm and it["norm"]:
            cos = sum(a * b for a, b in zip(qvec, it["vec"])) / (qnorm * it["norm"])
            score = 0.7 * cos + 0.3 * kwn
        else:
            score = kwn * (0.5 if qvec else 1.0)
        scored.append((score, it))
    scored.sort(key=lambda x: x[0], reverse=True)

    caps = {"example": 4, "table": 5, "doc": 3, "note": 4}
    caps.update(per_kind or {})
    taken: Counter = Counter()
    out = []
    for score, it in scored:
        if score <= 0:
            break
        kind = it["kind"]
        if taken[kind] >= caps.get(kind, k):
            continue
        taken[kind] += 1
        out.append({
            "id": it["id"], "key": it["key"], "kind": it["kind"], "source": it["source"],
            "title": it["title"], "content": it["content"], "score": round(score, 4),
        })
        if len(out) >= k:
            break
    return out


async def admin_notes(session: AsyncSession, max_chars: int = 4000) -> list[dict]:
    """Admin-taught definitions — small and authoritative, so always sent in full."""
    rows = (
        await session.execute(
            select(InsightsKnowledge)
            .where(InsightsKnowledge.kind == "note", InsightsKnowledge.enabled.is_(True))
            .order_by(InsightsKnowledge.id)
        )
    ).scalars().all()
    out, used = [], 0
    for r in rows:
        if used + len(r.content) > max_chars:
            break
        used += len(r.content)
        out.append({"key": r.key, "title": r.title, "content": r.content})
    return out


# --- admin-curated entries ---------------------------------------------------------------

async def add_entry(
    session: AsyncSession, *, kind: str, key: str, title: str, content: str, source: str, admin_id: int | None
) -> InsightsKnowledge:
    row = (await session.execute(select(InsightsKnowledge).where(InsightsKnowledge.key == key))).scalar_one_or_none()
    h = _hash(title + "\n" + content)
    if row is None:
        row = InsightsKnowledge(key=key, kind=kind, source=source, created_by=admin_id)
        session.add(row)
    row.title, row.content, row.content_hash, row.enabled = title, content, h, True
    row.embedding, row.embed_model = None, ""
    await session.commit()
    await embed_pending(session)
    invalidate()
    return row
