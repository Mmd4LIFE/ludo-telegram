"""Insights overhaul: knowledge store (RAG), native SQL audit, richer query log

Revision ID: 0019_insights_rag
Revises: 0018_scrub_faucet_history
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019_insights_rag"
down_revision = "0018_scrub_faucet_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insights_knowledge",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        sa.Column("embed_model", sa.String(64), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_insights_knowledge_key", "insights_knowledge", ["key"], unique=True)

    op.create_table(
        "insights_sql_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_insights_sql_runs_admin_id", "insights_sql_runs", ["admin_id"])
    op.create_index("ix_insights_sql_runs_created_at", "insights_sql_runs", ["created_at"])

    op.add_column("insights_queries", sa.Column("sql", sa.Text(), nullable=True))
    op.add_column("insights_queries", sa.Column("feedback", sa.SmallInteger(), nullable=True))
    op.alter_column("insights_queries", "answer", type_=sa.Text(), existing_type=sa.String(2000))


def downgrade() -> None:
    op.alter_column("insights_queries", "answer", type_=sa.String(2000), existing_type=sa.Text())
    op.drop_column("insights_queries", "feedback")
    op.drop_column("insights_queries", "sql")
    op.drop_index("ix_insights_sql_runs_created_at", table_name="insights_sql_runs")
    op.drop_index("ix_insights_sql_runs_admin_id", table_name="insights_sql_runs")
    op.drop_table("insights_sql_runs")
    op.drop_index("ix_insights_knowledge_key", table_name="insights_knowledge")
    op.drop_table("insights_knowledge")
