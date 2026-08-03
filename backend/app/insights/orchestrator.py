"""The Insights orchestrator — runs the pipeline and records every step.

    extract → resolve_time → resolve_entity → route → execute → compose

Each stage is timed and written to ``insights_steps`` (process monitoring); the whole turn
is written to ``insights_queries`` (audit log). Read-only throughout.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.insights import capabilities as caps
from app.insights import entities, temporal
from app.insights.extract import extract
from app.insights.llm import LLMError
from app.models import InsightsQuery, InsightsStep

logger = logging.getLogger("ludo.insights")


class _Flow:
    """Accumulates timed steps for one question."""

    def __init__(self) -> None:
        self.steps: list[dict] = []

    def add(self, stage: str, status: str, t0: float, detail: dict | None = None) -> None:
        self.steps.append({
            "seq": len(self.steps),
            "stage": stage,
            "status": status,
            "duration_ms": int((time.perf_counter() - t0) * 1000),
            "detail": detail or {},
        })


def _period_params(period: str) -> tuple[dict, str]:
    frm, to, label = temporal.resolve(period)
    return {"frm": frm, "to": to}, temporal.label_range(frm, to, label)


# metric → (capability, needs_entity)
_ROUTES = {
    "user_count": ("users.count", False),
    "user_wins": ("users.wins", True),
    "user_live_matches": ("users.live_matches", True),
    "matches_by_status": ("matches.count_by_status", False),
    "event_count": ("events.count", False),
    "coins": ("economy.coins", False),
    "leaderboard": ("leaderboard.top", False),
}


async def answer(session: AsyncSession, admin_id: int, question: str) -> dict:
    t_all = time.perf_counter()
    flow = _Flow()
    question = (question or "").strip()[:500]

    status = "error"
    answer_text = ""
    metric = capability = None
    intent = None
    log_params: dict | None = None
    result: dict | None = None
    error: str | None = None
    model = ""
    understood: dict = {}
    candidates: list[dict] = []

    try:
        # 1) EXTRACT — grounded intent
        t = time.perf_counter()
        try:
            intent, meta = await extract(question)
            model = meta.get("model", "")
            flow.add("extract", "ok", t, {"intent": intent, "usage": meta.get("usage", {})})
        except LLMError as e:
            error = str(e)
            flow.add("extract", "error", t, {"error": error})
            answer_text = "I couldn't understand the question (the AI extractor is unavailable)."
            raise _Handled()

        metric = intent["metric"]

        # explicit clarify from the extractor, or unknown metric → refuse/clarify
        if intent.get("clarify") or metric == "unknown" or metric not in _ROUTES:
            msg = intent.get("clarify") or "I can't answer that yet. Try asking about users, matches, wins, events, coins or leaderboards."
            status = "refused" if metric == "unknown" and not intent.get("clarify") else "clarified"
            answer_text = msg
            flow.add("route", status, time.perf_counter(), {"reason": "no capability", "metric": metric})
            raise _Handled()

        capability, needs_entity = _ROUTES[metric]
        exec_params: dict = {}
        period_label = "all time"

        # 2) RESOLVE TIME
        t = time.perf_counter()
        period = intent["time"]["period"]
        pp, period_label = _period_params(period)
        exec_params.update(pp)
        flow.add("resolve_time", "ok", t, {
            "period": period, "label": period_label,
            "from": pp["frm"].isoformat() if pp["frm"] else None,
            "to": pp["to"].isoformat() if pp["to"] else None,
        })

        entity_out = None
        # 3) RESOLVE ENTITY (only for player-scoped metrics)
        if needs_entity:
            t = time.perf_counter()
            name = (intent.get("entity") or {}).get("text")
            if not name:
                answer_text = "Which player do you mean?"
                status = "clarified"
                flow.add("resolve_entity", "clarify", t, {"reason": "no name given"})
                raise _Handled()
            found = await entities.resolve_users(session, name)
            if not found:
                answer_text = f'I couldn\'t find a player called "{name}".'
                status = "refused"
                flow.add("resolve_entity", "error", t, {"name": name, "candidates": 0})
                raise _Handled()
            if len(found) > 1:
                candidates = found
                answer_text = f'Which "{name}"? I found {len(found)} players.'
                status = "clarified"
                flow.add("resolve_entity", "clarify", t, {"name": name, "candidates": found})
                raise _Handled()
            chosen = found[0]
            exec_params["user_id"] = chosen["id"]
            entity_out = {"id": chosen["id"], "name": chosen["name"]}
            flow.add("resolve_entity", "ok", t, {"name": name, "resolved": entity_out})

        # extra params
        if metric == "event_count":
            exec_params["kind"] = intent.get("event_kind") or "dice_rolls"
        if metric == "leaderboard":
            exec_params["metric"] = intent.get("leaderboard_metric") or "wins"
            exec_params["n"] = intent.get("limit") or 5

        # 4) ROUTE (record the resolved plan)
        t = time.perf_counter()
        log_params = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in exec_params.items()}
        flow.add("route", "ok", t, {"capability": capability, "params": log_params})

        # 5) EXECUTE
        t = time.perf_counter()
        try:
            result = await caps.REGISTRY[capability].handler(session, exec_params)
            flow.add("execute", "ok", t, {"result": result})
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
            flow.add("execute", "error", t, {"error": error})
            answer_text = "The query failed while running."
            raise _Handled()

        # 6) COMPOSE
        t = time.perf_counter()
        answer_text = _compose(metric, result, entity_out, period_label, exec_params)
        status = "answered"
        understood = {
            "metric": metric, "capability": capability, "params": log_params,
            "entity": entity_out, "period_label": period_label,
        }
        flow.add("compose", "ok", t, {"answer": answer_text})

    except _Handled:
        pass
    except Exception as e:  # noqa: BLE001 — never leak a stack to the client
        logger.exception("insights pipeline crashed")
        error = f"{type(e).__name__}: {e}"
        answer_text = answer_text or "Something went wrong answering that."
        status = "error"

    latency_ms = int((time.perf_counter() - t_all) * 1000)

    # persist audit + flow
    q = InsightsQuery(
        admin_id=admin_id, question=question, status=status, answer=answer_text[:2000],
        metric=metric, capability=capability, intent=intent, params=log_params,
        result=result, error=(error or None), model=model, latency_ms=latency_ms,
    )
    session.add(q)
    await session.flush()
    for s in flow.steps:
        session.add(InsightsStep(
            query_id=q.id, seq=s["seq"], stage=s["stage"], status=s["status"],
            duration_ms=s["duration_ms"], detail=s["detail"],
        ))
    await session.commit()

    return {
        "query_id": q.id, "status": status, "answer": answer_text,
        "understood": understood, "result": result, "candidates": candidates,
        "steps": flow.steps, "model": model, "latency_ms": latency_ms,
    }


class _Handled(Exception):
    """Control-flow: a stage fully resolved the turn (clarify/refuse/error)."""


def _compose(metric: str, r: dict, entity, period_label: str, p: dict) -> str:
    who = f"{entity['name']} (#{entity['id']})" if entity else None
    if metric == "user_count":
        base = f"You have {r['count']:,} players"
        return base + (" (all-time, excluding bots)." if period_label == "all time" else f" created in {period_label}.")
    if metric == "user_wins":
        n = r["wins"]
        return f"{who} won {n} game{'s' if n != 1 else ''} in {period_label}."
    if metric == "user_live_matches":
        n = r["live_matches"]
        return f"{who} is in {n} game{'s' if n != 1 else ''} right now."
    if metric == "matches_by_status":
        return (f"In {period_label}: {r.get('total', 0)} matches — "
                f"{r.get('finished', 0)} finished, {r.get('playing', 0)} playing, "
                f"{r.get('waiting', 0)} waiting, {r.get('abandoned', 0)} abandoned.")
    if metric == "event_count":
        kind = (p.get("kind") or "events").replace("_", " ")
        return f"{r['count']:,} {kind} in {period_label}."
    if metric == "coins":
        return f"{r['coins']:,} coins are in circulation."
    if metric == "leaderboard":
        top = r.get("top", [])
        if not top:
            return "No players yet."
        lines = ", ".join(f"{i+1}. {t['name']} ({t['value']})" for i, t in enumerate(top))
        return f"Top by {r.get('metric', 'wins')}: {lines}."
    return "Done."
