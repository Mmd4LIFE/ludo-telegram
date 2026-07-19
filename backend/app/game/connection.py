"""Per-match websocket connection registry.

A thin, lock-guarded map of match code -> live sockets, with a personalised-render fan-out
(each viewer can get a payload tailored to their seat). Ported from the poker app; Ludo has
no hidden information today, so ``render`` usually returns the same dict for everyone, but
the hook is kept for future per-seat views (e.g. a "your legal moves" overlay).
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger("ludo.ws")


class ConnectionHub:
    def __init__(self) -> None:
        self._rooms: dict[str, set[tuple[int, WebSocket]]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, code: str, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._rooms.setdefault(code, set()).add((user_id, ws))

    async def disconnect(self, code: str, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._rooms.get(code)
            if conns:
                conns.discard((user_id, ws))
                if not conns:
                    self._rooms.pop(code, None)

    def viewers(self, code: str) -> set[int]:
        return {uid for uid, _ in self._rooms.get(code, set())}

    def has_viewers(self, code: str) -> bool:
        return bool(self._rooms.get(code))

    async def send_personalised(self, code: str, render) -> None:
        conns = list(self._rooms.get(code, set()))
        dead: list[tuple[int, WebSocket]] = []
        for user_id, ws in conns:
            try:
                await ws.send_json(render(user_id))
            except Exception:  # noqa: BLE001
                dead.append((user_id, ws))
        if dead:
            async with self._lock:
                c = self._rooms.get(code)
                if c:
                    for item in dead:
                        c.discard(item)

    async def broadcast(self, code: str, payload: dict) -> None:
        await self.send_personalised(code, lambda _uid: payload)


hub = ConnectionHub()
