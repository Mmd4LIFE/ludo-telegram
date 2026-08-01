"""admin-managed set of chat reaction emojis

Revision ID: 0005_reaction_emojis
Revises: 0004_chat_softdelete_reactions
Create Date: 2026-08-01

Creates reaction_emojis (the allowed reactions, editable by admins) and seeds it with the
four that were previously hard-coded. Additive; existing message_reactions are untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_reaction_emojis"
down_revision = "0004_chat_softdelete_reactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tbl = op.create_table(
        "reaction_emojis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("emoji", sa.String(16), nullable=False, unique=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.bulk_insert(
        tbl,
        [
            {"emoji": "👍", "position": 0},
            {"emoji": "❤️", "position": 1},
            {"emoji": "😂", "position": 2},
            {"emoji": "🔥", "position": 3},
        ],
    )


def downgrade() -> None:
    op.drop_table("reaction_emojis")
