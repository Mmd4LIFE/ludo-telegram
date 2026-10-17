"""Player profile endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_session
from app.models import User
from app.schemas import BuyDiceSkinRequest, SetDiceSkinRequest, UserProfile
from app.services import ledger
from app.services.coin_features import DICE_SKIN_PRICES
from app.services.ledger import InsufficientFunds

router = APIRouter(prefix="/api/profile", tags=["profile"])

# Keep in step with DICE_SKINS in the Mini App (web/lib/skins.ts).
DICE_SKINS = set(DICE_SKIN_PRICES.keys())


@router.get("/me", response_model=UserProfile)
async def me(user: User = Depends(get_current_user)):
    return UserProfile.from_user(user)


@router.get("/dice-skins")
async def dice_skin_catalog(_user: User = Depends(get_current_user)):
    """Prices for the dice-skin shop (0 = free / starter)."""
    return [
        {"id": sid, "price": price}
        for sid, price in DICE_SKIN_PRICES.items()
    ]


@router.post("/dice-skin", response_model=UserProfile)
async def set_dice_skin(
    body: SetDiceSkinRequest,
    user: User = Depends(get_current_user),
):
    skin = body.skin.strip().lower()
    if skin not in DICE_SKINS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown dice skin")
    unlocked = list(user.unlocked_skins or ["classic"])
    if skin not in unlocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Skin not unlocked — buy it first")
    user.dice_skin = skin
    return UserProfile.from_user(user)


@router.post("/dice-skin/buy", response_model=UserProfile)
async def buy_dice_skin(
    body: BuyDiceSkinRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    skin = body.skin.strip().lower()
    if skin not in DICE_SKINS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown dice skin")
    unlocked = list(user.unlocked_skins or ["classic"])
    if skin in unlocked:
        user.dice_skin = skin
        return UserProfile.from_user(user)
    price = int(DICE_SKIN_PRICES.get(skin, 0))
    if price > 0:
        try:
            await ledger.debit(
                session, user.id, price, reason="skin_unlock", meta={"skin": skin}
            )
        except InsufficientFunds as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not enough coins") from e
    unlocked.append(skin)
    user.unlocked_skins = unlocked
    user.dice_skin = skin
    return UserProfile.from_user(user)
