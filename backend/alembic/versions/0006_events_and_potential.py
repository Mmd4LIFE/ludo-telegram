"""per-roll + per-knock event logs, and an all-time potential-knock counter

Revision ID: 0006_events_and_potential
Revises: 0005_reaction_emojis
Create Date: 2026-08-01

Two append-only event tables (humans only) plus users.potential_knocks. A "knockout" row
records either an actual capture (taken=true) or a passed-up one — a capture that was
legal but not played (taken=false, a "potential knock"). Additive; nothing existing moves.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_events_and_potential"
down_revision = "0005_reaction_emojis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("potential_knocks", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "dice_rolls",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("seat", sa.Integer(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_dice_rolls_match_id", "dice_rolls", ["match_id"], unique=False)
    op.create_index("ix_dice_rolls_user_id", "dice_rolls", ["user_id"], unique=False)

    op.create_table(
        "knockouts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attacker_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("attacker_seat", sa.Integer(), nullable=False),
        sa.Column("victim_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("victim_seat", sa.Integer(), nullable=False),
        # true = a capture actually made; false = a legal capture that was passed up
        sa.Column("taken", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_knockouts_match_id", "knockouts", ["match_id"], unique=False)
    op.create_index("ix_knockouts_attacker_user_id", "knockouts", ["attacker_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_knockouts_attacker_user_id", table_name="knockouts")
    op.drop_index("ix_knockouts_match_id", table_name="knockouts")
    op.drop_table("knockouts")
    op.drop_index("ix_dice_rolls_user_id", table_name="dice_rolls")
    op.drop_index("ix_dice_rolls_match_id", table_name="dice_rolls")
    op.drop_table("dice_rolls")
    op.drop_column("users", "potential_knocks")
