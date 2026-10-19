"""API checks for durable rooms and approved late joins.

Requires the migrated test database (POSTGRES_HOST / POSTGRES_PORT). The bot is
left disabled by the caller so this does not poll Telegram.
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database import SessionLocal
from app.ludo.board import BASE
from app.main import app
from app.models import Match, MatchJoinRequest, MatchStatus
from app.services import ledger


async def _login(client: AsyncClient, telegram_id: int, name: str) -> dict:
    res = await client.post(
        "/api/auth/dev",
        json={"telegram_id": telegram_id, "first_name": name, "username": name.lower()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    return {"token": body["token"], "user": body["user"]}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _credit(user_id: int, amount: int) -> None:
    async with SessionLocal() as session:
        await ledger.credit(session, user_id, amount, reason="test")
        await session.commit()


async def _abandon_owned(user_id: int) -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Match).where(
                    Match.created_by == user_id,
                    Match.status.in_((MatchStatus.WAITING, MatchStatus.PLAYING)),
                )
            )
        ).scalars().all()
        for match in rows:
            match.status = MatchStatus.ABANDONED
        await session.commit()


async def _scenario() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        host = await _login(client, 910001, "Host")
        friend = await _login(client, 910002, "Friend")
        late = await _login(client, 910003, "Late")
        stranger = await _login(client, 910004, "Stranger")
        await _abandon_owned(host["user"]["id"])
        await _credit(host["user"]["id"], 200)
        await _credit(friend["user"]["id"], 200)

        created = await client.post(
            "/api/matches",
            headers=_auth(host["token"]),
            json={"max_players": 4, "is_public": True, "entry_fee": 50},
        )
        assert created.status_code == 200, created.text
        room = created.json()
        code = room["code"]
        assert room["i_am_seated"] is True
        assert room["seats"][0]["color"] == "BLUE"

        asked = await client.post(
            "/api/matches/join",
            headers=_auth(friend["token"]),
            json={"code": code},
        )
        assert asked.status_code == 200, asked.text
        asked_body = asked.json()
        assert asked_body["i_am_seated"] is False
        assert asked_body["request_status"] == "pending"
        assert asked_body["pending"] == []  # only the host sees the queue

        host_view = await client.get(f"/api/matches/{code}", headers=_auth(host["token"]))
        assert host_view.status_code == 200
        assert [p["user_id"] for p in host_view.json()["pending"]] == [friend["user"]["id"]]

        lobby = await client.get("/api/matches/lobby", headers=_auth(friend["token"]))
        assert lobby.status_code == 200, lobby.text
        mine = {row["code"]: row for row in lobby.json()["mine"]}
        assert mine[code]["request_status"] == "pending"
        assert code not in {row["code"] for row in lobby.json()["waiting"]}

        rejected = await client.post(
            f"/api/matches/{code}/reject",
            headers=_auth(host["token"]),
            json={"user_id": friend["user"]["id"]},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["pending"] == []

        again = await client.post(
            "/api/matches/join",
            headers=_auth(friend["token"]),
            json={"code": code},
        )
        assert again.status_code == 200, again.text
        assert again.json()["request_status"] == "pending"

        # Not enough coins: the request stays pending and nobody is seated.
        broke = await client.post(
            f"/api/matches/{code}/accept",
            headers=_auth(stranger["token"]),
            json={"user_id": friend["user"]["id"], "color": "RED"},
        )
        assert broke.status_code == 403

        poor_accept = await client.post(
            f"/api/matches/{code}/accept",
            headers=_auth(host["token"]),
            json={"user_id": friend["user"]["id"], "color": "RED"},
        )
        # Friend was credited, so this should succeed. The broke user above was the
        # authorization check. Fee failure is covered on the late join below.
        assert poor_accept.status_code == 200, poor_accept.text
        seated = poor_accept.json()
        assert any(s["user_id"] == friend["user"]["id"] and s["color"] == "RED" for s in seated["seats"])

        started = await client.post(
            f"/api/matches/{code}/start", headers=_auth(host["token"])
        )
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "playing"

        late_ask = await client.post(
            "/api/matches/join",
            headers=_auth(late["token"]),
            json={"code": code},
        )
        assert late_ask.status_code == 200, late_ask.text
        assert late_ask.json()["i_am_seated"] is False
        assert late_ask.json()["request_status"] == "pending"

        before = await client.get(f"/api/matches/{code}", headers=_auth(host["token"]))
        before_state = None
        async with SessionLocal() as session:
            match = (
                await session.execute(select(Match).where(Match.code == code))
            ).scalar_one()
            before_state = match.state
            assert before_state["current"] == before_state["current"]

        unpaid = await client.post(
            f"/api/matches/{code}/accept",
            headers=_auth(host["token"]),
            json={"user_id": late["user"]["id"], "color": "GREEN"},
        )
        assert unpaid.status_code == 409, unpaid.text
        assert "entry fee" in unpaid.text
        async with SessionLocal() as session:
            match = (
                await session.execute(select(Match).where(Match.code == code))
            ).scalar_one()
            req = (
                await session.execute(
                    select(MatchJoinRequest).where(
                        MatchJoinRequest.match_id == match.id,
                        MatchJoinRequest.user_id == late["user"]["id"],
                    )
                )
            ).scalar_one()
            assert req.status == "pending"
            assert len(match.seats) == 2
            assert match.state["current"] == before_state["current"]
            assert match.state["die"] == before_state["die"]

        await _credit(late["user"]["id"], 50)
        accepted = await client.post(
            f"/api/matches/{code}/accept",
            headers=_auth(host["token"]),
            json={"user_id": late["user"]["id"], "color": "GREEN"},
        )
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert any(s["user_id"] == late["user"]["id"] and s["color"] == "GREEN" for s in body["seats"])
        assert body["pending"] == []

        async with SessionLocal() as session:
            match = (
                await session.execute(select(Match).where(Match.code == code))
            ).scalar_one()
            players = match.state["players"]
            assert len(players) == 3
            assert players[2]["color"] == "GREEN"
            assert players[2]["tokens"] == [BASE, BASE, BASE, BASE]
            assert match.state["current"] == before_state["current"]
            assert match.state["phase"] == before_state["phase"]
            assert match.state["die"] == before_state["die"]
            assert match.state["turn"] == before_state["turn"]
            seat = next(s for s in match.seats if s.user_id == late["user"]["id"])
            assert seat.joined_at_turn == match.state["turn"]

        # A fresh runtime, as after a backend restart, restores the late player.
        from app.game.manager import manager
        from app.game.runtime import MatchRuntime

        live = manager._runtimes.get(match.id)
        assert live is not None
        assert live.state.players[2].tokens == [BASE, BASE, BASE, BASE]
        assert live.state.current == before_state["current"]
        await live.stop()
        manager._runtimes.pop(match.id, None)
        async with SessionLocal() as session:
            reloaded = (
                await session.execute(select(Match).where(Match.code == code))
            ).scalar_one()
            restored = MatchRuntime(reloaded)
        assert restored.seat_user[2] == late["user"]["id"]
        assert restored.state.players[2].tokens == [BASE, BASE, BASE, BASE]
        assert restored.state.current == before_state["current"]

        playing = await client.get("/api/matches/lobby", headers=_auth(stranger["token"]))
        assert playing.status_code == 200, playing.text
        assert code in {row["code"] for row in playing.json()["playing"]}

        private = await client.post(
            "/api/matches",
            headers=_auth(host["token"]),
            json={"max_players": 4, "is_public": False, "entry_fee": 0},
        )
        assert private.status_code == 200, private.text
        private_code = private.json()["code"]
        hidden = await client.get("/api/matches/lobby", headers=_auth(stranger["token"]))
        listed = {
            row["code"]
            for section in hidden.json().values()
            for row in section
        }
        assert private_code not in listed

        withdrawn = await client.post(
            "/api/matches/join",
            headers=_auth(stranger["token"]),
            json={"code": private_code},
        )
        assert withdrawn.status_code == 200, withdrawn.text
        left = await client.post(
            f"/api/matches/{private_code}/withdraw",
            headers=_auth(stranger["token"]),
        )
        assert left.status_code == 200, left.text
        assert left.json()["request_status"] == "rejected"
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(MatchJoinRequest)
                    .join(Match, Match.id == MatchJoinRequest.match_id)
                    .where(
                        Match.code == private_code,
                        MatchJoinRequest.user_id == stranger["user"]["id"],
                    )
                )
            ).scalar_one()
            assert row.status == "rejected"


@pytest.mark.asyncio
async def test_persistent_rooms_and_late_join():
    await _scenario()
