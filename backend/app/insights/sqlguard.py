"""Static checks for admin/LLM-supplied SQL.

The real wall is the read-only Postgres role (see ``readonly``); this layer only gives
fast, friendly errors and blocks a few side-effecting functions a read-only role can
still call.
"""
from __future__ import annotations

import re


class SqlRejected(ValueError):
    pass


ALLOWED_FIRST = {"select", "with", "explain", "show", "table", "values"}

_FORBIDDEN_WORDS = {
    "insert", "update", "delete", "merge", "alter", "drop", "create", "truncate",
    "grant", "revoke", "copy", "call", "do", "vacuum", "set", "reset", "lock",
    "listen", "unlisten", "notify", "begin", "commit", "rollback", "savepoint",
    "prepare", "execute", "deallocate", "refresh", "cluster", "reindex", "comment",
    "security", "import", "discard", "checkpoint", "load", "into",
}

_FORBIDDEN_FUNCS = re.compile(
    r"\b(pg_sleep\w*|set_config|pg_advisory\w*|pg_try_advisory\w*|dblink\w*|lo_\w+|"
    r"pg_read_\w+|pg_ls_\w+|pg_stat_file|pg_terminate_backend|pg_cancel_backend|"
    r"pg_reload_conf|pg_rotate_logfile|pg_notify|nextval|setval|pg_file_\w+|"
    r"query_to_xml\w*|table_to_xml\w*|cursor_to_xml\w*|database_to_xml\w*|schema_to_xml\w*)\s*\(",
    re.IGNORECASE,
)

_STRIP = re.compile(
    r"""
      --[^\n]*                       # line comment
    | /\*.*?\*/                      # block comment
    | \$(?P<tag>[A-Za-z_]*)\$.*?\$(?P=tag)\$   # dollar-quoted string
    | [eE]?'(?:[^'\\]|\\.|'')*'      # string literal
    | "(?:[^"]|"")*"                 # quoted identifier
    """,
    re.VERBOSE | re.DOTALL,
)

MAX_SQL_LEN = 20_000


def _skeleton(sql: str) -> str:
    """SQL with comments, strings and quoted identifiers blanked out."""
    return _STRIP.sub(" ", sql)


def check(sql: str) -> str:
    """Return the statement ready to run (trailing ``;`` removed) or raise SqlRejected."""
    sql = (sql or "").strip()
    if not sql:
        raise SqlRejected("Empty query")
    if len(sql) > MAX_SQL_LEN:
        raise SqlRejected(f"Query is too long (max {MAX_SQL_LEN:,} chars)")

    skel = _skeleton(sql).strip()
    while skel.endswith(";"):
        skel = skel[:-1].rstrip()
    if ";" in skel:
        raise SqlRejected("Run one statement at a time")
    if not skel:
        raise SqlRejected("Empty query")

    first = re.match(r"[A-Za-z]+", skel)
    if not first or first.group(0).lower() not in ALLOWED_FIRST:
        raise SqlRejected("Only read queries are allowed (SELECT / WITH / EXPLAIN / SHOW / VALUES)")

    words = {w.lower() for w in re.findall(r"\b[A-Za-z_]+\b", skel)}
    bad = sorted(words & _FORBIDDEN_WORDS)
    if bad:
        raise SqlRejected(f"Not allowed in a read-only query: {', '.join(w.upper() for w in bad)}")

    m = _FORBIDDEN_FUNCS.search(skel)
    if m:
        raise SqlRejected(f"Function not allowed: {m.group(1)}")

    out = sql.rstrip()
    while out.endswith(";"):
        out = out[:-1].rstrip()
    return out


def normalise(sql: str) -> str:
    """Whitespace/case-insensitive key for comparing two statements."""
    return re.sub(r"\s+", " ", (sql or "").strip().rstrip(";")).lower()
