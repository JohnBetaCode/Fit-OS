"""Cardio details per set (level, speed, incline, steps, distance)

Revision ID: 9d4b2e7a1c55
Revises: 7c1e0b5d9a2f
Create Date: 2026-11-19

"""
import sqlalchemy as sa
from alembic import op

revision = '9d4b2e7a1c55'
down_revision = '7c1e0b5d9a2f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('workout_sets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('extra', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('workout_sets', schema=None) as batch_op:
        batch_op.drop_column('extra')
