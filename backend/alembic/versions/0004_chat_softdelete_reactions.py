"""soft-delete chat messages + message reactions

Revision ID: 0004_chat_softdelete_reactions
Revises: 0003_stats_and_chat
Create Date: 2026-08-01

Additive: chat_messages gains a nullable deleted_at (deletes become soft), and a new
message_reactions table holds one reaction per (message, user). No existing data touched.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_chat_softdelete_reactions"
down_revision = "0003_stats_and_chat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "message_reactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("emoji", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("message_id", "user_id", name="uq_reaction_per_user"),
    )
    op.create_index(
        "ix_message_reactions_message_id", "message_reactions", ["message_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_message_reactions_message_id", table_name="message_reactions")
    op.drop_table("message_reactions")
    op.drop_column("chat_messages", "deleted_at")
