"""Append-only game-event logs (humans only): every die roll and every knockout.

These are the detailed source of truth behind the aggregate counters on ``User``
(dice_hist, captures_dealt/taken, potential_knocks). A knockout row is either an actual
capture (``taken=True``) or a *potential* one — a capture that was legal on that move but
not played (``taken=False``).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, func
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
