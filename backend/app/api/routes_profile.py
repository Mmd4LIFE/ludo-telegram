"""Player profile endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models import User
from app.schemas import SetDiceSkinRequest, UserProfile

router = APIRouter(prefix="/api/profile", tags=["profile"])

# Keep in step with DICE_SKINS in the Mini App (web/lib/skins.ts).
DICE_SKINS = {"classic", "gold", "night", "mint", "ruby", "ocean"}


@router.get("/me", response_model=UserProfile)
async def me(user: User = Depends(get_current_user)):
    return UserProfile.from_user(user)


@router.post("/dice-skin", response_model=UserProfile)
async def set_dice_skin(
    body: SetDiceSkinRequest,
    user: User = Depends(get_current_user),
):
    skin = body.skin.strip().lower()
    if skin not in DICE_SKINS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown dice skin")
    user.dice_skin = skin
    return UserProfile.from_user(user)
