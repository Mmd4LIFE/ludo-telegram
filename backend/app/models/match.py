"""A Ludo match (room) and its seats.

The authoritative game state lives in ``Match.state`` as JSON — the serialised
``app.ludo.state.GameState``. The DB is the durable store; the live in-memory runtime
(``app.game.runtime``) is what actually drives a match and periodically snapshots back
here so a restart can resume. Seats map a board seat index → a user (or a house bot).
"""
from __future__ import annotations

import enum
import secrets

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
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

    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    place: Mapped[int | None] = mapped_column(Integer, default=None)  # final placement 1..N

    match: Mapped["Match"] = relationship(back_populates="seats")
