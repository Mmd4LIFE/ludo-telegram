"""in-chat polls: admin-managed instant templates + poll/option/vote tables

Revision ID: 0009_polls
Revises: 0008_cards_all_live
Create Date: 2026-08-02

Instant polls are created from an admin-managed template (seeded with "Should I knock
it?"). The polls/poll_options/poll_votes tables are the general structure that a later
"open poll" (user-authored) feature reuses. chat_messages gains a nullable poll_id so a
poll shows inline in the feed. Additive.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_polls"
down_revision = "0008_cards_all_live"
branch_labels = None
depends_on = None


def upgrade() -> None:
    templates = op.create_table(
        "poll_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question", sa.String(200), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default="[]"),
        # when the template may be sent: "knock" (a capture is available) or "any"
        sa.Column("trigger", sa.String(32), nullable=False, server_default="any"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.bulk_insert(
        templates,
        [{"question": "Should I knock it?", "options": ["Yes", "No"],
          "trigger": "knock", "enabled": True, "position": 0}],
    )

    op.create_table(
        "polls",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("poll_templates.id"), nullable=True),
        sa.Column("question", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="instant"),   # instant | open
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),     # open | closed
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_polls_match_id", "polls", ["match_id"], unique=False)

    op.create_table(
        "poll_options",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("poll_id", sa.BigInteger(), sa.ForeignKey("polls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_poll_options_poll_id", "poll_options", ["poll_id"], unique=False)

    op.create_table(
        "poll_votes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("poll_id", sa.BigInteger(), sa.ForeignKey("polls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("option_id", sa.BigInteger(), sa.ForeignKey("poll_options.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("poll_id", "user_id", name="uq_vote_per_user"),
    )
    op.create_index("ix_poll_votes_poll_id", "poll_votes", ["poll_id"], unique=False)

    op.add_column(
        "chat_messages",
        sa.Column("poll_id", sa.BigInteger(), sa.ForeignKey("polls.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "poll_id")
    op.drop_index("ix_poll_votes_poll_id", table_name="poll_votes")
    op.drop_table("poll_votes")
    op.drop_index("ix_poll_options_poll_id", table_name="poll_options")
    op.drop_table("poll_options")
    op.drop_index("ix_polls_match_id", table_name="polls")
    op.drop_table("polls")
    op.drop_table("poll_templates")
