"""Append-only game-event logs (humans only): every die roll and every knockout.

These are the detailed source of truth behind the aggregate counters on ``User``
(dice_hist, captures_dealt/taken, potential_knocks). A knockout row is either an actual
capture (``taken=True``) or a *potential* one — a capture that was legal on that move but
not played (``taken=False``).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DiceRoll(Base):
    __tablename__ = "dice_rolls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    seat: Mapped[int] = mapped_column(Integer)
    value: Mapped[int] = mapped_column(Integer)
    turn: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Knockout(Base):
    __tablename__ = "knockouts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    turn: Mapped[int] = mapped_column(Integer, default=0)
    attacker_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    attacker_seat: Mapped[int] = mapped_column(Integer)
    victim_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    victim_seat: Mapped[int] = mapped_column(Integer)
    # True = a capture actually made; False = a legal capture the player passed up.
    taken: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Card(Base):
    """A fantasy card in the catalog. Definitions live in the DB (seeded by migration),
    not in code, so the set can grow without a deploy."""

    __tablename__ = "cards"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(48))
    icon: Mapped[str] = mapped_column(String(16), default="")
    rarity: Mapped[str] = mapped_column(String(16), default="common")
    effect: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="soon")   # "live" | "soon"
    description: Mapped[str] = mapped_column(String(200), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class CardDraw(Base):
    """A fantasy-card draw: the four options offered and the one the player picked."""

    __tablename__ = "card_draws"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    seat: Mapped[int] = mapped_column(Integer)
    options: Mapped[list] = mapped_column(JSONB, default=list)
    picked: Mapped[str] = mapped_column(String(32))
    turn: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
