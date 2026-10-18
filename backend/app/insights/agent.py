"""The Insights agent: an OpenAI tool-calling loop over the read-only database.

    retrieve knowledge → [search_knowledge | describe_table | run_sql]* → final_answer

The model always sees the full compact schema, admin notes and the chunks retrieved for
the question; it can pull more knowledge, inspect tables and run as many read-only queries
as it needs (bounded), self-correcting on SQL errors. Every step is timed and written to
``insights_steps``; the turn is written to ``insights_queries``.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.insights import knowledge, llm, readonly, sqlguard
from app.insights.readonly import SqlError, SqlRejected, SqlResult
from app.models import InsightsQuery, InsightsStep

logger = logging.getLogger("ludo.insights")

MAX_ROUNDS = 8
MODEL_ROWS = 40          # rows of each result shown to the model
DISPLAY_ROWS = 500       # rows kept for the admin's result table
AGENT_TIMEOUT_MS = 12000
MAX_CELL_FOR_MODEL = 200

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search the database knowledge base: table docs, business definitions "
                           "(activity, wins, economy reasons, revenue…), verified SQL examples and admin notes.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What you need to know"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "Columns, types, keys, column notes, known categorical values and the 3 newest rows of a table.",
            "parameters": {
                "type": "object",
                "properties": {"table": {"type": "string"}},
                "required": ["table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": f"Run ONE read-only PostgreSQL 16 statement (SELECT / WITH). Returns column names "
                           f"and up to {MODEL_ROWS} rows; row_count is rows fetched, truncated=true means more exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "purpose": {"type": "string", "description": "One short line: what this query answers"},
                },
                "required": ["sql", "purpose"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "final_answer",
            "description": "Finish the turn. Call it alone, after you have read the query results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "Markdown answer for the admin. Lead with the result; mention the time "
                                       "window and assumptions. Same language as the question.",
                    },
                    "sql": {
                        "type": ["string", "null"],
                        "description": "The executed query whose result best supports the answer "
                                       "(shown to the admin as a table). null if none.",
                    },
                    "chart": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["none", "bar", "line"]},
                            "x": {"type": "string", "description": "column for labels / dates"},
                            "y": {"type": "string", "description": "numeric column"},
                        },
                        "required": ["type"],
                    },
                    "follow_ups": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Up to 3 short follow-up questions the admin may want next",
                    },
                },
                "required": ["answer", "sql", "follow_ups"],
            },
        },
    },
]

_SYSTEM = """You are the senior data analyst for "Ludo Board", a Ludo game played as a Telegram Mini App \
(players, rooms/matches, fantasy cards, a coin economy, Telegram Stars purchases, wallet transfers).
You answer the admin's questions from the PostgreSQL 16 database using the tools. The connection is read-only.

Current time: {now_utc} UTC — admin local time {now_local} (UTC{tz}). In SQL use {tz_sql} for the admin timezone.

How to work:
1. Read the schema and the retrieved knowledge. If a definition is unclear call search_knowledge; call describe_table to see column notes, values and sample rows.
2. Write precise SQL and call run_sql. Sanity-check the result; on an error or a suspicious result, fix the SQL and run again. Use several queries for multi-part questions.
3. When you have the numbers, call final_answer on its own.

Rules:
- Every number in the answer must come from a query result. Never guess or extrapolate.
- If the question is vague, pick the most reasonable interpretation, answer it, and state the assumption in one short line. Only ask a clarifying question (final_answer with sql null) when no reasonable interpretation exists.
- Players by name: match first_name / username with ILIKE. If several players match, show them all with ids.
- Exclude house bots (users.is_bot, match_seats.is_bot or NULL user_id, matches.is_bot_table) unless asked.
- Private columns are not readable: never SELECT * from users; select explicit columns.
- "today", "this week", "last month"… are calendar periods in the admin timezone (see the conventions doc).
- Make the final table readable: aggregate, snake_case aliases, sensible ORDER BY, LIMIT 50 for lists.
- Reply in the language of the question. Be concise: the direct result first, then at most 3 short bullets of context. Use thousands separators.
- chart: "line" for time series (x = date column), "bar" for categories / rankings with at most 30 rows, otherwise "none".

Database schema — table (~rows): column type, pk, →foreign key:
{schema}
{notes}"""


class _Trace:
    def __init__(self) -> None:
        self.steps: list[dict] = []

    def add(self, stage: str, status: str, t0: float, detail: dict | None = None) -> None:
        self.steps.append({
            "seq": len(self.steps), "stage": stage, "status": status,
            "duration_ms": int((time.perf_counter() - t0) * 1000), "detail": detail or {},
        })


def _short(v):
    if isinstance(v, str) and len(v) > MAX_CELL_FOR_MODEL:
        return v[:MAX_CELL_FOR_MODEL] + "…"
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, default=str)
        return s if len(s) <= MAX_CELL_FOR_MODEL else s[:MAX_CELL_FOR_MODEL] + "…"
    return v


def _for_model(res: SqlResult) -> str:
    return json.dumps({
        "columns": [c["name"] for c in res.columns],
        "types": [c["type"] for c in res.columns],
        "rows": [[_short(v) for v in r] for r in res.rows[:MODEL_ROWS]],
        "row_count": res.row_count,
        "truncated": res.truncated,
        "rows_shown": min(MODEL_ROWS, len(res.rows)),
    }, ensure_ascii=False, default=str)


def _format_chunks(chunks: list[dict]) -> str:
    return "\n\n".join(f"### {c['title']}\n{c['content']}" for c in chunks)


def _valid_chart(chart, res: SqlResult | None) -> dict | None:
    if not isinstance(chart, dict) or res is None or not res.rows:
        return None
    kind = chart.get("type")
    if kind not in ("bar", "line"):
        return None
    names = [c["name"] for c in res.columns]
    x, y = chart.get("x"), chart.get("y")
    if x not in names or y not in names or x == y:
        return None
    yi = names.index(y)
    if not all(isinstance(r[yi], (int, float)) or r[yi] is None for r in res.rows):
        return None
    if kind == "bar" and len(res.rows) > 30:
        return None
    return {"type": kind, "x": x, "y": y}


class Agent:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.trace = _Trace()
        self.results: dict[str, SqlResult] = {}
        self.last_ok_sql: str | None = None
        self.usage: dict = {}
        self.model = ""
        self.sources: list[dict] = []

    # --- tools -------------------------------------------------------------
    async def _search_knowledge(self, args: dict) -> str:
        t = time.perf_counter()
        q = str(args.get("query") or "")[:300]
        hits = await knowledge.retrieve(self.session, q, k=6)
        self.trace.add("search_knowledge", "ok", t, {
            "query": q, "hits": [{"key": h["key"], "title": h["title"], "score": h["score"]} for h in hits],
        })
        return _format_chunks(hits) or "No matching knowledge."

    async def _describe_table(self, args: dict) -> str:
        t = time.perf_counter()
        name = str(args.get("table") or "").strip().strip('"').lower()
        info = knowledge.table_info(name)
        if info is None:
            self.trace.add("describe_table", "error", t, {"table": name, "error": "unknown table"})
            known = ", ".join(tb.name for tb in knowledge.tables())
            return f"Unknown table '{name}'. Tables: {known}"
        chunk = await knowledge.get_chunk(self.session, f"table:{name}")
        text_out = chunk or json.dumps(info, ensure_ascii=False)
        cols = [c["name"] for c in info["columns"] if c["name"] != "state"]
        pk = next((c["name"] for c in info["columns"] if c["pk"]), cols[0])
        sample_sql = f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{name}" ORDER BY "{pk}" DESC LIMIT 3'
        try:
            res = await readonly.run(sample_sql, max_rows=3, timeout_ms=5000)
            text_out += "\nNewest rows: " + _for_model(res)
            self.trace.add("describe_table", "ok", t, {"table": name})
        except (SqlError, SqlRejected) as e:
            self.trace.add("describe_table", "ok", t, {"table": name, "sample_error": str(e)})
        return text_out

    async def _run_sql(self, args: dict) -> str:
        t = time.perf_counter()
        sql = str(args.get("sql") or "")
        purpose = str(args.get("purpose") or "")[:200]
        try:
            res = await readonly.run(sql, max_rows=DISPLAY_ROWS, timeout_ms=AGENT_TIMEOUT_MS)
        except (SqlError, SqlRejected) as e:
            self.trace.add("run_sql", "error", t, {"sql": sql, "purpose": purpose, "error": str(e)})
            return json.dumps({"error": str(e)})
        self.results[sqlguard.normalise(sql)] = res
        self.last_ok_sql = sql
        self.trace.add("run_sql", "ok", t, {
            "sql": sql, "purpose": purpose, "row_count": res.row_count, "truncated": res.truncated,
            "columns": [c["name"] for c in res.columns], "preview": [[_short(v) for v in r] for r in res.rows[:5]],
            "db_ms": res.duration_ms,
        })
        return _for_model(res)

    async def _dispatch(self, name: str, args: dict) -> str:
        if name == "search_knowledge":
            return await self._search_knowledge(args)
        if name == "describe_table":
            return await self._describe_table(args)
        if name == "run_sql":
            return await self._run_sql(args)
        return f"Unknown tool {name}"

    # --- main loop ---------------------------------------------------------
    async def _messages(self, question: str, history: list[dict]) -> list[dict]:
        t = time.perf_counter()
        counts = await knowledge.row_counts(self.session)
        notes = await knowledge.admin_notes(self.session)
        notes_txt = ("\nAdmin notes (authoritative definitions):\n" + "\n".join(
            f"- {n['title']}: {n['content']}" if n["title"] else f"- {n['content']}" for n in notes
        )) if notes else ""
        now = datetime.now(timezone.utc)
        local = now + timedelta(minutes=settings.INSIGHTS_TZ_OFFSET_MINUTES)
        system = _SYSTEM.format(
            now_utc=now.strftime("%Y-%m-%d %H:%M (%A)"),
            now_local=local.strftime("%Y-%m-%d %H:%M (%A)"),
            tz=knowledge.tz_offset(),
            tz_sql=knowledge.tz_interval(),
            schema=knowledge.compact_schema(counts),
            notes=notes_txt,
        )
        # retrieval query includes the previous question so follow-ups keep their context
        rq = question if not history else f"{history[-1].get('question', '')}\n{question}"
        hits = await knowledge.retrieve(self.session, rq, k=9)
        self.trace.add("retrieve", "ok", t, {
            "hits": [{"key": h["key"], "title": h["title"], "score": h["score"]} for h in hits],
            "notes": len(notes),
        })
        self.sources = hits

        msgs: list[dict] = [{"role": "system", "content": system}]
        for h in history[-4:]:
            q, a, s = h.get("question"), h.get("answer"), h.get("sql")
            if not q or not a:
                continue
            msgs.append({"role": "user", "content": str(q)[:500]})
            msgs.append({"role": "assistant", "content": str(a)[:1500] + (f"\n\nSQL used:\n{str(s)[:2000]}" if s else "")})
        if hits:
            msgs.append({"role": "system", "content": "Retrieved knowledge for the next question:\n\n" + _format_chunks(hits)})
        msgs.append({"role": "user", "content": question})
        return msgs

    async def run(self, question: str, history: list[dict]) -> dict:
        messages = await self._messages(question, history)
        final: dict | None = None
        for rnd in range(MAX_ROUNDS):
            last = rnd == MAX_ROUNDS - 1
            choice = {"type": "function", "function": {"name": "final_answer"}} if last else "auto"
            t = time.perf_counter()
            msg, meta = await llm.chat(messages, tools=TOOLS, tool_choice=choice)
            self.model = meta.get("model", self.model)
            llm.add_usage(self.usage, meta.get("usage", {}))
            calls = msg.get("tool_calls") or []
            self.trace.add("llm", "ok", t, {
                "round": rnd, "tools": [c["function"]["name"] for c in calls],
                "usage": meta.get("usage", {}), **({"text": msg["content"][:500]} if msg.get("content") else {}),
            })
            if not calls:
                final = {"answer": msg.get("content") or "", "sql": self.last_ok_sql, "follow_ups": []}
                break
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            names = [c["function"]["name"] for c in calls]
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                if name == "final_answer":
                    if len(names) == 1 and isinstance(args, dict):
                        final = args
                        break
                    out = ("Ignored: call final_answer alone, after reading the tool results."
                           if isinstance(args, dict) else "Invalid JSON arguments.")
                elif args is None:
                    out = "Invalid JSON arguments."
                else:
                    out = await self._dispatch(name, args)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
            if final is not None:
                break
        if final is None:
            final = {"answer": "I couldn't finish this analysis — try rephrasing or narrowing the question.",
                     "sql": self.last_ok_sql, "follow_ups": []}
        return await self._assemble(final)

    async def _assemble(self, final: dict) -> dict:
        t = time.perf_counter()
        sql = final.get("sql") or None
        if isinstance(sql, str) and not sql.strip():
            sql = None
        res = self.results.get(sqlguard.normalise(sql)) if sql else None
        if sql and res is None:
            try:
                res = await readonly.run(sql, max_rows=DISPLAY_ROWS, timeout_ms=AGENT_TIMEOUT_MS)
            except (SqlError, SqlRejected) as e:
                self.trace.add("run_sql", "error", t, {"sql": sql, "purpose": "final table", "error": str(e)})
                sql, res = self.last_ok_sql, self.results.get(sqlguard.normalise(self.last_ok_sql or ""))
        follow = [str(f)[:160] for f in (final.get("follow_ups") or []) if str(f).strip()][:3]
        out = {
            "answer": str(final.get("answer") or "").strip() or "No answer.",
            "sql": sql,
            "table": res.as_dict() if res else None,
            "chart": _valid_chart(final.get("chart"), res),
            "follow_ups": follow,
        }
        self.trace.add("answer", "ok", t, {"sql": sql, "chart": out["chart"], "follow_ups": follow})
        return out


async def ask(session: AsyncSession, admin_id: int, question: str, history: list[dict] | None = None) -> dict:
    t_all = time.perf_counter()
    question = (question or "").strip()[:500]
    agent = Agent(session)
    status, error = "error", None
    out: dict = {"answer": "", "sql": None, "table": None, "chart": None, "follow_ups": []}
    try:
        if not llm.available():
            raise llm.LLMError("OPENAI_API_KEY isn't configured on the server")
        out = await agent.run(question, history or [])
        status = "answered"
    except llm.LLMError as e:
        error = str(e)
        out["answer"] = f"The AI service is unavailable: {error}. You can still use the SQL tab."
        agent.trace.add("llm", "error", time.perf_counter(), {"error": error})
    except Exception as e:  # noqa: BLE001 — never leak a stack trace to the client
        logger.exception("insights agent crashed")
        error = f"{type(e).__name__}: {e}"
        out["answer"] = "Something went wrong answering that."

    latency_ms = int((time.perf_counter() - t_all) * 1000)
    sources = [{"key": s["key"], "title": s["title"], "kind": s["kind"], "score": s["score"]}
               for s in agent.sources]
    table = out.get("table")
    q = InsightsQuery(
        admin_id=admin_id, question=question, status=status, answer=out["answer"][:20000],
        sql=out.get("sql"), error=(error[:500] if error else None), model=agent.model[:48],
        latency_ms=latency_ms,
        result={
            "columns": table["columns"] if table else [],
            "rows": table["rows"][:100] if table else [],
            "row_count": table["row_count"] if table else 0,
            "truncated": bool(table and (table["truncated"] or len(table["rows"]) > 100)),
            "chart": out.get("chart"), "follow_ups": out.get("follow_ups", []),
            "usage": agent.usage, "sources": sources,
        },
    )
    session.add(q)
    await session.flush()
    for s in agent.trace.steps:
        session.add(InsightsStep(
            query_id=q.id, seq=s["seq"], stage=s["stage"][:32], status=s["status"],
            duration_ms=s["duration_ms"], detail=json.loads(json.dumps(s["detail"], default=str)),
        ))
    await session.commit()

    return {
        "query_id": q.id, "status": status, "question": question, **out,
        "sources": sources, "steps": agent.trace.steps, "model": agent.model,
        "latency_ms": latency_ms, "usage": agent.usage, "feedback": None,
    }
