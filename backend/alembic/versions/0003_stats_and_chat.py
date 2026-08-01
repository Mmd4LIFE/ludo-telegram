"""player stats (dice histogram, captures) + persistent chat_messages

Revision ID: 0003_stats_and_chat
Revises: 0002_dice_skin
Create Date: 2026-07-25

Additive and idempotent: new user columns default to an empty histogram / zero, and the
chat table is created fresh. No existing data is touched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_stats_and_chat"
down_revision = "0002_dice_skin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("dice_hist", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "users",
        sa.Column("captures_dealt", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("captures_taken", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("text", sa.String(400), nullable=False, server_default=""),
        sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reply_to", sa.Integer(), nullable=True),
        sa.Column("reply_name", sa.String(128), nullable=True),
        sa.Column("reply_text", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_match_id", "chat_messages", ["match_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_messages_match_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_column("users", "captures_taken")
    op.drop_column("users", "captures_dealt")
    op.drop_column("users", "dice_hist")
