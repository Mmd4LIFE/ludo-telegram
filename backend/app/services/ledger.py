"""Professional coin ledger — every mutation goes through credit/debit.

``balances`` is the source of truth; ``users.coins`` is kept in sync for existing API
surfaces. ``balance_history`` is append-only audit.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.ledger import Balance, BalanceHistory

CURRENCY = "coins"


class InsufficientFunds(Exception):
    """Raised when a debit would take the balance below zero (without floor)."""


async def ensure_balance(session: AsyncSession, user_id: int) -> Balance:
    """Get or create the coins balance row, seeding from ``users.coins`` if new."""
    bal = (
        await session.execute(
            select(Balance)
            .where(Balance.user_id == user_id, Balance.currency == CURRENCY)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if bal is not None:
        return bal
    user = await session.get(User, user_id)
    seed = int(user.coins or 0) if user else 0
    bal = Balance(user_id=user_id, currency=CURRENCY, amount=seed)
    session.add(bal)
    await session.flush()
    if seed:
        session.add(
            BalanceHistory(
                user_id=user_id,
                currency=CURRENCY,
                delta=seed,
                balance_after=seed,
                reason="opening_balance",
            )
        )
    return bal


async def credit(
    session: AsyncSession,
    user_id: int,
    amount: int,
    *,
    reason: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    meta: dict | None = None,
) -> int:
    """Add coins. Returns the new balance."""
    if amount < 0:
        raise ValueError("credit amount must be >= 0")
    if amount == 0:
        bal = await ensure_balance(session, user_id)
        return int(bal.amount)
    bal = await ensure_balance(session, user_id)
    bal.amount = int(bal.amount) + amount
    session.add(
        BalanceHistory(
            user_id=user_id,
            currency=CURRENCY,
            delta=amount,
            balance_after=int(bal.amount),
            reason=reason,
            ref_type=ref_type,
            ref_id=ref_id,
            meta=meta,
        )
    )
    user = await session.get(User, user_id)
    if user is not None:
        user.coins = int(bal.amount)
    return int(bal.amount)


async def debit(
    session: AsyncSession,
    user_id: int,
    amount: int,
    *,
    reason: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    meta: dict | None = None,
    floor_zero: bool = False,
) -> int:
    """Remove coins. Returns the new balance.

    If ``floor_zero`` is True, debit only as much as available (never negative).
    Otherwise raises ``InsufficientFunds`` when amount > balance.
    """
    if amount < 0:
        raise ValueError("debit amount must be >= 0")
    bal = await ensure_balance(session, user_id)
    current = int(bal.amount)
    if amount == 0:
        return current
    take = amount
    if take > current:
        if not floor_zero:
            raise InsufficientFunds(f"need {amount}, have {current}")
        take = current
    if take == 0:
        return current
    bal.amount = current - take
    session.add(
        BalanceHistory(
            user_id=user_id,
            currency=CURRENCY,
            delta=-take,
            balance_after=int(bal.amount),
            reason=reason,
            ref_type=ref_type,
            ref_id=ref_id,
            meta=meta,
        )
    )
    user = await session.get(User, user_id)
    if user is not None:
        user.coins = int(bal.amount)
    return int(bal.amount)


def placement_payout(cfg: dict, place: int, human_count: int) -> int:
    """Look up placement reward from admin tunables. 0 if no payout for that slot."""
    if human_count < 2 or place < 1:
        return 0
    key = {
        (1, 4): "PAY_1ST_4",
        (1, 3): "PAY_1ST_3",
        (1, 2): "PAY_1ST_2",
        (2, 4): "PAY_2ND_4",
        (2, 3): "PAY_2ND_3",
        (3, 4): "PAY_3RD_4",
    }.get((place, human_count))
    if not key:
        return 0
    return max(0, int(cfg.get(key, 0)))
