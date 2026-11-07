"""Player profile endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.appconfig import current_values
from app.database import get_session
from app.models import User
from app.schemas import BuyDiceSkinRequest, SetDiceSkinRequest, UserProfile
from app.services import ledger
from app.services import xp as xp_service
from app.services.leveling import MAX_LEVEL, progress, roadmap
from app.services.coin_features import DICE_SKIN_PRICES
from app.services.ledger import InsufficientFunds

router = APIRouter(prefix="/api/profile", tags=["profile"])

# Keep in step with DICE_SKINS in the Mini App (web/lib/skins.ts).
DICE_SKINS = set(DICE_SKIN_PRICES.keys())


@router.get("/me", response_model=UserProfile)
async def me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    cfg = await current_values(session)
    out = UserProfile.from_user(user)
    out.xp_today = await xp_service.action_xp_today(session, user.id)
    out.xp_daily_cap = xp_service.daily_cap(cfg)
    return out


@router.get("/levels")
async def levels(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Current progress, today's capped XP, and the next 10 levels with their coin rewards."""
    cfg = await current_values(session)
    return {
        "progress": progress(user.xp or 0),
        "xp_today": await xp_service.action_xp_today(session, user.id),
        "xp_daily_cap": xp_service.daily_cap(cfg),
        "max_level": MAX_LEVEL,
        "roadmap": roadmap(user.xp or 0, 10, cfg),
        "xp_sources": {
            "place_1": cfg["XP_PLACE_1"], "place_2": cfg["XP_PLACE_2"],
            "place_3": cfg["XP_PLACE_3"], "place_last": cfg["XP_PLACE_4"],
            "knock": cfg["XP_KNOCK"], "home": cfg["XP_HOME"],
            "card": cfg["XP_CARD"], "couldve": cfg["XP_COULDVE"],
            "bot_table_pct": cfg["XP_BOT_TABLE_PCT"],
        },
    }


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
