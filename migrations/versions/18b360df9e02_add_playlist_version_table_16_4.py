"""add playlist_version table (§16.4 trace versioning v1)

Pre-§16.4, `playlist_trace` was a single-row-per-Playlist record of
the generated trace, and the regenerate flow hard-deleted the row via
the Playlist cascade — making the prior trace unrecoverable.

§16.4 introduces multi-row trace history via a new
`playlist_version` table keyed on (playlist_id, version_no). The
regenerate flow now APPENDS a new version (version_no = max + 1)
instead of replacing. The current version is `max(version_no)`;
`Playlist.trace_id` keeps its column as a denormalized pointer to
the current version's trace_id (auto-updated by save_playlist on
each regen).

Migration shape:

  1. Create `playlist_version` table with columns mirroring the
     prior `playlist_trace` shape PLUS `version_no`. UNIQUE
     (playlist_id, version_no); UNIQUE trace_id; FK to playlist
     with ON DELETE CASCADE.
  2. Backfill: every existing playlist_trace row → version_no=1.
  3. Drop `playlist_trace`.

Symmetric downgrade rebuilds `playlist_trace` from the latest
version per playlist (MAX(version_no)) and drops `playlist_version`.

Decisions from the design pass (DESIGN.md §16.4 status block):

* Drop PlaylistTrace entirely — cleaner than keeping a wrapper view.
* PlaylistEntry stays latest-only (TraceSlot already carries
  track_id / name / artist; the viewer renders historical versions
  from slots JSON directly).
* Retention: keep all versions forever; no cap in v1.

Revision ID: 18b360df9e02
Revises: 08c84df31980
Create Date: 2026-06-21 22:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '18b360df9e02'
down_revision: Union[str, Sequence[str], None] = '08c84df31980'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create the new versioned table.
    op.create_table(
        'playlist_version',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('playlist_id', sa.Integer(), nullable=False),
        sa.Column('version_no', sa.Integer(), nullable=False),
        sa.Column('trace_id', sa.Text(), nullable=False),
        sa.Column('station_slug', sa.Text(), nullable=False),
        sa.Column('week', sa.Integer(), nullable=False),
        sa.Column('rng', sa.Text(), nullable=False),
        sa.Column('seed', sa.Integer(), nullable=True),
        sa.Column('generated_at', sa.Text(), nullable=False),
        sa.Column('slots', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ['playlist_id'], ['playlist.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trace_id', name='uq_playlist_version_trace_id'),
        sa.UniqueConstraint(
            'playlist_id', 'version_no',
            name='uq_playlist_version_playlist_id_version_no',
        ),
    )
    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_playlist_version_playlist_id'),
            ['playlist_id'], unique=False,
        )

    # 2. Backfill: each existing playlist_trace → version_no=1.
    # Raw SQL on purpose — avoids importing the model layer mid-
    # migration (the ORM mapping mid-flight would point at the new
    # PlaylistVersion class while the old playlist_trace table still
    # exists; ad-hoc Table references keep the migration self-
    # contained, matching the pattern used by the §14.x migrations).
    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO playlist_version (
            playlist_id, version_no, trace_id, station_slug, week,
            rng, seed, generated_at, slots
        )
        SELECT
            playlist_id, 1, trace_id, station_slug, week,
            rng, seed, generated_at, slots
        FROM playlist_trace
    """))

    # 3. Drop the old single-version table.
    with op.batch_alter_table('playlist_trace', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_playlist_trace_playlist_id'))
    op.drop_table('playlist_trace')


def downgrade() -> None:
    """Downgrade schema.

    Recreate playlist_trace from the latest version per playlist
    (MAX(version_no)) — any history beyond version_no=1 is LOST on
    downgrade (acceptable: downgrade is a recovery escape hatch, not
    a routine operation; preserving history would require either a
    separate archive table or refusing to downgrade non-trivial
    data).
    """
    op.create_table(
        'playlist_trace',
        sa.Column('trace_id', sa.Text(), nullable=False),
        sa.Column('playlist_id', sa.Integer(), nullable=False),
        sa.Column('station_slug', sa.Text(), nullable=False),
        sa.Column('week', sa.Integer(), nullable=False),
        sa.Column('rng', sa.Text(), nullable=False),
        sa.Column('seed', sa.Integer(), nullable=True),
        sa.Column('generated_at', sa.Text(), nullable=False),
        sa.Column('slots', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ['playlist_id'], ['playlist.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('trace_id'),
    )
    with op.batch_alter_table('playlist_trace', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_playlist_trace_playlist_id'),
            ['playlist_id'], unique=False,
        )

    # Copy back the LATEST version per playlist only.
    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO playlist_trace (
            trace_id, playlist_id, station_slug, week, rng, seed,
            generated_at, slots
        )
        SELECT
            v.trace_id, v.playlist_id, v.station_slug, v.week, v.rng,
            v.seed, v.generated_at, v.slots
        FROM playlist_version v
        INNER JOIN (
            SELECT playlist_id, MAX(version_no) AS max_v
            FROM playlist_version
            GROUP BY playlist_id
        ) latest
        ON v.playlist_id = latest.playlist_id
        AND v.version_no = latest.max_v
    """))

    with op.batch_alter_table('playlist_version', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_playlist_version_playlist_id'))
    op.drop_table('playlist_version')
