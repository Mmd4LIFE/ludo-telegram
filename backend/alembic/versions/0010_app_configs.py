"""append-only app config store

Revision ID: 0010_app_configs
Revises: 0009_polls
Create Date: 2026-08-02

app_configs is deliberately INSERT-ONLY: a config change is a new row (never an update),
and the *current* value of a key is the newest row that isn't soft-deleted (deleted_at is
NULL). This keeps a full, auditable history of every setting change.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_app_configs"
down_revision = "0009_polls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_app_configs_key", "app_configs", ["key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_app_configs_key", table_name="app_configs")
    op.drop_table("app_configs")
