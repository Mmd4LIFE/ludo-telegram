"""WebSocket endpoint for a live Ludo match.

Client connects to ``/ws/match/{code}?token=<jwt>``. On connect we register the socket,
ensure the match runtime is running, and push the current state. The client then sends:

    {"type": "roll"}                      -> roll the die (only honoured on your turn)
    {"type": "move", "token_index": 0}    -> move one of your tokens
    {"type": "ping"}                      -> keep-alive, replied with {"type":"pong"}
    {"type": "sync"}                      -> request a fresh state snapshot
    {"type": "emote", "emote": "fire"}    -> broadcast a reaction

All game rules are enforced server-side by the runtime + pure engine; the client is a
renderer. A stale or out-of-turn action is simply ignored.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.security import AuthError, decode_access_token
from app.database import SessionLocal
from app.game.connection import hub
from app.game.manager import manager
from app.models import Match, User

router = APIRouter()
logger = logging.getLogger("ludo.ws")

ALLOWED_EMOTES = {"thumbs_up", "laugh", "fire", "party", "cry", "angry", "clap", "lucky"}


@router.websocket("/ws/match/{code}")
async def match_ws(websocket: WebSocket, code: str, token: str = Query(...)):
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (AuthError, KeyError, ValueError):
        await websocket.close(code=4401)
        return

    async with SessionLocal() as session:
        match = (
            await session.execute(select(Match).where(Match.code == code.upper()))
        ).scalar_one_or_none()
        if match is None:
            await websocket.close(code=4404)
            return
        user = await session.get(User, user_id)
        if user is None:
            await websocket.close(code=4401)
            return
        # A game needs 2+ seats; opening a socket on a one-seat room used to blow up in
        # initial_state() and leave the client hanging on "Joining game…". Refuse cleanly.
        if sum(1 for s in match.seats if s.user_id is not None or s.is_bot) < 2:
            await websocket.close(code=4409)
            return
        rt = await manager.get_runtime(session, match)
        # refresh joiner names (picks up players who joined after the runtime started)
        seated_ids = [uid for uid in rt.seat_user.values() if uid]
        if seated_ids:
            rows = (
                await session.execute(
                    select(User.id, User.first_name).where(User.id.in_(seated_ids))
                )
            ).all()
            id_name = {rid: (fn or "Player") for rid, fn in rows}
            for seat, uid in rt.seat_user.items():
                if uid and uid in id_name:
                    rt.seat_names[seat] = id_name[uid]
        match_id = match.id
        match_code = match.code

    rt.start()
    await hub.connect(match_code, user_id, websocket)
    try:
        await websocket.send_json(rt.render(user_id))
        while True:
            data = await websocket.receive_json()
            mtype = data.get("type")
            if mtype in ("roll", "move"):
                manager.handle_action(match_id, user_id, data)
            elif mtype == "ping":
                await websocket.send_json({"type": "pong"})
            elif mtype == "sync":
                await websocket.send_json(rt.render(user_id))
            elif mtype == "emote":
                emote = str(data.get("emote", ""))
                if emote in ALLOWED_EMOTES:
                    await hub.broadcast(
                        match_code,
                        {"type": "emote", "user_id": user_id, "emote": emote},
                    )
            elif mtype == "rematch":
                rt.rematch.add(user_id)
                human_ids = {uid for uid in rt.seat_user.values() if uid}
                if human_ids and rt.rematch.issuperset(human_ids):
                    # everyone who played wants another game — spin one up and send all in
                    async with SessionLocal() as s2:
                        new_match = await manager.create_rematch(s2, match_id)
                    await hub.broadcast(
                        match_code, {"type": "rematch_ready", "code": new_match.code}
                    )
                else:
                    await hub.broadcast(
                        match_code,
                        {
                            "type": "rematch",
                            "votes": sorted(rt.rematch),
                            "human_ids": sorted(human_ids),
                        },
                    )
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("ws error in match %s", match_code)
    finally:
        await hub.disconnect(match_code, user_id, websocket)
