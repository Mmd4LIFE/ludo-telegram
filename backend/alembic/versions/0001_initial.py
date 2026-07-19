"""initial schema: users, matches, match_seats

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("language_code", sa.String(8), nullable=True),
        sa.Column("coins", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("xp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("games_played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("games_won", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("bot_started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("referred_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    # Create the matches table; the matches.status column owns the match_status
    # enum and creates it exactly once. (An explicit standalone ENUM.create() plus a
    # column create_type=False proved fragile across SQLAlchemy versions — the column
    # still emitted CREATE TYPE, colliding with the manual create. Let the column own it.)
    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column(
            "status",
            sa.Enum("waiting", "playing", "finished", "abandoned", name="match_status"),
            nullable=False, server_default="waiting",
        ),
        sa.Column("max_players", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_bot_table", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("entry_fee", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("state", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_matches_code", "matches", ["code"], unique=True)
    op.create_index("ix_matches_status", "matches", ["status"], unique=False)

    op.create_table(
        "match_seats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seat_index", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(8), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("connected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("place", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("match_id", "seat_index", name="uq_seat_per_match"),
    )
    op.create_index("ix_match_seats_match_id", "match_seats", ["match_id"], unique=False)


def downgrade() -> None:
    op.drop_table("match_seats")
    op.drop_index("ix_matches_status", table_name="matches")
    op.drop_index("ix_matches_code", table_name="matches")
    op.drop_table("matches")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")
    postgresql.ENUM(name="match_status").drop(op.get_bind(), checkfirst=True)
