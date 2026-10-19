"""persistent room join requests and late-join seat metadata

Revision ID: 0020_persistent_room_join_requests
Revises: 0019_insights_rag
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_room_join_requests"
down_revision = "0019_insights_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("match_seats", sa.Column("joined_at_turn", sa.Integer(), nullable=True))
    op.create_table(
        "match_join_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "match_id",
            sa.Integer(),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "match_id", "user_id", name="uq_join_request_match_user"
        ),
    )
    op.create_index(
        "ix_match_join_requests_match_id", "match_join_requests", ["match_id"]
    )
    op.create_index(
        "ix_match_join_requests_user_id", "match_join_requests", ["user_id"]
    )
    op.create_index(
        "ix_match_join_requests_status", "match_join_requests", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_match_join_requests_status", table_name="match_join_requests")
    op.drop_index("ix_match_join_requests_user_id", table_name="match_join_requests")
    op.drop_index("ix_match_join_requests_match_id", table_name="match_join_requests")
    op.drop_table("match_join_requests")
    op.drop_column("match_seats", "joined_at_turn")
