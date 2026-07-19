"""User lookup / creation from a Telegram identity."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User


async def get_or_create_from_telegram(
    session: AsyncSession, tg_user: dict, *, referral: str | None = None
) -> tuple[User, bool]:
    """Return (user, created). Grants the signup bonus + applies a referral once."""
    telegram_id = int(tg_user["id"])
    existing = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if existing is not None:
        # keep display fields fresh
        existing.first_name = tg_user.get("first_name") or existing.first_name
        existing.username = tg_user.get("username") or existing.username
        existing.language_code = tg_user.get("language_code") or existing.language_code
        return existing, False

    user = User(
        telegram_id=telegram_id,
        first_name=tg_user.get("first_name") or "Player",
        username=tg_user.get("username"),
        language_code=tg_user.get("language_code"),
        coins=settings.SIGNUP_BONUS_COINS,
    )
    session.add(user)
    await session.flush()

    if referral:
        await _apply_referral(session, user, referral)

    return user, True


async def _apply_referral(session: AsyncSession, new_user: User, referral: str) -> None:
    """Referral deep-link is ``ref-<referrer_user_id>``. Rewards both sides once."""
    if not referral.startswith("ref-"):
        return
    try:
        referrer_id = int(referral[4:])
    except ValueError:
        return
    referrer = await session.get(User, referrer_id)
    if referrer is None or referrer.id == new_user.id:
        return
    new_user.referred_by = referrer.telegram_id
    new_user.coins += settings.REFERRAL_FRIEND_REWARD
    referrer.coins += settings.REFERRAL_REFERRER_REWARD
