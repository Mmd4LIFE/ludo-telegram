"""outbox for batched ClickHouse score copies

Revision ID: 0021_analytics_outbox
Revises: 0020_room_join_requests
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021_analytics_outbox"
down_revision = "0020_room_join_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("kind", "source_id", name="uq_analytics_outbox_source"),
    )
    op.create_index(
        "ix_analytics_outbox_due",
        "analytics_outbox",
        ["next_attempt_at", "id"],
    )
    op.create_table(
        "analytics_cursors",
        sa.Column("stream", sa.String(16), primary_key=True),
        sa.Column("last_id", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("analytics_cursors")
    op.drop_index("ix_analytics_outbox_due", table_name="analytics_outbox")
    op.drop_table("analytics_outbox")
