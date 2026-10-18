"""Abandon all active (waiting/playing) rooms

Revision ID: 0015_abandon_active_rooms
Revises: 0014_unlocked_skins
Create Date: 2026-10-18

Closes every open lobby and in-progress match (including bot-filled rooms) so
they no longer appear as active. Finished rooms are left untouched.
"""
from alembic import op

revision = "0015_abandon_active_rooms"
down_revision = "0014_unlocked_skins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE matches
        SET status = 'abandoned'
        WHERE status IN ('waiting', 'playing')
        """
    )


def downgrade() -> None:
    # Irreversible data cleanup — cannot restore prior waiting/playing statuses.
    pass
