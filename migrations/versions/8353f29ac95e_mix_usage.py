"""add mix_usage table for cross-week mix dedup (§14.25)

Powers the `mix` strategy's exclusion window. When generating week W
for a station, the strategy reads MixUsage rows from that station's
last N playlists (N = block.exclusion_window) and excludes those
track ids from the candidate set.

Index strategy: separate indexes on playlist_id and track_id mirror
the two query shapes (load-by-playlists, and any future "where was
this track used?" lookup). Composite index intentionally absent —
the working set is small (a few hundred rows per playlist) and the
single-column indexes cover the realistic queries.

Trimmed manually: alembic autogenerate also flagged adding indexes
to cursor_state / playlist / playlist_entry / playlist_trace /
source_playlist_entry — those indexes already exist (baseline created
them via `batch_op.create_index`, which autogen doesn't always
recognise as equivalent to inline definition). Reapplying them
would be a no-op in the best case and an "index already exists"
error in the worst.

Revision ID: 8353f29ac95e
Revises: 0f76cfb45e8d
Create Date: 2026-05-17 07:49:08.646041

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8353f29ac95e'
down_revision: Union[str, Sequence[str], None] = '0f76cfb45e8d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'mix_usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('playlist_id', sa.Integer(), nullable=False),
        sa.Column('track_id', sa.Integer(), nullable=False),
        sa.Column('genre', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['playlist_id'], ['playlist.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['track_id'], ['track.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mix_usage', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_mix_usage_playlist_id'), ['playlist_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_mix_usage_track_id'), ['track_id'], unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('mix_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mix_usage_track_id'))
        batch_op.drop_index(batch_op.f('ix_mix_usage_playlist_id'))
    op.drop_table('mix_usage')
