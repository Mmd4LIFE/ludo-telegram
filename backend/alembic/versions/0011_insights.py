"""admin insights assistant — query log + per-step flow log

Revision ID: 0011_insights
Revises: 0010_app_configs
Create Date: 2026-08-03

Two append-only observability tables:
  insights_queries — one row per admin question (the audit log)
  insights_steps   — one row per pipeline step (extract → resolve → route → execute →
                     compose), for process monitoring of each flow
Additive; read-only analytics feature.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_insights"
down_revision = "0010_app_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insights_queries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("question", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="error"),  # answered|clarified|refused|error
        sa.Column("answer", sa.String(2000), nullable=False, server_default=""),
        sa.Column("metric", sa.String(64), nullable=True),
        sa.Column("capability", sa.String(64), nullable=True),
        sa.Column("intent", postgresql.JSONB(), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("model", sa.String(48), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_insights_queries_admin_id", "insights_queries", ["admin_id"], unique=False)
    op.create_index("ix_insights_queries_created_at", "insights_queries", ["created_at"], unique=False)

    op.create_table(
        "insights_steps",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("query_id", sa.BigInteger(), sa.ForeignKey("insights_queries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage", sa.String(32), nullable=False),   # extract|resolve_time|resolve_entity|route|execute|compose
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),  # ok|skip|error|clarify
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_insights_steps_query_id", "insights_steps", ["query_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_insights_steps_query_id", table_name="insights_steps")
    op.drop_table("insights_steps")
    op.drop_index("ix_insights_queries_created_at", table_name="insights_queries")
    op.drop_index("ix_insights_queries_admin_id", table_name="insights_queries")
    op.drop_table("insights_queries")
