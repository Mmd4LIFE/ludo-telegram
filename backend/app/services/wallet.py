"""Wallet transfers: direct send + claimable gift links (escrow)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.instance import get_bot_username
from app.models import User
from app.models.wallet import Transfer, TransferStatus, _new_claim_token
from app.services import ledger
from app.services.ledger import InsufficientFunds

GIFT_TTL_DAYS = 7
STAR_PACKS = (50, 100, 250, 500)


class WalletError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def direct_transfer(
    session: AsyncSession,
    *,
    from_user_id: int,
    to_user_id: int,
    amount: int,
    note: str | None = None,
) -> Transfer:
    if amount < 1:
        raise WalletError("Amount must be at least 1")
    if from_user_id == to_user_id:
        raise WalletError("Cannot transfer to yourself")
    dest = await session.get(User, to_user_id)
    if dest is None or dest.is_bot:
        raise WalletError("Recipient not found", 404)

    xfer = Transfer(
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        amount=amount,
        status=TransferStatus.COMPLETED.value,
        note=(note.strip()[:120] if note and note.strip() else None),
        claimed_at=datetime.now(timezone.utc),
    )
    session.add(xfer)
    await session.flush()
    try:
        await ledger.debit(
            session,
            from_user_id,
            amount,
            reason="transfer_out",
            ref_type="transfer",
            ref_id=xfer.id,
            meta={"to_user_id": to_user_id},
        )
    except InsufficientFunds as e:
        raise WalletError("Not enough coins") from e
    await ledger.credit(
        session,
        to_user_id,
        amount,
        reason="transfer_in",
        ref_type="transfer",
        ref_id=xfer.id,
        meta={"from_user_id": from_user_id},
    )
    return xfer


async def create_gift(
    session: AsyncSession,
    *,
    from_user_id: int,
    amount: int,
    note: str | None = None,
) -> Transfer:
    if amount < 1:
        raise WalletError("Amount must be at least 1")

    token = _new_claim_token()
    xfer = Transfer(
        from_user_id=from_user_id,
        to_user_id=None,
        amount=amount,
        status=TransferStatus.PENDING.value,
        claim_token=token,
        note=(note.strip()[:120] if note and note.strip() else None),
    )
    session.add(xfer)
    await session.flush()
    try:
        await ledger.debit(
            session,
            from_user_id,
            amount,
            reason="transfer_out",
            ref_type="transfer",
            ref_id=xfer.id,
            meta={"kind": "gift_escrow"},
        )
    except InsufficientFunds as e:
        raise WalletError("Not enough coins") from e
    return xfer


def gift_share_url(token: str) -> str:
    bot = get_bot_username()
    return f"https://t.me/{bot}?start=gift-{token}"


def _is_expired(xfer: Transfer) -> bool:
    created = xfer.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > timedelta(days=GIFT_TTL_DAYS)


async def expire_if_needed(session: AsyncSession, xfer: Transfer) -> Transfer:
    if xfer.status != TransferStatus.PENDING.value:
        return xfer
    if not _is_expired(xfer):
        return xfer
    xfer.status = TransferStatus.EXPIRED.value
    await ledger.credit(
        session,
        xfer.from_user_id,
        int(xfer.amount),
        reason="transfer_refund",
        ref_type="transfer",
        ref_id=xfer.id,
        meta={"kind": "gift_expired"},
    )
    return xfer


async def claim_gift(
    session: AsyncSession,
    *,
    claimer_id: int,
    token: str,
) -> Transfer:
    token = (token or "").strip()
    if not token:
        raise WalletError("Missing gift token")
    xfer = (
        await session.execute(select(Transfer).where(Transfer.claim_token == token))
    ).scalar_one_or_none()
    if xfer is None:
        raise WalletError("Gift not found", 404)
    await expire_if_needed(session, xfer)
    if xfer.status == TransferStatus.EXPIRED.value:
        raise WalletError("This gift has expired")
    if xfer.status == TransferStatus.CLAIMED.value:
        raise WalletError("Gift already claimed")
    if xfer.status != TransferStatus.PENDING.value:
        raise WalletError("Gift is not claimable")
    if xfer.from_user_id == claimer_id:
        raise WalletError("Cannot claim your own gift")

    xfer.to_user_id = claimer_id
    xfer.status = TransferStatus.CLAIMED.value
    xfer.claimed_at = datetime.now(timezone.utc)
    await ledger.credit(
        session,
        claimer_id,
        int(xfer.amount),
        reason="transfer_in",
        ref_type="transfer",
        ref_id=xfer.id,
        meta={"from_user_id": xfer.from_user_id, "kind": "gift"},
    )
    return xfer
