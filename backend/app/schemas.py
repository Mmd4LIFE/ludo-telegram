"""Pydantic request/response schemas for the REST API.

Rule: no schema ever carries ``telegram_id``. Player-facing identity is display name +
optional friends-only username + a stable public ``id``.
"""
from __future__ import annotations

from pydantic import BaseModel


# --- auth -------------------------------------------------------------------
class AuthRequest(BaseModel):
    init_data: str


class DevAuthRequest(BaseModel):
    telegram_id: int
    first_name: str = "Dev"
    username: str | None = None


class UserProfile(BaseModel):
    id: int
    first_name: str
    username: str | None = None
    coins: int
    level: int
    xp: int
    games_played: int
    games_won: int
    # bot @username, so the Mini App can build t.me/<bot>?start=rm-<code> invite links.
    bot_username: str = ""

    @classmethod
    def from_user(cls, u) -> "UserProfile":
        from app.bot.instance import get_bot_username

        return cls(
            id=u.id,
            first_name=u.first_name,
            username=u.username,
            coins=u.coins,
            level=u.level,
            xp=u.xp,
            games_played=u.games_played,
            games_won=u.games_won,
            bot_username=get_bot_username(),
        )


class TokenResponse(BaseModel):
    token: str
    user: UserProfile


# --- matches ----------------------------------------------------------------
class CreateMatchRequest(BaseModel):
    max_players: int = 4          # 2..4
    is_public: bool = True
    entry_fee: int = 0
    fill_with_bots: bool = False  # start immediately against house bots


class SeatInfo(BaseModel):
    seat_index: int
    color: str
    name: str            # display name (friends-only detail stays server-side)
    is_bot: bool
    user_id: int | None = None


class PendingJoiner(BaseModel):
    user_id: int
    name: str


class MatchSummary(BaseModel):
    code: str
    status: str
    max_players: int
    seated: int
    is_public: bool
    entry_fee: int
    created_by: int | None = None   # host user id — only the host may start/delete
    seats: list[SeatInfo] = []
    pending: list[PendingJoiner] = []   # awaiting the host's approval


class AcceptJoinerRequest(BaseModel):
    user_id: int
    color: str        # RED | GREEN | YELLOW | BLUE


class RejectJoinerRequest(BaseModel):
    user_id: int


class JoinMatchRequest(BaseModel):
    code: str


# --- room lobby chat ---------------------------------------------------------
class ChatMessage(BaseModel):
    id: int
    user_id: int
    name: str
    text: str


class SendChatRequest(BaseModel):
    text: str
