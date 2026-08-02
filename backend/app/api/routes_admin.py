"""Admin-only views: who's playing and how the box is doing.

Gated by ``require_admin`` (ADMIN_IDS in the environment). Note the inherited privacy
rule still holds here: ``telegram_id`` never leaves the server, not even for an admin.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_admin
from app.database import Base, get_session
from app.models import Match, MatchSeat, MatchStatus, User, MessageReaction, ReactionEmoji, PollTemplate, Poll, AppConfig
from app.models import ChatMessage as ChatRow
from app import appconfig
from app.schemas import (
    AddPollTemplateRequest,
    AddReactionRequest,
    AdminChatEntry,
    AdminChatSeat,
    AdminChatView,
    AdminKnockRow,
    AppConfigOut,
    AdminStats,
    AdminUser,
    PollTemplateOut,
    ReactionEmojiOut,
    SetConfigRequest,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# --- data browser -----------------------------------------------------------
# Browsable tables come straight from the ORM metadata (a whitelist — a client can
# never name an arbitrary relation). Sensitive columns are redacted even for admins:
# telegram_id NEVER leaves the server, per the project's hard privacy rule.
_TABLES = Base.metadata.tables
_REDACT = {"users": {"telegram_id"}}
_DATA_MAX = 100


def _serialise(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


@router.get("/stats", response_model=AdminStats)
async def stats(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    async def count(stmt) -> int:
        return int((await session.execute(stmt)).scalar_one() or 0)

    users = await count(select(func.count()).select_from(User).where(User.is_bot.is_(False)))
    started = await count(
        select(func.count()).select_from(User).where(
            User.is_bot.is_(False), User.bot_started.is_(True)
        )
    )
    played = await count(
        select(func.coalesce(func.sum(User.games_played), 0)).where(User.is_bot.is_(False))
    )
    coins = await count(
        select(func.coalesce(func.sum(User.coins), 0)).where(User.is_bot.is_(False))
    )
    by_status: dict[str, int] = {}
    rows = (
        await session.execute(select(Match.status, func.count()).group_by(Match.status))
    ).all()
    for st, n in rows:
        by_status[st.value if hasattr(st, "value") else str(st)] = int(n)

    return AdminStats(
        users=users,
        users_started=started,
        games_played=played,
        coins_in_circulation=coins,
        matches_playing=by_status.get(MatchStatus.PLAYING.value, 0),
        matches_waiting=by_status.get(MatchStatus.WAITING.value, 0),
        matches_finished=by_status.get(MatchStatus.FINISHED.value, 0),
        matches_abandoned=by_status.get(MatchStatus.ABANDONED.value, 0),
    )


@router.get("/users", response_model=list[AdminUser])
async def list_users(
    q: str | None = Query(default=None, description="name/username search"),
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(User).where(User.is_bot.is_(False))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(User.first_name.ilike(like) | User.username.ilike(like))
    stmt = stmt.order_by(User.last_seen_at.desc().nullslast(), User.id.desc())
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return [AdminUser.from_user(u) for u in rows]


@router.get("/data/tables")
async def data_tables(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Every browsable table with its row count and (redacted) column list."""
    out = []
    for name, tbl in sorted(_TABLES.items()):
        count = (await session.execute(select(func.count()).select_from(tbl))).scalar_one()
        cols = [c.name for c in tbl.columns if c.name not in _REDACT.get(name, set())]
        out.append({"name": name, "rows": int(count or 0), "columns": cols})
    return out


# --- reaction emojis (admin-managed) ----------------------------------------
@router.get("/reactions", response_model=list[ReactionEmojiOut])
async def list_reactions(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(ReactionEmoji).order_by(ReactionEmoji.position, ReactionEmoji.id)
        )
    ).scalars().all()
    return [ReactionEmojiOut(id=r.id, emoji=r.emoji, position=r.position) for r in rows]


@router.post("/reactions", response_model=list[ReactionEmojiOut])
async def add_reaction(
    body: AddReactionRequest,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    emoji = body.emoji.strip()
    if not emoji or len(emoji) > 16:
        raise HTTPException(400, "Emoji required (max 16 chars)")
    exists = (
        await session.execute(select(ReactionEmoji).where(ReactionEmoji.emoji == emoji))
    ).scalar_one_or_none()
    if exists is None:
        top = (
            await session.execute(select(func.coalesce(func.max(ReactionEmoji.position), -1)))
        ).scalar_one()
        session.add(ReactionEmoji(emoji=emoji, position=int(top) + 1))
        await session.commit()
    return await list_reactions(_admin=_admin, session=session)


@router.delete("/reactions/{reaction_id}", response_model=list[ReactionEmojiOut])
async def remove_reaction(
    reaction_id: int,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(ReactionEmoji, reaction_id)
    if row is not None:
        await session.delete(row)
        await session.commit()
    return await list_reactions(_admin=_admin, session=session)


# --- app config (append-only) -----------------------------------------------
async def _configs_out(session: AsyncSession) -> list[AppConfigOut]:
    values = await appconfig.current_values(session)
    # which keys currently have a live override row?
    live = (
        await session.execute(
            select(AppConfig.key).where(AppConfig.deleted_at.is_(None)).distinct()
        )
    ).scalars().all()
    live_set = set(live)
    return [
        AppConfigOut(
            key=k, label=m["label"], help=m.get("help", ""),
            value=values[k], default=int(m["default"]), min=m["min"], max=m["max"],
            is_set=k in live_set,
        )
        for k, m in appconfig.TUNABLE.items()
    ]


@router.get("/configs", response_model=list[AppConfigOut])
async def list_configs(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _configs_out(session)


@router.post("/configs/{key}", response_model=list[AppConfigOut])
async def set_config(
    key: str,
    body: SetConfigRequest,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Change a config by INSERTING a new row (the table is append-only)."""
    meta = appconfig.TUNABLE.get(key)
    if meta is None:
        raise HTTPException(404, "Unknown config key")
    if not (meta["min"] <= body.value <= meta["max"]):
        raise HTTPException(400, f"Value must be between {meta['min']} and {meta['max']}")
    session.add(AppConfig(key=key, value=str(body.value)))
    await session.commit()
    return await _configs_out(session)


@router.delete("/configs/{key}", response_model=list[AppConfigOut])
async def reset_config(
    key: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Revert a config to its code default by soft-deleting its live override rows."""
    if key not in appconfig.TUNABLE:
        raise HTTPException(404, "Unknown config key")
    rows = (
        await session.execute(
            select(AppConfig).where(AppConfig.key == key, AppConfig.deleted_at.is_(None))
        )
    ).scalars().all()
    for row in rows:
        row.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    return await _configs_out(session)


# --- instant-poll templates -------------------------------------------------
def _tmpl_out(t: PollTemplate) -> PollTemplateOut:
    return PollTemplateOut(
        id=t.id, question=t.question, options=list(t.options or []),
        trigger=t.trigger, enabled=t.enabled,
    )


@router.get("/poll-templates", response_model=list[PollTemplateOut])
async def list_poll_templates(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(select(PollTemplate).order_by(PollTemplate.position, PollTemplate.id))
    ).scalars().all()
    return [_tmpl_out(t) for t in rows]


@router.post("/poll-templates", response_model=list[PollTemplateOut])
async def add_poll_template(
    body: AddPollTemplateRequest,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    q = body.question.strip()
    opts = [o.strip() for o in body.options if o.strip()]
    if not q or len(opts) < 2:
        raise HTTPException(400, "A question and at least two options are required")
    trigger = body.trigger if body.trigger in ("knock", "any") else "any"
    top = (await session.execute(select(func.coalesce(func.max(PollTemplate.position), -1)))).scalar_one()
    session.add(PollTemplate(question=q[:200], options=opts, trigger=trigger, position=int(top) + 1))
    await session.commit()
    return await list_poll_templates(_admin=_admin, session=session)


@router.delete("/poll-templates/{tmpl_id}", response_model=list[PollTemplateOut])
async def remove_poll_template(
    tmpl_id: int,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(PollTemplate, tmpl_id)
    if row is not None:
        # polls created from this template keep their own question/options; just detach
        # them so the FK doesn't block the delete.
        await session.execute(
            sa.update(Poll).where(Poll.template_id == tmpl_id).values(template_id=None)
        )
        await session.delete(row)
        await session.commit()
    return await list_poll_templates(_admin=_admin, session=session)


# --- knock leaderboard ------------------------------------------------------
@router.get("/knocks", response_model=list[AdminKnockRow])
async def knock_totals(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """All-time knock stats per player: knocks dealt, knocked, and passed-up (potential)."""
    rows = (
        await session.execute(
            select(User)
            .where(User.is_bot.is_(False))
            .order_by(User.captures_dealt.desc(), User.potential_knocks.desc())
        )
    ).scalars().all()
    return [
        AdminKnockRow(
            id=u.id, first_name=u.first_name or "Player",
            knocks=u.captures_dealt or 0, knocked=u.captures_taken or 0,
            potential=u.potential_knocks or 0,
        )
        for u in rows
    ]


# --- per-match chat viewer --------------------------------------------------
@router.get("/matches/{ref}/chat", response_model=AdminChatView)
async def match_chat(
    ref: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Everything said in one match — by numeric id or room code. Includes deleted
    messages (flagged) and each message's reaction tallies, plus who sat where."""
    match = None
    if ref.isdigit():
        match = await session.get(Match, int(ref))
    if match is None:
        match = (
            await session.execute(select(Match).where(Match.code == ref.upper()))
        ).scalar_one_or_none()
    if match is None:
        raise HTTPException(404, "Match not found")

    # seats + player names
    seats_rows = sorted(match.seats, key=lambda s: s.seat_index)
    ids = [s.user_id for s in seats_rows if s.user_id is not None]
    names: dict[int, str] = {}
    if ids:
        rows = (
            await session.execute(select(User.id, User.first_name).where(User.id.in_(ids)))
        ).all()
        names = {rid: (fn or "Player") for rid, fn in rows}
    seats = [
        AdminChatSeat(
            seat_index=s.seat_index, color=s.color,
            name=names.get(s.user_id, "Bot" if s.is_bot else "Open"),
            user_id=s.user_id, is_bot=s.is_bot,
        )
        for s in seats_rows
    ]

    # every message (incl. soft-deleted), oldest first
    msg_rows = (
        await session.execute(
            select(ChatRow).where(ChatRow.match_id == match.id).order_by(ChatRow.id)
        )
    ).scalars().all()

    counts: dict[int, dict[str, int]] = {}
    if msg_rows:
        mids = [m.id for m in msg_rows]
        reacts = (
            await session.execute(
                select(MessageReaction).where(MessageReaction.message_id.in_(mids))
            )
        ).scalars().all()
        for rx in reacts:
            counts.setdefault(rx.message_id, {})
            counts[rx.message_id][rx.emoji] = counts[rx.message_id].get(rx.emoji, 0) + 1

    messages = [
        AdminChatEntry(
            id=m.id, user_id=m.user_id, name=m.name, text=m.text,
            edited=m.edited, deleted=m.deleted_at is not None,
            created_at=m.created_at.isoformat() if m.created_at else None,
            reply_name=m.reply_name, reply_text=m.reply_text,
            reactions=counts.get(m.id, {}),
        )
        for m in msg_rows
    ]

    return AdminChatView(
        id=match.id, code=match.code, status=match.status.value,
        created_at=match.created_at.isoformat() if getattr(match, "created_at", None) else None,
        seats=seats, messages=messages,
    )


@router.get("/data/rows/{table}")
async def data_rows(
    table: str,
    limit: int = Query(default=25, le=_DATA_MAX),
    offset: int = 0,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Rows of one whitelisted table, newest first, with sensitive columns redacted."""
    tbl = _TABLES.get(table)
    if tbl is None:
        raise HTTPException(404, "Unknown table")
    limit = max(1, min(limit, _DATA_MAX))
    offset = max(0, offset)
    redact = _REDACT.get(table, set())
    cols = [c for c in tbl.columns if c.name not in redact]

    total = (await session.execute(select(func.count()).select_from(tbl))).scalar_one()
    stmt = select(*cols)
    pk = list(tbl.primary_key.columns)
    if len(pk) == 1:
        stmt = stmt.order_by(pk[0].desc())   # newest first
    stmt = stmt.limit(limit).offset(offset)
    result = (await session.execute(stmt)).mappings().all()
    data = [{k: _serialise(v) for k, v in row.items()} for row in result]
    return {
        "table": table,
        "columns": [c.name for c in cols],
        "rows": data,
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }
