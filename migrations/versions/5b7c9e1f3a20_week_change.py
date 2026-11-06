"""week_change table and playlist_version.note column

Just-this-week changes (engine.week_changes): one song of a generated
week, changed for that week only, kept with the week and made again
whenever it's generated. Deleting the week deletes its changes; a
deleted track leaves its swap with no track (dropped, with the reason,
at the next generation).

`playlist_version.note` says what made a version when it wasn't a
generation ("Just this week: song 12 swapped"). NULL for every version
made before this column, which were all generations or rollbacks.

Revision ID: 5b7c9e1f3a20
Revises: 3e1d2c8a9b47
Create Date: 2026-11-06 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5b7c9e1f3a20'
down_revision: Union[str, Sequence[str], None] = '3e1d2c8a9b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'week_change',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('playlist_id', sa.Integer(), nullable=False),
        sa.Column('row', sa.Integer(), nullable=False),
        sa.Column('block', sa.Integer(), nullable=False),
        sa.Column('k', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('track_id', sa.Integer(), nullable=True),
        sa.Column('rule', sa.JSON(), nullable=True),
        sa.Column('rule_strategy', sa.Text(), nullable=False),
        sa.Column('rule_source', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['playlist_id'], ['playlist.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['track_id'], ['track.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'playlist_id', 'row', 'block', 'k',
            name='uq_week_change_playlist_id_row_block_k',
        ),
    )
    with op.batch_alter_table('week_change', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_week_change_playlist_id'), ['playlist_id'], unique=False,
        )
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        batch_op.add_column(sa.Column('note', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        batch_op.drop_column('note')
    with op.batch_alter_table('week_change', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_week_change_playlist_id'))
    op.drop_table('week_change')
