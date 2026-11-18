"""Mark profiles that already trained as onboarded, so the welcome flow only greets new people

Revision ID: 7c1e0b5d9a2f
Revises: 022a583cac6c
Create Date: 2026-11-17

"""
from alembic import op

revision = '7c1e0b5d9a2f'
down_revision = '022a583cac6c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE profile SET onboarded = 1 WHERE EXISTS (SELECT 1 FROM workout_sessions)")


def downgrade() -> None:
    pass  # data only: leaving the flag set is harmless
