"""add playlist_version.cursors column

Records the persistent cursor positions each generation started from,
so a past week can be regenerated from where its rules were then
instead of from the station's live cursors. Nullable: stateless
templates have none, and versions made before this column existed
weren't recorded.

Revision ID: 3e1d2c8a9b47
Revises: be9b9713c5a4
Create Date: 2026-10-30 17:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3e1d2c8a9b47'
down_revision: Union[str, Sequence[str], None] = 'be9b9713c5a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        # NULL = not recorded (stateless template, or made before 1.12).
        batch_op.add_column(sa.Column('cursors', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema — drop the column."""
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        batch_op.drop_column('cursors')
