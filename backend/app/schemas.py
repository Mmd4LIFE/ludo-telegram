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

    @classmethod
    def from_user(cls, u) -> "UserProfile":
        return cls(
            id=u.id,
            first_name=u.first_name,
            username=u.username,
            coins=u.coins,
            level=u.level,
            xp=u.xp,
            games_played=u.games_played,
            games_won=u.games_won,
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


class MatchSummary(BaseModel):
    code: str
    status: str
    max_players: int
    seated: int
    is_public: bool
    entry_fee: int


class JoinMatchRequest(BaseModel):
    code: str
