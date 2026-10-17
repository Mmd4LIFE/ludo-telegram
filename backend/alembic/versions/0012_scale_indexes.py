"""scale indexes for lobby, seats, bots, chat, users

Revision ID: 0012_scale_indexes
Revises: 0011_insights
Create Date: 2026-10-17

Additive indexes only — no data changes. Speeds lobby list, janitor bot-table scans,
per-user seat lookups, chat feed, and live-user filters under growth.
"""
from alembic import op

revision = "0012_scale_indexes"
down_revision = "0011_insights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_match_seats_user_id", "match_seats", ["user_id"])

    # Hot lobby filter: public waiting rooms ordered by created_at
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_matches_lobby_open
        ON matches (created_at DESC)
        WHERE is_public IS TRUE AND status = 'waiting'
        """
    )

    # Janitor: live / finished bot tables
    op.create_index(
        "ix_matches_bot_table_status",
        "matches",
        ["is_bot_table", "status"],
    )

    # Chat feed: recent non-deleted messages per match
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chat_messages_match_live
        ON chat_messages (match_id, id DESC)
        WHERE deleted_at IS NULL
        """
    )

    # Scoreboard / insights filters on real players
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_users_live
        ON users (id)
        WHERE is_bot IS FALSE
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_live")
    op.execute("DROP INDEX IF EXISTS ix_chat_messages_match_live")
    op.drop_index("ix_matches_bot_table_status", table_name="matches")
    op.execute("DROP INDEX IF EXISTS ix_matches_lobby_open")
    op.drop_index("ix_match_seats_user_id", table_name="match_seats")
