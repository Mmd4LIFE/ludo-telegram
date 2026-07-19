"""Player profile endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models import User
from app.schemas import UserProfile

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("/me", response_model=UserProfile)
async def me(user: User = Depends(get_current_user)):
    return UserProfile.from_user(user)
