"""week_change table and playlist_version.note column

Just-this-week changes (engine.week_changes): one song of a generated
week, changed for that week only, kept with the week and made again
whenever it's generated. Deleting the week deletes its changes; a
deleted track leaves its swap with no track (dropped, with the reason,
at the next generation).

`playlist_version.note` says what made a version when it wasn't a
generation ("Just this week: song 12 swapped"). NULL for every version
made before this column, which were all generations or rollbacks.

`playlist_version.slots` is written compressed from this revision on
(engine.compressed_json: a JSON string "z1:<base64 zlib JSON>"). The
column's type is unchanged and rows written before stay as they are, so
upgrading rewrites nothing; downgrading unpacks every compressed row,
for code that reads slots as plain JSON.

Revision ID: 5b7c9e1f3a20
Revises: 3e1d2c8a9b47
Create Date: 2026-11-06 12:00:00.000000

"""
from __future__ import annotations

import base64
import json
import zlib
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
    # No index on playlist_id of its own: the unique constraint's index
    # starts with it.
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        batch_op.add_column(sa.Column('note', sa.Text(), nullable=True))


def _unpack_slots() -> None:
    """Rewrite every compressed `playlist_version.slots` as plain JSON.
    Self-contained (no engine imports), one row at a time."""
    conn = op.get_bind()
    pv = sa.table('playlist_version', sa.column('id', sa.Integer), sa.column('slots', sa.Text))
    ids = [r[0] for r in conn.execute(sa.select(pv.c.id)).fetchall()]
    for vid in ids:
        raw = conn.execute(sa.select(pv.c.slots).where(pv.c.id == vid)).scalar()
        if not raw:
            continue
        value = json.loads(raw)
        if isinstance(value, str) and value.startswith('z1:'):
            plain = zlib.decompress(base64.b64decode(value[3:])).decode('utf-8')
            conn.execute(pv.update().where(pv.c.id == vid).values(slots=plain))


def downgrade() -> None:
    """Downgrade schema."""
    _unpack_slots()
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        batch_op.drop_column('note')
    op.drop_table('week_change')
