"""Player wallet: balance history, direct transfers, claimable gifts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.database import get_session
from app.models import User
from app.models.ledger import BalanceHistory
from app.schemas import (
    BalanceHistoryRow,
    GiftClaimRequest,
    GiftCreateRequest,
    TransferOut,
    TransferRequest,
    WalletLookup,
)
from app.services import wallet as wallet_svc
from app.services.wallet import WalletError

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _xfer_out(xfer, coins: int, *, include_token: bool = False) -> TransferOut:
    share = None
    token = None
    if include_token and xfer.claim_token:
        token = xfer.claim_token
        share = wallet_svc.gift_share_url(xfer.claim_token)
    return TransferOut(
        id=xfer.id,
        from_user_id=xfer.from_user_id,
        to_user_id=xfer.to_user_id,
        amount=int(xfer.amount),
        status=xfer.status,
        note=xfer.note,
        claim_token=token,
        share_url=share,
        created_at=_iso(xfer.created_at),
        claimed_at=_iso(xfer.claimed_at),
        coins=coins,
    )


@router.get("/history", response_model=list[BalanceHistoryRow])
async def history(
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(BalanceHistory)
            .where(BalanceHistory.user_id == user.id)
            .order_by(BalanceHistory.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return [
        BalanceHistoryRow(
            id=r.id,
            delta=int(r.delta),
            balance_after=int(r.balance_after),
            reason=r.reason,
            ref_type=r.ref_type,
            ref_id=r.ref_id,
            created_at=_iso(r.created_at),
        )
        for r in rows
    ]


@router.get("/lookup", response_model=WalletLookup)
async def lookup(
    id: int = Query(..., ge=1),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if id == user.id:
        raise HTTPException(400, "That is your own wallet")
    dest = await session.get(User, id)
    if dest is None or dest.is_bot:
        raise HTTPException(404, "Wallet not found")
    return WalletLookup(id=dest.id, first_name=dest.first_name or "Player")


@router.post("/transfer", response_model=TransferOut)
async def transfer(
    body: TransferRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        xfer = await wallet_svc.direct_transfer(
            session,
            from_user_id=user.id,
            to_user_id=body.to_user_id,
            amount=int(body.amount),
            note=body.note,
        )
    except WalletError as e:
        raise HTTPException(e.status_code, e.message) from e
    await session.commit()
    await session.refresh(user)
    return _xfer_out(xfer, int(user.coins))


@router.post("/gifts", response_model=TransferOut)
async def create_gift(
    body: GiftCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        xfer = await wallet_svc.create_gift(
            session,
            from_user_id=user.id,
            amount=int(body.amount),
            note=body.note,
        )
    except WalletError as e:
        raise HTTPException(e.status_code, e.message) from e
    await session.commit()
    await session.refresh(user)
    return _xfer_out(xfer, int(user.coins), include_token=True)


@router.post("/gifts/claim", response_model=TransferOut)
async def claim_gift(
    body: GiftClaimRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        xfer = await wallet_svc.claim_gift(
            session, claimer_id=user.id, token=body.token
        )
    except WalletError as e:
        raise HTTPException(e.status_code, e.message) from e
    await session.commit()
    await session.refresh(user)
    return _xfer_out(xfer, int(user.coins))
