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

    # OpenAI (admin-only AI chat panel). Key lives ONLY in the server env / .env — it is
    # never sent to the client; all calls are proxied by the backend.
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    # Insights assistant: chat model (falls back to OPENAI_MODEL) + embedding model for RAG
    OPENAI_INSIGHTS_MODEL: str = ""
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    # anchor for "today"/"this week" in the Insights assistant (minutes offset from UTC)
    INSIGHTS_TZ_OFFSET_MINUTES: int = 0
    # Read-only Postgres role used by Insights + the native SQL tab. Created/synced at boot;
    # the password is derived from SECRET_KEY when left empty.
    INSIGHTS_RO_ROLE: str = "ludo_ro"
    INSIGHTS_RO_PASSWORD: str = ""
    INSIGHTS_SQL_TIMEOUT_MS: int = 15000
    INSIGHTS_MAX_ROWS: int = 1000

    # Database
    POSTGRES_USER: str = "ludo"
    POSTGRES_PASSWORD: str = "ludo_secret"
    POSTGRES_DB: str = "ludo"
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    # AsyncPG pool — keep small on a shared 1GB box (tunable via env).
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_RECYCLE: int = 1800
    DB_POOL_TIMEOUT: int = 10

    # Live-match scale caps
    MAX_VIEWERS_PER_MATCH: int = 16
    ACTION_QUEUE_MAX: int = 64
    PERSIST_EVERY_N_STEPS: int = 5
    # How often to persist User.last_seen_at (seconds); off the request session.
    PRESENCE_TOUCH_SECONDS: int = 60
    # Per-socket send timeout during WS fanout (seconds).
    WS_SEND_TIMEOUT_SECONDS: float = 2.0

    # Economy (coins are a soft currency; identical faucet/sink model to poker)
    # New accounts start at 0 — coins come from play, Stars shop, gifts, or admin.
    SIGNUP_BONUS_COINS: int = 0
    DAILY_REWARD_COINS: int = 0
    BOT_START_BONUS: int = 0

    # Matches / rooms
    MAX_ACTIVE_ROOMS_PER_USER: int = 3
    ROOM_IDLE_CLOSE_HOURS: float = 1.0

    # Gameplay
    TURN_TIMEOUT_SECONDS: int = 20          # a player has this long to act, else auto-play
    MAX_MISSED_TURNS: int = 2               # miss this many rolls in a row → auto-kicked
    BOT_THINK_MIN: float = 1.2
    BOT_THINK_MAX: float = 2.4
    ROLL_REVEAL_SECONDS: float = 1.0        # hold on the rolled die so everyone sees it
    NO_MOVE_SECONDS: float = 1.2            # pause to show "no legal moves" before passing
    MOVE_SETTLE_SECONDS: float = 0.45       # let a token's glide finish before the next act
    CARD_PICK_SECONDS: int = 20             # a player has this long to pick a fantasy card
    CARD_REVEAL_SECONDS: float = 3.5        # hold on the flipped cards after a pick
    CARD_RESULT_DELAY: float = 0.6          # beat before a card's effect plays out on the board
    COULDVE_PER_CARD: int = 3               # pass up this many knocks in a game → a card draw
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
