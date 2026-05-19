"""add playlist_entry.slot_type column (§14.31)

Per-entry strategy tag (window/pick/sequential/jingle/mix/weighted/
prev_ref). Denormalises the trace's slot.strategy onto the entry so
queries like "show me all mix entries from week 12" don't need a
JOIN with playlist_trace.slots JSON.

Nullable so existing rows remain valid pre-backfill; new rows always
populate via save_playlist. scripts/backfill_slot_types.py populates
historical entries from their persisted traces.

Trimmed manually: alembic autogen also flagged adding indexes to
cursor_state / playlist / playlist_entry / playlist_trace /
source_playlist_entry — same baseline-style mismatch artefact as
prior §14.x migrations. Those indexes already exist.

Revision ID: 1cafcc971152
Revises: 54570900ed97
Create Date: 2026-05-19 13:07:55.894043

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1cafcc971152'
down_revision: Union[str, Sequence[str], None] = '54570900ed97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('playlist_entry', schema=None) as batch_op:
        batch_op.add_column(sa.Column('slot_type', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('playlist_entry', schema=None) as batch_op:
        batch_op.drop_column('slot_type')
