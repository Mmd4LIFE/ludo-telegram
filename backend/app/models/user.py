"""The player account.

One row per Telegram user (and per house bot). ``telegram_id`` is PRIVATE — it must
never be exposed through any API response or websocket payload (a hard rule carried over
from the poker app). ``username`` is only ever shown to a player's friends.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Telegram numeric id — PRIVATE, never returned to any client.
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), default=None)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    language_code: Mapped[str | None] = mapped_column(String(8), default=None)

    # soft currency
    coins: Mapped[int] = mapped_column(BigInteger, default=0)

    # progression
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    games_played: Mapped[int] = mapped_column(Integer, default=0)
    games_won: Mapped[int] = mapped_column(Integer, default=0)

    # lifetime dice + combat stats (fed by finished games)
    # dice_hist: {"1": n, ..., "6": n} — count of each die face this player has ever rolled.
    dice_hist: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    captures_dealt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")   # you knocked others home
    captures_taken: Mapped[int] = mapped_column(Integer, default=0, server_default="0")   # others knocked you home

    # cosmetics — which die face this player rolls with
    dice_skin: Mapped[str] = mapped_column(String(16), default="classic", server_default="classic")

    # flags / presence
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    bot_started: Mapped[bool] = mapped_column(Boolean, default=False)  # pressed /start
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # referral graph
    referred_by: Mapped[int | None] = mapped_column(BigInteger, default=None)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} tg=*** username={self.username!r}>"
