"""balances + balance_history, economy reset all to 0, close waiting rooms

Revision ID: 0013_economy_ledger
Revises: 0012_scale_indexes
Create Date: 2026-10-17

Creates the coin ledger, backfills from users.coins, then zeros every human balance
(fresh economy start), and abandons all WAITING matches so the 60-minute join deadline
starts clean.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_economy_ledger"
down_revision = "0012_scale_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "balances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False, server_default="coins"),
        sa.Column("amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "currency", name="uq_balance_user_currency"),
    )
    op.create_index("ix_balances_user_id", "balances", ["user_id"])

    op.create_table(
        "balance_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("currency", sa.String(16), nullable=False, server_default="coins"),
        sa.Column("delta", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("ref_type", sa.String(32), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_balance_history_user_id", "balance_history", ["user_id"])
    op.create_index("ix_balance_history_reason", "balance_history", ["reason"])
    op.create_index("ix_balance_history_created_at", "balance_history", ["created_at"])

    # Backfill balances from users.coins + opening history
    op.execute(
        """
        INSERT INTO balances (user_id, currency, amount)
        SELECT id, 'coins', COALESCE(coins, 0) FROM users
        ON CONFLICT (user_id, currency) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO balance_history (user_id, currency, delta, balance_after, reason)
        SELECT id, 'coins', COALESCE(coins, 0), COALESCE(coins, 0), 'opening_balance'
        FROM users
        WHERE COALESCE(coins, 0) > 0 OR is_bot IS FALSE
        """
    )

    # Economy reset: set every human balance to 0 (fresh start)
    op.execute(
        """
        WITH targets AS (
          SELECT u.id AS user_id,
                 b.id AS balance_id,
                 b.amount AS before_amt
          FROM users u
          JOIN balances b ON b.user_id = u.id AND b.currency = 'coins'
          WHERE u.is_bot IS FALSE AND b.amount <> 0
        ),
        upd AS (
          UPDATE balances b
          SET amount = 0,
              updated_at = now()
          FROM targets t
          WHERE b.id = t.balance_id
          RETURNING t.user_id, t.before_amt
        )
        INSERT INTO balance_history (user_id, currency, delta, balance_after, reason, meta)
        SELECT user_id, 'coins', -before_amt, 0, 'economy_reset_zero',
               jsonb_build_object('before', before_amt)
        FROM upd
        """
    )
    op.execute(
        """
        UPDATE users u
        SET coins = 0
        FROM balances b
        WHERE b.user_id = u.id AND b.currency = 'coins' AND u.is_bot IS FALSE
        """
    )

    # Close all open waiting rooms
    op.execute(
        """
        UPDATE matches
        SET status = 'abandoned'
        WHERE status = 'waiting'
        """
    )


def downgrade() -> None:
    op.drop_table("balance_history")
    op.drop_table("balances")
