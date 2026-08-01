"""fantasy cards catalog + card-draw log

Revision ID: 0007_card_draws
Revises: 0006_events_and_potential
Create Date: 2026-08-01

Creates the ``cards`` catalog (seeded here — the definitions live in the DB, not in code)
and ``card_draws`` (one row per draw: the four options offered and the pick). Additive.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_card_draws"
down_revision = "0006_events_and_potential"
branch_labels = None
depends_on = None


# The starter catalog. Effects marked status="live" change play now; "soon" ones are
# drawable but inert (roadmap: docs/prd/fantasy-cards.md). Admins/migrations own this list.
_SEED = [
    ("extra_roll", "Encore", "🎲", "common", "extra_roll", "live", "Roll again right now — one bonus turn.", 0),
    ("active_stars", "Starfall", "⭐", "rare", "active_stars", "live", "Light up your colour's stars — you're safe on them for the rest of the game.", 1),
    ("shield_one", "Aegis", "🛡️", "uncommon", "shield", "soon", "Shield one of your tokens from capture for 3 rounds.", 2),
    ("shield_all", "Bulwark", "🏰", "epic", "shield_all", "soon", "Shield ALL your tokens from capture for 2 rounds.", 3),
    ("double_dice", "Twin Dice", "🎯", "uncommon", "double_dice", "soon", "Your next 2 rolls count double.", 4),
    ("lock_one", "Freeze", "🧊", "rare", "lock", "soon", "Freeze a rival — they skip their next roll.", 5),
    ("lock_two", "Deep Freeze", "❄️", "epic", "lock2", "soon", "Freeze a rival for their next 2 turns.", 6),
    ("swap", "Switcheroo", "🔄", "epic", "swap", "soon", "Swap one of your tokens with a rival's on the track.", 7),
    ("teleport", "Warp", "🌀", "rare", "teleport", "soon", "Warp one token forward to your nearest star.", 8),
    ("recall", "Recall", "🪃", "uncommon", "recall", "soon", "Send a rival's leading token back a few steps.", 9),
    ("boost", "Sprint", "⚡", "common", "boost", "soon", "Push one of your tokens forward 3 extra steps.", 10),
    ("summon", "Rally", "🚀", "uncommon", "summon", "soon", "Free one token from base without rolling a six.", 11),
    ("steal_turn", "Usurp", "👑", "epic", "steal_turn", "soon", "Take the next player's turn before they do.", 12),
    ("second_chance", "Second Wind", "🍀", "rare", "second_chance", "soon", "The next time a token of yours is knocked home, it isn't.", 13),
    ("toll", "Toll Gate", "⛩️", "uncommon", "toll", "soon", "Your neutral star blocks rivals — they can't pass it for 1 round.", 14),
    ("mirror", "Mirror", "🪞", "rare", "mirror", "soon", "Copy the last card any opponent played.", 15),
    ("jackpot", "Jackpot", "💰", "common", "coins", "soon", "Pocket a handful of bonus coins.", 16),
]


def upgrade() -> None:
    cards = op.create_table(
        "cards",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(48), nullable=False),
        sa.Column("icon", sa.String(16), nullable=False, server_default=""),
        sa.Column("rarity", sa.String(16), nullable=False, server_default="common"),
        sa.Column("effect", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="soon"),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.bulk_insert(
        cards,
        [
            {"id": i, "name": n, "icon": ic, "rarity": r, "effect": e,
             "status": st, "description": d, "position": p, "enabled": True}
            for (i, n, ic, r, e, st, d, p) in _SEED
        ],
    )

    op.create_table(
        "card_draws",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("seat", sa.Integer(), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("picked", sa.String(32), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_card_draws_match_id", "card_draws", ["match_id"], unique=False)
    op.create_index("ix_card_draws_user_id", "card_draws", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_card_draws_user_id", table_name="card_draws")
    op.drop_index("ix_card_draws_match_id", table_name="card_draws")
    op.drop_table("card_draws")
    op.drop_table("cards")
