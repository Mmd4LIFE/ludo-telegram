"""Application configuration loaded from environment variables.

Mirrors the poker app's settings shape so the deploy story and the docs carry over. Keep
new knobs small and documented — this runs on a shared 1GB box.
"""
from __future__ import annotations

from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    # Telegram
    BOT_TOKEN: str = ""
    PUBLIC_URL: str = "https://localhost"
    WEBAPP_URL: str = "https://localhost"
    BOT_MODE: str = "polling"  # polling | webhook
    WEBHOOK_SECRET: str = "change-me"

    # Security
    SECRET_KEY: str = "change-me"
    ADMIN_IDS: str = ""

    # Database
    POSTGRES_USER: str = "ludo"
    POSTGRES_PASSWORD: str = "ludo_secret"
    POSTGRES_DB: str = "ludo"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432

    # Economy (coins are a soft currency; identical faucet/sink model to poker)
    SIGNUP_BONUS_COINS: int = 10000
    DAILY_REWARD_COINS: int = 1000
    BOT_START_BONUS: int = 2000

    # Matches / rooms
    MAX_ACTIVE_ROOMS_PER_USER: int = 3
    ROOM_IDLE_CLOSE_HOURS: float = 1.0

    # Gameplay
    TURN_TIMEOUT_SECONDS: int = 20          # a player has this long to act, else auto-play
    BOT_THINK_MIN: float = 1.2
    BOT_THINK_MAX: float = 2.4
    ROLL_REVEAL_SECONDS: float = 1.0        # hold on the rolled die so everyone sees it
    NO_MOVE_SECONDS: float = 1.2            # pause to show "no legal moves" before passing
    MOVE_SETTLE_SECONDS: float = 0.45       # let a token's glide finish before the next act
    IDLE_SEAT_GRACE_SECONDS: int = 90       # keep a seat this long after disconnect
    JANITOR_INTERVAL_SECONDS: int = 30

    # Self-play tables that keep the lobby alive (each is one asyncio task).
    BOT_TABLES: int = 1
    BOT_TABLE_IDLE_SECONDS: float = 90.0    # throttle when nobody is watching

    # Referrals
    REFERRAL_REFERRER_REWARD: int = 5000
    REFERRAL_FRIEND_REWARD: int = 2500
    BOT_USERNAME: str = ""

    # App
    ENV: str = "production"
    LOG_LEVEL: str = "INFO"

    @cached_property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @cached_property
    def sync_database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @cached_property
    def admin_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.ADMIN_IDS.split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out


settings = Settings()
