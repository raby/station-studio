"""add station.sidecar_error column (§16.2.5.t7)

The §16.2.5.t9 + .t7 paired slice replaces the in-route disk-fallback
warning banner with a persisted `Station.sidecar_error` column.

Pre-§16.2.5.t9, `_resolve_sidecar` had a three-tier fallback chain
(DB column → on-disk file → trivial). The Case 2 disk branch surfaced
a `sidecar_error` string in the GET response when the file failed
parse/validation. That branch was structurally dead in production
(the lifespan `populate_db_sidecars` writes the DB column on every
boot — by the first request `Station.sidecar` is always non-NULL),
but tests staged the path with `STATION_STUDIO_SKIP_AUTO_SEED=1`.

The replacement (§16.2.5.t7): when `populate_db_sidecars` falls back
from a broken disk sidecar to `derive_sidecar(template)`, it persists
the failure reason into this column. The runtime read path surfaces
that reason in the existing `EditorSidecarResponse.sidecar_error`
field — same UX contract as before, but the value is now
authoritatively persisted (no transient log-only signal).

A successful disk-load clears the column (NULL), so a re-restart after
the operator fixes the file naturally removes the stale banner.

Revision ID: 08c84df31980
Revises: e695658879de
Create Date: 2026-06-21 16:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '08c84df31980'
down_revision: Union[str, Sequence[str], None] = 'e695658879de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('station', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('sidecar_error', sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('station', schema=None) as batch_op:
        batch_op.drop_column('sidecar_error')
