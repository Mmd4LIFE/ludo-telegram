"""Telegram Mini App auth + JWT session tokens.

Two responsibilities:
  1. Validate the ``initData`` blob Telegram hands the Mini App (HMAC per the WebApp spec).
  2. Mint / verify our own short JWT session tokens the frontend sends as a Bearer header.

This is a byte-for-byte port of the poker app's proven implementation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=7)


class AuthError(Exception):
    pass


def validate_init_data(init_data: str, max_age: int = 86400) -> dict:
    """Validate a Telegram WebApp initData string and return the parsed fields.

    Raises AuthError on a bad signature or a stale auth_date. The returned dict has the
    decoded ``user`` object (a dict) plus any ``start_param`` (deep-link referral).
    """
    if not init_data:
        raise AuthError("empty initData")
    if not settings.BOT_TOKEN:
        raise AuthError("BOT_TOKEN not configured")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("missing hash")

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        raise AuthError("bad signature")

    auth_date = int(pairs.get("auth_date", "0"))
    if max_age and auth_date and (time.time() - auth_date) > max_age:
        raise AuthError("initData expired")

    user_raw = pairs.get("user")
    if not user_raw:
        raise AuthError("no user in initData")
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError as e:
        raise AuthError("bad user json") from e

    return {"user": user, "start_param": pairs.get("start_param")}


def create_access_token(user_id: int, telegram_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tg": telegram_id,
        "iat": int(now.timestamp()),
        "exp": int((now + TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise AuthError(str(e)) from e
