"""XP events, level rewards, and levels recomputed from existing XP.

Revision ID: 0023_leveling
Revises: 0022_fantasy_cards

Existing players move to the level their XP has earned. No coins are paid for those
past levels: they are recorded in level_rewards with coins=0 so they never pay later.
"""
from alembic import op
import sqlalchemy as sa

from app.services.leveling import level_for_xp

revision = "0023_leveling"
down_revision = "0022_fantasy_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "xp_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("ref", sa.String(48), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "ref", name="uq_xp_events_user_ref"),
    )
    op.create_index("ix_xp_events_user_created", "xp_events", ["user_id", "created_at"])
    op.create_index("ix_xp_events_match_id", "xp_events", ["match_id"])

    op.create_table(
        "level_rewards",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("coins", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "level", name="uq_level_rewards_user_level"),
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, xp FROM users WHERE xp > 0")).all()
    for uid, xp in rows:
        level = level_for_xp(xp)
        conn.execute(
            sa.text("UPDATE users SET level = :lvl WHERE id = :uid"),
            {"lvl": level, "uid": uid},
        )
        for n in range(2, level + 1):
            conn.execute(
                sa.text(
                    "INSERT INTO level_rewards (user_id, level, coins) VALUES (:uid, :n, 0)"
                ),
                {"uid": uid, "n": n},
            )


def downgrade() -> None:
    op.drop_table("level_rewards")
    op.drop_index("ix_xp_events_match_id", table_name="xp_events")
    op.drop_index("ix_xp_events_user_created", table_name="xp_events")
    op.drop_table("xp_events")
