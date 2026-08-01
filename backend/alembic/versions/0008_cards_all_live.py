"""all fantasy-card effects are now wired — flip status to live + refine descriptions

Revision ID: 0008_cards_all_live
Revises: 0007_card_draws
Create Date: 2026-08-01

Phase 2–4 effects are implemented (with auto-targeting), so every card is now "live".
Descriptions are refined to match the shipped behaviour. Data-only; no schema change.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_cards_all_live"
down_revision = "0007_card_draws"
branch_labels = None
depends_on = None


# id -> refined description (auto-targeted where a rival/token is involved)
_DESC = {
    "extra_roll": "Roll again right now — one bonus turn.",
    "active_stars": "Light up your colour's stars — you're safe on them for the rest of the game.",
    "shield_one": "Shield your tokens from capture for 3 rounds.",
    "shield_all": "Shield your tokens from capture for 5 rounds.",
    "double_dice": "Your next 2 rolls move double.",
    "lock_one": "Freeze the leading rival — they skip their next turn.",
    "lock_two": "Freeze the leading rival — they skip their next 2 turns.",
    "swap": "Swap your lead token with the leading rival's on the track.",
    "teleport": "Warp your lead token forward to the next star.",
    "recall": "Send the leading rival's lead token back 4 steps.",
    "boost": "Push your lead token forward 3 extra steps.",
    "summon": "Free one token from base without rolling a six.",
    "steal_turn": "Jump the queue and take a turn right now.",
    "second_chance": "The next time one of your tokens would be knocked home, it isn't.",
    "toll": "Your neutral star blocks rivals from landing on it for a round.",
    "mirror": "Replay the last card an opponent played.",
    "jackpot": "Pocket 150 bonus coins.",
}


def upgrade() -> None:
    cards = sa.table(
        "cards",
        sa.column("id", sa.String),
        sa.column("status", sa.String),
        sa.column("description", sa.String),
    )
    op.execute(cards.update().values(status="live"))
    for cid, desc in _DESC.items():
        op.execute(cards.update().where(cards.c.id == cid).values(description=desc))


def downgrade() -> None:
    cards = sa.table("cards", sa.column("id", sa.String), sa.column("status", sa.String))
    # restore the two that were live at 0007; the rest were "soon"
    op.execute(cards.update().values(status="soon"))
    op.execute(cards.update().where(cards.c.id.in_(["extra_roll", "active_stars"])).values(status="live"))
