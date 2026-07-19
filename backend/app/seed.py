"""Idempotent seed run at container start (after migrations).

For the base this is a no-op placeholder that just confirms the DB is reachable and the
models import cleanly. A follow-up session adds house-bot user rows, cosmetic catalogs,
etc. Safe to run repeatedly.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models import User

logger = logging.getLogger("ludo.seed")


async def _seed() -> None:
    async with SessionLocal() as session:
        count = len((await session.execute(select(User.id))).scalars().all())
        logger.info("seed: %d users present", count)


def main() -> None:
    asyncio.run(_seed())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
