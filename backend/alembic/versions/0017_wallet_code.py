"""Add public wallet_code UUID to users

Revision ID: 0017_wallet_code
Revises: 0016_wallet_transfers_stars
Create Date: 2026-10-18

Opaque public wallet identifier for transfers — never expose sequential user ids.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_wallet_code"
down_revision = "0016_wallet_transfers_stars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("wallet_code", sa.String(36), nullable=True),
    )
    # Backfill existing rows with unique UUIDs
    op.execute(
        """
        UPDATE users
        SET wallet_code = gen_random_uuid()::text
        WHERE wallet_code IS NULL
        """
    )
    op.alter_column("users", "wallet_code", nullable=False)
    op.create_index("ix_users_wallet_code", "users", ["wallet_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_wallet_code", table_name="users")
    op.drop_column("users", "wallet_code")
