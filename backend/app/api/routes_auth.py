"""Authentication endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import AuthError, create_access_token, validate_init_data
from app.database import get_session
from app.schemas import AuthRequest, DevAuthRequest, TokenResponse, UserProfile
from app.services.users import get_or_create_from_telegram

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/telegram", response_model=TokenResponse)
async def auth_telegram(body: AuthRequest, session: AsyncSession = Depends(get_session)):
    try:
        data = validate_init_data(body.init_data)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    referral = data.get("start_param")
    user, _ = await get_or_create_from_telegram(session, data["user"], referral=referral)
    await session.flush()
    token = create_access_token(user.id, user.telegram_id)
    return TokenResponse(token=token, user=UserProfile.from_user(user))


@router.post("/dev", response_model=TokenResponse)
async def auth_dev(body: DevAuthRequest, session: AsyncSession = Depends(get_session)):
    """Local-only fake login. Disabled in production."""
    if settings.ENV == "production":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Disabled in production")
    user, _ = await get_or_create_from_telegram(session, {
        "id": body.telegram_id,
        "first_name": body.first_name,
        "username": body.username,
    })
    await session.flush()
    token = create_access_token(user.id, user.telegram_id)
    return TokenResponse(token=token, user=UserProfile.from_user(user))
