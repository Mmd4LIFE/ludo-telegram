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


class ReactionEmojiOut(BaseModel):
    id: int
    emoji: str
    position: int = 0


class AddReactionRequest(BaseModel):
    emoji: str


class AdminChatSeat(BaseModel):
    seat_index: int
    color: str
    name: str
    user_id: int | None = None
    is_bot: bool = False


class AdminChatEntry(BaseModel):
    id: int
    user_id: int
    name: str
    text: str
    edited: bool = False
    deleted: bool = False
    created_at: str | None = None
    reply_name: str | None = None
    reply_text: str | None = None
    reactions: dict[str, int] = {}


class AdminChatView(BaseModel):
    id: int
    code: str
    status: str
    created_at: str | None = None
    seats: list[AdminChatSeat] = []
    messages: list[AdminChatEntry] = []


class AppConfigOut(BaseModel):
    key: str
    label: str
    help: str = ""
    value: int          # current effective value
    default: int
    min: int
    max: int
    is_set: bool = False   # true when overridden from the code default


class SetConfigRequest(BaseModel):
    value: int


class AdminStats(BaseModel):
    users: int
    users_started: int
    games_played: int
    coins_in_circulation: int
    matches_playing: int
    matches_waiting: int
    matches_finished: int
    matches_abandoned: int


# --- public profiles & scoreboard -------------------------------------------
class PlayerStats(BaseModel):
    """A player's public record — dice luck + combat. NEVER carries telegram_id."""

    id: int
    first_name: str
    level: int
    games_played: int
    games_won: int
    dice: dict[str, int]        # {"1": n, ..., "6": n}
    dice_rolls: int             # total dice ever rolled
    dice_avg: float             # average face value (0 if never rolled)
    captures_dealt: int         # times this player knocked someone home
    captures_taken: int         # times this player was knocked home
    potential_knocks: int       # captures this player could have made but didn't

    @classmethod
    def from_user(cls, u) -> "PlayerStats":
        hist = {str(f): int((u.dice_hist or {}).get(str(f), 0)) for f in range(1, 7)}
        rolls = sum(hist.values())
        total = sum(f * n for f, n in ((int(k), v) for k, v in hist.items()))
        return cls(
            id=u.id,
            first_name=u.first_name or "Player",
            level=u.level,
            games_played=u.games_played,
            games_won=u.games_won,
            dice=hist,
            dice_rolls=rolls,
            dice_avg=round(total / rolls, 2) if rolls else 0.0,
            captures_dealt=u.captures_dealt or 0,
            captures_taken=u.captures_taken or 0,
            potential_knocks=u.potential_knocks or 0,
        )


class CardOut(BaseModel):
    """A fantasy card as the Mini App sees it."""

    id: str
    name: str
    icon: str
    rarity: str
    effect: str
    status: str          # "live" | "soon"
    description: str

    @classmethod
    def from_row(cls, c) -> "CardOut":
        return cls(
            id=c.id, name=c.name, icon=c.icon, rarity=c.rarity,
            effect=c.effect, status=c.status, description=c.description,
        )


class KnockEvent(BaseModel):
    """One knockout from a match: an actual capture (taken=True) or a passed-up one."""

    id: int
    turn: int
    taken: bool
    attacker_user_id: int
    attacker_seat: int
    attacker_name: str
    victim_user_id: int | None = None
    victim_seat: int
    victim_name: str


class AdminKnockRow(BaseModel):
    """Per-player knock totals for the admin view."""

    id: int
    first_name: str
    knocks: int          # captures_dealt
    knocked: int         # captures_taken
    potential: int       # potential_knocks


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
class PollOptionOut(BaseModel):
    id: int
    text: str
    position: int = 0
    votes: int = 0


class PollOut(BaseModel):
    id: int
    question: str
    kind: str = "instant"
    status: str = "open"
    total_votes: int = 0
    my_vote: int | None = None       # option id the viewer voted for, if any
    options: list[PollOptionOut] = []


class PollTemplateOut(BaseModel):
    id: int
    question: str
    options: list[str] = []
    trigger: str = "any"             # "knock" | "any"
    enabled: bool = True


class CreatePollRequest(BaseModel):
    template_id: int


class VoteRequest(BaseModel):
    option_id: int


class AddPollTemplateRequest(BaseModel):
    question: str
    options: list[str] = []
    trigger: str = "any"


class ChatMessage(BaseModel):
    id: int
    user_id: int
    name: str
    text: str
    edited: bool = False
    reply_to: int | None = None      # id of the message this replies to
    reply_name: str | None = None    # snapshot of who is being replied to
    reply_text: str | None = None    # snapshot of the replied-to text (truncated)
    reactions: dict[str, int] = {}   # emoji -> count
    my_reaction: str | None = None   # the viewer's own reaction, if any
    poll: PollOut | None = None      # present when this message is a poll


class SendChatRequest(BaseModel):
    text: str
    reply_to: int | None = None


class ReactRequest(BaseModel):
    emoji: str


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
