"""Thin OpenAI JSON client for the Insights extractor. Server-side key only."""
from __future__ import annotations

import json

import httpx

from app.config import settings

_URL = "https://api.openai.com/v1/chat/completions"


class LLMError(RuntimeError):
    pass


async def complete_json(system: str, user: str) -> tuple[dict, dict]:
    """Return (parsed_json, meta). Forces a JSON object response. Raises LLMError."""
    key = settings.OPENAI_API_KEY
    if not key:
        raise LLMError("OpenAI API key isn't configured on the server")
    payload = {
        "model": settings.OPENAI_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(_URL, json=payload, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:  # network
        raise LLMError(f"could not reach OpenAI: {e}") from e
    if resp.status_code != 200:
        detail = resp.text[:200]
        try:
            detail = resp.json().get("error", {}).get("message", detail)
        except Exception:  # noqa: BLE001
            pass
        raise LLMError(f"OpenAI {resp.status_code}: {detail}")
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        raise LLMError(f"bad response from OpenAI: {e}") from e
    meta = {"model": data.get("model", settings.OPENAI_MODEL), "usage": data.get("usage", {})}
    return parsed, meta
