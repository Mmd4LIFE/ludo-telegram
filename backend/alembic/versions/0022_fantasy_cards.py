"""Direct Kick, All Stars, and Vertigo fantasy cards.

Revision ID: 0022_fantasy_cards
Revises: 0021_analytics_outbox
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_fantasy_cards"
down_revision = "0021_analytics_outbox"
branch_labels = None
depends_on = None

_SEED = [
    (
        "direct_kick",
        "Direct Kick",
        "🎯",
        "epic",
        "direct_kick",
        "live",
        "Tap an opponent's piece on the board and send it back to their yard.",
        17,
    ),
    (
        "all_stars",
        "All Stars",
        "✨",
        "epic",
        "all_stars",
        "live",
        "Every neutral star becomes yours and turns your colour. You are safe on all of them.",
        18,
    ),
    (
        "vertigo",
        "Vertigo",
        "🌀",
        "rare",
        "vertigo",
        "live",
        "Their next roll steps backward. A piece in the home column stays, and nothing steps back past its start.",
        19,
    ),
]


def upgrade() -> None:
    cards = sa.table(
        "cards",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("icon", sa.String),
        sa.column("rarity", sa.String),
        sa.column("effect", sa.String),
        sa.column("status", sa.String),
        sa.column("description", sa.String),
        sa.column("position", sa.Integer),
        sa.column("enabled", sa.Boolean),
    )
    op.bulk_insert(
        cards,
        [
            {
                "id": i,
                "name": n,
                "icon": ic,
                "rarity": r,
                "effect": e,
                "status": st,
                "description": d,
                "position": p,
                "enabled": True,
            }
            for (i, n, ic, r, e, st, d, p) in _SEED
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM cards WHERE id IN ('direct_kick', 'all_stars', 'vertigo')"
    )
