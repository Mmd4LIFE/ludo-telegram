"""Throttled last_seen updates off the request session.

Writing ``User.last_seen_at`` on every authenticated REST call dirties the ORM row and
forces a commit via ``get_session``. Under lobby/chat poll storms that becomes write
amplification. Instead we schedule a best-effort touch at most once per
``PRESENCE_TOUCH_SECONDS`` using a dedicated session.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings
from app.database import SessionLocal
from app.models import User

logger = logging.getLogger("ludo.presence")

# user_id -> monotonic time of last scheduled/completed touch
_last_touch: dict[int, float] = {}
_lock = asyncio.Lock()


def should_touch(user_id: int) -> bool:
    interval = max(5.0, float(settings.PRESENCE_TOUCH_SECONDS))
    last = _last_touch.get(user_id)
    return last is None or (time.monotonic() - last) >= interval


def schedule_touch(user_id: int) -> None:
    """Fire-and-forget presence update if the throttle window has elapsed."""
    if not should_touch(user_id):
        return
    _last_touch[user_id] = time.monotonic()
    try:
        asyncio.get_running_loop().create_task(_touch(user_id))
    except RuntimeError:
        _last_touch.pop(user_id, None)


async def _touch(user_id: int) -> None:
    try:
        async with SessionLocal() as session:
            user = await session.get(User, user_id)
            if user is None:
                return
            from datetime import datetime, timezone

            user.last_seen_at = datetime.now(timezone.utc)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.debug("presence touch failed for user %s", user_id, exc_info=True)
        async with _lock:
            _last_touch.pop(user_id, None)
