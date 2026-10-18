"""Thin OpenAI client for the Insights assistant (chat + tool calls, embeddings).

Server-side key only; nothing here is ever proxied raw to a client.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from app.config import settings

_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_EMBED_URL = "https://api.openai.com/v1/embeddings"
_RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


def chat_model() -> str:
    return settings.OPENAI_INSIGHTS_MODEL or settings.OPENAI_MODEL


def available() -> bool:
    return bool(settings.OPENAI_API_KEY)


async def _post(url: str, payload: dict, timeout: float) -> dict:
    key = settings.OPENAI_API_KEY
    if not key:
        raise LLMError("OPENAI_API_KEY isn't configured on the server")
    last = ""
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            last = f"could not reach OpenAI: {e}"
        else:
            if resp.status_code == 200:
                return resp.json()
            detail = resp.text[:300]
            try:
                detail = resp.json().get("error", {}).get("message", detail)
            except Exception:  # noqa: BLE001
                pass
            last = f"OpenAI {resp.status_code}: {detail}"
            if resp.status_code not in _RETRY_STATUS:
                break
        await asyncio.sleep(0.8 * (attempt + 1))
    raise LLMError(last)


async def chat(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0,
) -> tuple[dict, dict]:
    """Return (assistant_message, meta). meta = {"model", "usage"}."""
    payload: dict = {"model": chat_model(), "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["parallel_tool_calls"] = True
        if tool_choice:
            payload["tool_choice"] = tool_choice
    data = await _post(_CHAT_URL, payload, timeout=90)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"bad response from OpenAI: {e}") from e
    return msg, {"model": data.get("model", chat_model()), "usage": data.get("usage", {})}


async def complete_json(system: str, user: str) -> tuple[dict, dict]:
    """One-shot JSON-object completion. Returns (parsed, meta)."""
    payload = {
        "model": chat_model(),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    data = await _post(_CHAT_URL, payload, timeout=60)
    try:
        parsed = json.loads(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise LLMError(f"bad response from OpenAI: {e}") from e
    return parsed, {"model": data.get("model", chat_model()), "usage": data.get("usage", {})}


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts (batched). Raises LLMError."""
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        batch = [t[:8000] for t in texts[i:i + 64]]
        data = await _post(_EMBED_URL, {"model": settings.OPENAI_EMBED_MODEL, "input": batch}, timeout=60)
        try:
            items = sorted(data["data"], key=lambda d: d["index"])
            out.extend([round(x, 6) for x in d["embedding"]] for d in items)
        except (KeyError, TypeError) as e:
            raise LLMError(f"bad embeddings response: {e}") from e
    return out


def add_usage(total: dict, usage: dict) -> None:
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[k] = total.get(k, 0) + int(usage.get(k) or 0)
