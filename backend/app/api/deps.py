"""Shared FastAPI dependencies."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import AuthError, decode_access_token
from app.database import get_session
from app.models import User


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    user = await session.get(User, int(payload["sub"]))
    if user is None or user.is_banned:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    # refresh presence, throttled to ~30s
    now = datetime.now(timezone.utc)
    ls = user.last_seen_at
    if ls is not None and ls.tzinfo is None:
        ls = ls.replace(tzinfo=timezone.utc)
    if ls is None or (now - ls).total_seconds() > 30:
        user.last_seen_at = now
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.telegram_id not in settings.admin_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user
