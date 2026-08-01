"""A persisted chat message in a match room.

Chat used to live in a process-local dict; it now durably persists here so history
survives restarts and can feed later features (moderation, replays, notifications).
One row per message; edits flip ``edited`` and rewrite ``text`` in place, deletes remove
the row. Replies snapshot the parent's author + a text excerpt so they render even if the
parent is later deleted.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(128), default="")
    text: Mapped[str] = mapped_column(String(400), default="")
    edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    reply_to: Mapped[int | None] = mapped_column(Integer, default=None)
    reply_name: Mapped[str | None] = mapped_column(String(128), default=None)
    reply_text: Mapped[str | None] = mapped_column(String(120), default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
