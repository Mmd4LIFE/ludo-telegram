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
    is_admin: bool = False
    dice_skin: str = "classic"

    @classmethod
    def from_user(cls, u) -> "UserProfile":
        from app.bot.instance import get_bot_username
        from app.config import settings

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
            is_admin=u.telegram_id in settings.admin_ids,
            dice_skin=getattr(u, "dice_skin", "classic") or "classic",
        )


class SetDiceSkinRequest(BaseModel):
    skin: str


class TokenResponse(BaseModel):
    token: str
    user: UserProfile


# --- admin ------------------------------------------------------------------
class AdminUser(BaseModel):
    """A player as an admin sees them. Still no telegram_id — that never leaves."""

    id: int
    first_name: str
    username: str | None = None
    coins: int
    level: int
    xp: int
    games_played: int
    games_won: int
    bot_started: bool
    is_banned: bool
    last_seen_at: str | None = None
    created_at: str | None = None

    @classmethod
    def from_user(cls, u) -> "AdminUser":
        return cls(
            id=u.id,
            first_name=u.first_name,
            username=u.username,
            coins=u.coins,
            level=u.level,
            xp=u.xp,
            games_played=u.games_played,
            games_won=u.games_won,
            bot_started=u.bot_started,
            is_banned=u.is_banned,
            last_seen_at=u.last_seen_at.isoformat() if u.last_seen_at else None,
            created_at=u.created_at.isoformat() if getattr(u, "created_at", None) else None,
        )


class AdminStats(BaseModel):
    users: int
    users_started: int
    games_played: int
    coins_in_circulation: int
    matches_playing: int
    matches_waiting: int
    matches_finished: int
    matches_abandoned: int


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


class SetColorRequest(BaseModel):
    user_id: int     # seated player to recolour (the host may recolour themselves)
    color: str       # RED | GREEN | YELLOW | BLUE


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


# --- waiting-room dice game --------------------------------------------------
class DiceEntry(BaseModel):
    user_id: int
    name: str
    rolls: int
    total: int
    avg: float
    best: int
    last_value: int


class DiceState(BaseModel):
    cooldown: float                 # seconds between rolls
    ranking: list[DiceEntry] = []
