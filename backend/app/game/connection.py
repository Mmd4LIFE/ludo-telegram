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

from app.config import settings

logger = logging.getLogger("ludo.ws")


class ConnectionHub:
    def __init__(self) -> None:
        self._rooms: dict[str, set[tuple[int, WebSocket]]] = {}
        self._lock = asyncio.Lock()

    def room_full(self, code: str) -> bool:
        return len(self._rooms.get(code, ())) >= settings.MAX_VIEWERS_PER_MATCH

    async def connect(self, code: str, user_id: int, ws: WebSocket) -> bool:
        """Accept and register. Returns False if the room is at the viewer cap."""
        async with self._lock:
            conns = self._rooms.setdefault(code, set())
            if len(conns) >= settings.MAX_VIEWERS_PER_MATCH:
                return False
            await ws.accept()
            conns.add((user_id, ws))
            return True

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
        """Fan out state to a room without stalling the match driver on a slow client.

        Today Ludo has no hidden per-seat fields, so we build the payload once and
        ``gather`` the sends with a short timeout. Dead sockets are pruned afterward.
        """
        conns = list(self._rooms.get(code, set()))
        if not conns:
            return
        # One render for the room (personalization hook kept for future use).
        try:
            payload = render(conns[0][0])
        except Exception:  # noqa: BLE001
            logger.exception("render failed for match %s", code)
            return

        timeout = max(0.2, float(settings.WS_SEND_TIMEOUT_SECONDS))

        async def _send(user_id: int, ws: WebSocket) -> tuple[int, WebSocket] | None:
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=timeout)
                return None
            except Exception:  # noqa: BLE001
                return (user_id, ws)

        results = await asyncio.gather(
            *(_send(uid, ws) for uid, ws in conns),
            return_exceptions=False,
        )
        dead = [item for item in results if item is not None]
        if dead:
            async with self._lock:
                c = self._rooms.get(code)
                if c:
                    for item in dead:
                        c.discard(item)

    async def broadcast(self, code: str, payload: dict) -> None:
        await self.send_personalised(code, lambda _uid: payload)


hub = ConnectionHub()
