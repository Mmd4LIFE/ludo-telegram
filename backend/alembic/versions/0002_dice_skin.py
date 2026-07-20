"""add users.dice_skin

Revision ID: 0002_dice_skin
Revises: 0001_initial
Create Date: 2026-07-20

Additive and idempotent: every player gets a dice skin, defaulting to the classic
white die, so existing rows stay valid without a backfill pass.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_dice_skin"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "dice_skin",
            sa.String(16),
            nullable=False,
            server_default="classic",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "dice_skin")
