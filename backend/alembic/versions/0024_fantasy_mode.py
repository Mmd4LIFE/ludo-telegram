"""Fantasy Arena mode: match mode, champions, card costs, card plays, fantasy record.

Revision ID: 0024_fantasy_mode
Revises: 0023_leveling

Adds the second game mode. Classic rooms get ``mode='classic'`` and play exactly as
before. Cards gain a mana ``cost`` (defaulted by rarity) and a ``fantasy_only`` flag so
arena-only cards never show up in a classic draw. Every cast is logged in ``card_plays``.
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_fantasy_mode"
down_revision = "0023_leveling"
branch_labels = None
depends_on = None

_COST_BY_RARITY = {"common": 1, "uncommon": 2, "rare": 3, "epic": 4}

# Arena-only cards. ``effect`` names are handled by app.game.fantasy.
_SEED = [
    (
        "mana_surge",
        "Mana Surge",
        "🔮",
        "common",
        "mana_surge",
        "live",
        "Gain 3 mana at once.",
        20,
    ),
    (
        "pickpocket",
        "Pickpocket",
        "🪄",
        "uncommon",
        "pickpocket",
        "live",
        "Take a random card from a rival's hand.",
        21,
    ),
    (
        "hex",
        "Hex",
        "🧿",
        "rare",
        "hex",
        "live",
        "A rival's next roll is capped at 3.",
        22,
    ),
    (
        "time_warp",
        "Time Warp",
        "⏳",
        "epic",
        "time_warp",
        "live",
        "Take two turns in a row.",
        23,
    ),
]


def upgrade() -> None:
    op.add_column(
        "matches",
        sa.Column("mode", sa.String(16), nullable=False, server_default="classic"),
    )
    op.create_index("ix_matches_mode", "matches", ["mode"])

    op.add_column("match_seats", sa.Column("champion", sa.String(16), nullable=True))

    op.add_column(
        "cards", sa.Column("cost", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "cards",
        sa.Column("fantasy_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    conn = op.get_bind()
    for rarity, cost in _COST_BY_RARITY.items():
        conn.execute(
            sa.text("UPDATE cards SET cost = :cost WHERE rarity = :rarity"),
            {"cost": cost, "rarity": rarity},
        )

    op.create_table(
        "card_plays",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "match_id", sa.Integer(),
            sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("seat", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.String(32), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_seat", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_card_plays_match_id", "card_plays", ["match_id"])
    op.create_index("ix_card_plays_user_id", "card_plays", ["user_id"])

    op.add_column(
        "users",
        sa.Column("fantasy_played", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "users",
        sa.Column("fantasy_wins", sa.Integer(), nullable=False, server_default="0"),
    )

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
        sa.column("cost", sa.Integer),
        sa.column("fantasy_only", sa.Boolean),
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
                "cost": _COST_BY_RARITY[r],
                "fantasy_only": True,
            }
            for (i, n, ic, r, e, st, d, p) in _SEED
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM cards WHERE id IN ('mana_surge', 'pickpocket', 'hex', 'time_warp')"
    )
    op.drop_column("users", "fantasy_wins")
    op.drop_column("users", "fantasy_played")
    op.drop_index("ix_card_plays_user_id", table_name="card_plays")
    op.drop_index("ix_card_plays_match_id", table_name="card_plays")
    op.drop_table("card_plays")
    op.drop_column("cards", "fantasy_only")
    op.drop_column("cards", "cost")
    op.drop_column("match_seats", "champion")
    op.drop_index("ix_matches_mode", table_name="matches")
    op.drop_column("matches", "mode")
