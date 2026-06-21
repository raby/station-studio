"""add station.sidecar column (§16.2.5 D1)

§16.2.5 is the first slice with a sidecar write path that mutates
the SHAPE of the editable structure (section CRUD, structural block
edits). The §16.2.4 macro-edit spec-writeback (c1 review #6) wrote
back to the on-disk file directly; that doesn't scale to per-edit
mutation + breaks atomicity of (template + sidecar) writes since
they live in two stores.

This migration adds `Station.sidecar` as a nullable JSON column
mirroring `Station.template`. Reads in §16.2.5+ prefer the DB
column; the disk file `stations/{slug}.editor.json` becomes the
EXPORT artifact, symmetric to `stations/{slug}.json` for the
template (Decision A3 from §16.2.3 — DB-as-truth + on-demand disk
export).

§16.2.5.t10: this migration originally also ran a backfill loop
that copied `stations/{slug}.editor.json` into the new column for
every existing row. That loop was eliminated — the runtime
lifespan helper `engine.seeding.populate_db_sidecars` already
performs the same disk → DB-column copy on every server boot
(including the first boot post-upgrade), and carries the richer
`derive_sidecar(template)` fallback + per-station error logging
that the migration intentionally omitted. Holding both paths
created drift risk: a sanity check added to one wouldn't
propagate. Operators must restart the FastAPI process after
`alembic upgrade head` to pick up the new schema, so the lifespan
fires before any GET hits the column.

Revision ID: e695658879de
Revises: c7bb27708788
Create Date: 2026-06-06 17:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e695658879de'
down_revision: Union[str, Sequence[str], None] = 'c7bb27708788'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('station', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sidecar', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('station', schema=None) as batch_op:
        batch_op.drop_column('sidecar')
