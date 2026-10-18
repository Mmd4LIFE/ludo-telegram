"""Scrub faucet/admin history for user_id=31 only

Revision ID: 0018_scrub_faucet_history
Revises: 0017_wallet_code
Create Date: 2026-10-18

Removes signup / bot_start / admin_credit / admin_debit rows for the one
player who got the 12k faucet (+ mistaken admin top-up), rebuilds their
running balance_after, and resets their balance. Other users are untouched.
"""
from alembic import op

revision = "0018_scrub_faucet_history"
down_revision = "0017_wallet_code"
branch_labels = None
depends_on = None

TARGET_USER_ID = 31


def upgrade() -> None:
    uid = TARGET_USER_ID

    op.execute(
        f"""
        DELETE FROM balance_history
        WHERE user_id = {uid}
          AND reason IN ('signup', 'bot_start', 'admin_credit', 'admin_debit')
        """
    )

    op.execute(
        f"""
        WITH ordered AS (
            SELECT
                id,
                SUM(delta) OVER (
                    PARTITION BY user_id, currency
                    ORDER BY id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS running
            FROM balance_history
            WHERE user_id = {uid}
        )
        UPDATE balance_history AS h
        SET balance_after = ordered.running
        FROM ordered
        WHERE h.id = ordered.id
        """
    )

    op.execute(
        f"""
        UPDATE balances
        SET amount = COALESCE((
            SELECT SUM(h.delta)
            FROM balance_history AS h
            WHERE h.user_id = {uid} AND h.currency = balances.currency
        ), 0)
        WHERE user_id = {uid}
        """
    )

    op.execute(
        f"""
        UPDATE users
        SET coins = COALESCE((
            SELECT b.amount
            FROM balances AS b
            WHERE b.user_id = {uid} AND b.currency = 'coins'
        ), 0)
        WHERE id = {uid}
        """
    )


def downgrade() -> None:
    # Irreversible data scrub for a single user.
    pass
