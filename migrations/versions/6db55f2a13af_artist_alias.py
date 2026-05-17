"""add artist_alias table (§14.26)

Global (not station-scoped) — artist identity is a library-wide fact;
"Plastikman is Richie Hawtin" doesn't change between stations. See the
ArtistAlias model docstring for the per-station-scoping punt.

Trimmed manually: autogen also flagged adding indexes to cursor_state
/ playlist / playlist_entry / playlist_trace / source_playlist_entry —
same baseline-style mismatch artefact as §14.25's migration. Those
indexes already exist; reapplying would be a no-op at best and an
error at worst.

Revision ID: 6db55f2a13af
Revises: 8353f29ac95e
Create Date: 2026-05-17 11:47:09.762975

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6db55f2a13af'
down_revision: Union[str, Sequence[str], None] = '8353f29ac95e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'artist_alias',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alias_name', sa.Text(), nullable=False),
        sa.Column('canonical_name', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alias_name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('artist_alias')
