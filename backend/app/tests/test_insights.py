"""Insights: SQL guard (pure) + read-only runner / knowledge / agent against a real Postgres.

The DB tests run only when a migrated database is reachable with the configured
POSTGRES_* settings (e.g. ``POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432``).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.insights import sqlguard
from app.insights.sqlguard import SqlRejected


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select count(*) from users;",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "EXPLAIN SELECT * FROM matches",
    "SELECT 'drop table users; delete' AS s",
    "SELECT jsonb_set('{}'::jsonb, '{a}', '1') -- update users\n",
    "SELECT created_at, updated_at, deleted_at FROM chat_messages",
])
def test_guard_allows_reads(sql):
    assert sqlguard.check(sql)


@pytest.mark.parametrize("sql", [
    "",
    "DELETE FROM users",
    "UPDATE users SET coins = 1",
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE users",
    "WITH d AS (DELETE FROM users RETURNING id) SELECT * FROM d",
    "SELECT * INTO tmp FROM users",
    "COPY (SELECT 1) TO PROGRAM 'id'",
    "SELECT pg_sleep(10)",
    "SELECT set_config('statement_timeout', '0', false)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT nextval('users_id_seq')",
    "CREATE TABLE x (id int)",
])
def test_guard_rejects(sql):
    with pytest.raises(SqlRejected):
        sqlguard.check(sql)


def _db_available() -> bool:
    import psycopg2

    from app.config import settings
    try:
        psycopg2.connect(
            host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT, user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD, dbname=settings.POSTGRES_DB, connect_timeout=2,
        ).close()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_db = pytest.mark.skipif(not _db_available(), reason="no database")


class _FakeLLM:
    """Scripted chat responses: each item is a list of (tool_name, args) or a plain string."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def chat(self, messages, *, tools=None, tool_choice=None, temperature=0):
        step = self.script.pop(0)
        self.calls += 1
        if isinstance(step, str):
            return {"role": "assistant", "content": step}, {"model": "fake", "usage": {"total_tokens": 1}}
        calls = [
            {"id": f"c{self.calls}_{i}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for i, (name, args) in enumerate(step)
        ]
        return {"role": "assistant", "content": None, "tool_calls": calls}, {"model": "fake", "usage": {"total_tokens": 1}}


@needs_db
def test_insights_end_to_end(monkeypatch):
    import asyncpg

    from app.config import settings
    from app.database import SessionLocal, engine
    from app.insights import agent, catalog, knowledge, llm, readonly
    from app.insights.readonly import SqlError

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")

    async def scenario():
        await readonly.ensure_role()
        assert readonly.role_status()["ready"], readonly.role_status()

        async with SessionLocal() as s:
            await s.execute(__import__("sqlalchemy").text(
                "INSERT INTO users (telegram_id, first_name, username, wallet_code, coins, level, xp, games_played, "
                "games_won, is_bot, is_banned, bot_started) VALUES "
                "(900000001, 'Nila', 'nila', gen_random_uuid()::text, 0, 1, 0, 3, 2, false, false, true) "
                "ON CONFLICT (telegram_id) DO NOTHING"
            ))
            await s.commit()

        # every curated example runs on the read-only role
        for q, sql in catalog.EXAMPLES:
            res = await readonly.run(knowledge.fill(sql), max_rows=50)
            assert res.columns, q

        # private columns are unreadable, even through SELECT *
        for bad in ("SELECT telegram_id FROM users", "SELECT * FROM users", "SELECT referred_by FROM users",
                    "SELECT claim_token FROM transfers"):
            with pytest.raises(SqlError):
                await readonly.run(bad)
        ok = await readonly.run("SELECT id, first_name FROM users WHERE username = 'nila'")
        assert ok.rows and ok.rows[0][1] == "Nila"

        # the role itself can't write or escape, even bypassing the guard
        pool = await readonly._get_pool()
        async with pool.acquire() as conn:
            for stmt in (
                "INSERT INTO app_configs (key, value) VALUES ('X', '1')",
                "CREATE TABLE hax (id int)",
                "COPY (SELECT 1) TO PROGRAM 'id'",
                "SELECT pg_read_file('/etc/hostname')",
            ):
                with pytest.raises(asyncpg.PostgresError):
                    await conn.execute(stmt)
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute("SET ROLE ludo")

        # row cap via cursor, EXPLAIN / SHOW, timeout
        big = await readonly.run("SELECT g FROM generate_series(1, 100000) g", max_rows=10)
        assert big.row_count == 10 and big.truncated
        assert (await readonly.run("EXPLAIN SELECT * FROM matches")).rows
        assert (await readonly.run("SHOW statement_timeout")).rows
        with pytest.raises(SqlError, match="cancelled"):
            await readonly.run(
                "SELECT count(*) FROM generate_series(1, 1000000000) a", timeout_ms=300,
            )

        # timezone helper semantics: start of today in the admin tz is <= now
        monkeypatch.setattr(settings, "INSIGHTS_TZ_OFFSET_MINUTES", 210)
        r = await readonly.run(knowledge.fill(
            "SELECT date_trunc('day', now() AT TIME ZONE {TZ}) AT TIME ZONE {TZ} <= now() AS ok, "
            "(now() AT TIME ZONE {TZ}) - (now() AT TIME ZONE INTERVAL '+00:00') AS diff"
        ))
        assert r.rows[0][0] is True and r.rows[0][1] == "3:30:00"
        monkeypatch.setattr(settings, "INSIGHTS_TZ_OFFSET_MINUTES", 0)

        # knowledge sync + keyword retrieval (no embeddings without a key)
        async with SessionLocal() as s:
            stats = await knowledge.sync(s)
            assert stats["chunks"] > 30
            again = await knowledge.sync(s)
            assert again["changed"] == 0
            hits = await knowledge.retrieve(s, "stars revenue per day", k=5)
            assert any("star_payments" in h["content"] for h in hits)
            hits = await knowledge.retrieve(s, "how many knockouts did players make", k=5)
            assert any(h["key"] == "table:knockouts" or "knockouts" in h["content"] for h in hits)
            assert "telegram_id" not in knowledge.compact_schema()

        # agent loop with a scripted model: error → self-correct → answer
        fake = _FakeLLM([
            [("search_knowledge", {"query": "player count"})],
            [("run_sql", {"sql": "SELECT count(*) FROM userz", "purpose": "typo"})],
            [("run_sql", {"sql": "SELECT COUNT(*) AS players FROM users WHERE NOT is_bot", "purpose": "count"}),
             ("final_answer", {"answer": "too early", "sql": None, "follow_ups": []})],
            [("final_answer", {"answer": "You have **1** player.",
                               "sql": "SELECT COUNT(*) AS players FROM users WHERE NOT is_bot",
                               "chart": {"type": "bar", "x": "players", "y": "players"},
                               "follow_ups": ["Signups per day?"]})],
        ])
        monkeypatch.setattr(llm, "chat", fake.chat)
        monkeypatch.setattr(llm, "available", lambda: True)
        async with SessionLocal() as s:
            out = await agent.ask(s, admin_id=ok.rows[0][0], question="How many players?")
        assert out["status"] == "answered", out
        assert out["answer"] == "You have **1** player."
        assert out["table"]["rows"] == [[1]]
        assert out["chart"] is None  # x == y is rejected
        stages = [st["stage"] for st in out["steps"]]
        assert stages.count("run_sql") == 2 and "answer" in stages
        assert any(st["stage"] == "run_sql" and st["status"] == "error" for st in out["steps"])

        await readonly.close_pool()
        await engine.dispose()

    asyncio.run(scenario())
