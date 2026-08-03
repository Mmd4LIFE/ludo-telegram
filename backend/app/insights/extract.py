"""The text extractor — question → grounded ``AnalyticsIntent`` (LangExtract-style).

We use a schema-constrained, few-shot LLM call to pull structured fields out of the
question, then GROUND the entity/time phrases back to their character spans by locating
them in the original text (so the UI can show exactly what was read). The extractor never
touches the DB and never authors SQL — it only classifies + fills a typed intent.
"""
from __future__ import annotations

from app.insights.llm import complete_json
from app.insights.temporal import PERIODS

# the closed vocabulary the extractor may choose from — keeps it grounded to real capabilities
METRICS = (
    "user_count",         # total / new players
    "user_wins",          # a named player's wins
    "user_live_matches",  # a named player's games in progress
    "matches_by_status",  # matches started/finished/… in a period
    "event_count",        # dice_rolls / knockouts / card_draws volume
    "coins",              # coins in circulation
    "leaderboard",        # top players
    "unknown",            # not answerable → refuse
)
EVENT_KINDS = ("dice_rolls", "knockouts", "card_draws")

_SYSTEM = f"""You convert an admin's analytics question about a Ludo game app into a strict JSON intent.
Only classify — never invent data. Choose fields ONLY from the allowed vocabularies.

metrics: {", ".join(METRICS)}
periods: {", ".join(PERIODS)}
event_kinds: {", ".join(EVENT_KINDS)}
leaderboard_metrics: wins, knockouts

Return a JSON object with EXACTLY these keys:
- metric: one of metrics
- entity_text: the exact player name mentioned, or null
- time_phrase: the exact time expression mentioned (e.g. "today", "last week"), or null
- period: the best-matching period token (default "all" if none implied)
- event_kind: one of event_kinds when metric=event_count, else null
- leaderboard_metric: "wins" or "knockouts" when metric=leaderboard, else null
- limit: integer for leaderboard/top-N, or null
- confidence: 0..1
- clarify: a short question if the request is ambiguous or unanswerable, else null

If the question is not answerable from these metrics, set metric="unknown" and give a clarify."""

_FEWSHOT = [
    ("how many users do we have?",
     '{"metric":"user_count","entity_text":null,"time_phrase":null,"period":"all","event_kind":null,"leaderboard_metric":null,"limit":null,"confidence":0.98,"clarify":null}'),
    ("how many matches were played today and finished?",
     '{"metric":"matches_by_status","entity_text":null,"time_phrase":"today","period":"today","event_kind":null,"leaderboard_metric":null,"limit":null,"confidence":0.95,"clarify":null}'),
    ("how many wins does Nila have last week?",
     '{"metric":"user_wins","entity_text":"Nila","time_phrase":"last week","period":"7d","event_kind":null,"leaderboard_metric":null,"limit":null,"confidence":0.94,"clarify":null}'),
    ("how many cards were drawn yesterday?",
     '{"metric":"event_count","entity_text":null,"time_phrase":"yesterday","period":"yesterday","event_kind":"card_draws","leaderboard_metric":null,"limit":null,"confidence":0.93,"clarify":null}'),
    ("top 5 players by knockouts",
     '{"metric":"leaderboard","entity_text":null,"time_phrase":null,"period":"all","event_kind":null,"leaderboard_metric":"knockouts","limit":5,"confidence":0.9,"clarify":null}'),
]


def _span(text: str, phrase: str | None) -> list[int] | None:
    if not phrase:
        return None
    i = text.lower().find(phrase.lower())
    return [i, i + len(phrase)] if i >= 0 else None


async def extract(question: str) -> tuple[dict, dict]:
    """Return (intent, meta). ``intent`` is normalized + grounded with character spans."""
    examples = "\n".join(f"Q: {q}\nA: {a}" for q, a in _FEWSHOT)
    user = f"{examples}\n\nQ: {question}\nA:"
    raw, meta = await complete_json(_SYSTEM, user)

    metric = raw.get("metric") if raw.get("metric") in METRICS else "unknown"
    period = raw.get("period") if raw.get("period") in PERIODS else "all"
    event_kind = raw.get("event_kind") if raw.get("event_kind") in EVENT_KINDS else None
    lb_metric = raw.get("leaderboard_metric") if raw.get("leaderboard_metric") in ("wins", "knockouts") else None
    entity_text = (raw.get("entity_text") or None)
    time_phrase = (raw.get("time_phrase") or None)
    try:
        limit = int(raw["limit"]) if raw.get("limit") is not None else None
    except (TypeError, ValueError):
        limit = None
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    intent = {
        "metric": metric,
        "entity": {"text": entity_text, "span": _span(question, entity_text)} if entity_text else None,
        "time": {"phrase": time_phrase, "span": _span(question, time_phrase), "period": period},
        "event_kind": event_kind,
        "leaderboard_metric": lb_metric,
        "limit": limit,
        "confidence": confidence,
        "clarify": raw.get("clarify") or None,
    }
    return intent, meta
