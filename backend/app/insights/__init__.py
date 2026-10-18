"""Admin Insights: an OpenAI analyst over a read-only view of the database (RAG over
schema + business docs + examples), plus the native read-only SQL runner."""
from __future__ import annotations

import logging

logger = logging.getLogger("ludo.insights")


async def bootstrap() -> None:
    """Boot-time setup: sync the read-only role, then (re)index the knowledge store."""
    from app.database import SessionLocal
    from app.insights import knowledge, readonly

    await readonly.ensure_role()
    try:
        async with SessionLocal() as session:
            stats = await knowledge.sync(session)
        logger.info("insights knowledge synced: %s", stats)
    except Exception:  # noqa: BLE001
        logger.exception("insights knowledge sync failed")
