"""transfers + star_payments for wallet gifts and Stars shop

Revision ID: 0016_wallet_transfers_stars
Revises: 0015_abandon_active_rooms
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_wallet_transfers_stars"
down_revision = "0015_abandon_active_rooms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("from_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("claim_token", sa.String(32), nullable=True),
        sa.Column("note", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_transfers_from_user_id", "transfers", ["from_user_id"])
    op.create_index("ix_transfers_to_user_id", "transfers", ["to_user_id"])
    op.create_index("ix_transfers_status", "transfers", ["status"])
    op.create_index("ix_transfers_claim_token", "transfers", ["claim_token"], unique=True)

    op.create_table(
        "star_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("coins", sa.BigInteger(), nullable=False),
        sa.Column("pack_stars", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("invoice_payload", sa.String(64), nullable=False),
        sa.Column("telegram_payment_charge_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_star_payments_user_id", "star_payments", ["user_id"])
    op.create_index("ix_star_payments_status", "star_payments", ["status"])
    op.create_index(
        "ix_star_payments_invoice_payload", "star_payments", ["invoice_payload"], unique=True
    )
    op.create_index(
        "ix_star_payments_charge_id",
        "star_payments",
        ["telegram_payment_charge_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("star_payments")
    op.drop_table("transfers")
