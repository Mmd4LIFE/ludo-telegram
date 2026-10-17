"""users.unlocked_skins for coin shop

Revision ID: 0014_unlocked_skins
Revises: 0013_economy_ledger
Create Date: 2026-10-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_unlocked_skins"
down_revision = "0013_economy_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "unlocked_skins",
            postgresql.JSONB(),
            nullable=False,
            server_default='["classic"]',
        ),
    )
    # Everyone who already has a non-classic skin equipped keeps it unlocked.
    op.execute(
        """
        UPDATE users
        SET unlocked_skins = unlocked_skins || jsonb_build_array(dice_skin)
        WHERE dice_skin IS NOT NULL AND dice_skin <> 'classic'
          AND NOT (unlocked_skins ? dice_skin)
        """
    )


def downgrade() -> None:
    op.drop_column("users", "unlocked_skins")
