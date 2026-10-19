"""A Ludo match (room) and its seats.

The authoritative game state lives in ``Match.state`` as JSON — the serialised
``app.ludo.state.GameState``. The DB is the durable store; the live in-memory runtime
(``app.game.runtime``) is what actually drives a match and periodically snapshots back
here so a restart can resume. Seats map a board seat index → a user (or a house bot).
"""
from __future__ import annotations

import enum
import secrets
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin


class MatchStatus(str, enum.Enum):
    WAITING = "waiting"      # seats filling in the lobby
    PLAYING = "playing"      # game in progress
    FINISHED = "finished"    # someone won; ranking is final
    ABANDONED = "abandoned"  # closed with nobody around


def _new_code() -> str:
    # short, unambiguous room code for deep links (no 0/O/1/I).
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(5))


class Match(Base, TimestampMixin):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True, default=_new_code)
    status: Mapped[MatchStatus] = mapped_column(
        # values_callable pins the DB representation to the enum *values*
        # ("waiting", "playing", …) — the lowercase strings the migration created.
        # Without it SQLAlchemy persists member *names* (WAITING, PLAYING) and every
        # query mismatches the Postgres enum type.
        Enum(MatchStatus, name="match_status", values_callable=lambda e: [m.value for m in e]),
        default=MatchStatus.WAITING,
        index=True,
    )
    max_players: Mapped[int] = mapped_column(Integer, default=4)   # 2..4
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_bot_table: Mapped[bool] = mapped_column(Boolean, default=False)  # self-play lobby filler

    entry_fee: Mapped[int] = mapped_column(Integer, default=0)     # coins staked per seat
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    # serialised app.ludo.state.GameState (null until the game starts)
    state: Mapped[dict | None] = mapped_column(JSONB, default=None)

    seats: Mapped[list["MatchSeat"]] = relationship(
        back_populates="match", cascade="all, delete-orphan", lazy="selectin"
    )


class MatchSeat(Base, TimestampMixin):
    __tablename__ = "match_seats"
    __table_args__ = (UniqueConstraint("match_id", "seat_index", name="uq_seat_per_match"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    seat_index: Mapped[int] = mapped_column(Integer)   # 0..3, maps to a board colour
    color: Mapped[str] = mapped_column(String(8))      # RED/GREEN/YELLOW/BLUE

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None, index=True
    )
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    place: Mapped[int | None] = mapped_column(Integer, default=None)  # final placement 1..N
    # NULL for players present at game start; the engine turn when a late joiner entered.
    joined_at_turn: Mapped[int | None] = mapped_column(Integer, default=None)

    match: Mapped["Match"] = relationship(back_populates="seats")


class MatchJoinRequest(Base):
    """Durable request to join a waiting or already-started room.

    One row per match/player is reused when a rejected player requests again. Keeping
    resolved rows gives us an audit trail without allowing duplicate pending requests.
    """

    __tablename__ = "match_join_requests"
    __table_args__ = (
        UniqueConstraint("match_id", "user_id", name="uq_join_request_match_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
