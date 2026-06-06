"""add station.sidecar column + backfill from disk (§16.2.5 D1)

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

Backfill: walks every existing station row, looks for
`stations/{slug}.editor.json` on disk, copies its content into the
new column when present. Stations without an on-disk sidecar stay
NULL (the GET endpoint already synthesises a trivial sidecar in
that case — pre-§16.2.5 behavior preserved).

The disk-resolution path stays as a fallback in code for legacy
DBs that haven't been migrated yet AND for the operator-friendly
"drop a sidecar file in stations/ and reload" workflow during
authoring (the operator may iterate locally before they trust the
in-UI editor).

Revision ID: e695658879de
Revises: c7bb27708788
Create Date: 2026-06-06 17:00:00.000000

"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e695658879de'
down_revision: Union[str, Sequence[str], None] = 'c7bb27708788'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


logger = logging.getLogger("alembic.runtime.migration")


def _stations_dir() -> Path | None:
    """Return the directory the backfill should scan for sidecars.

    Honors `STATION_STUDIO_STATIONS_DIR` (set by tests, Docker,
    Playwright webServer) for parity with the runtime path
    `api.deps.get_stations_dir`. Returns None if the override is
    set to an empty value (interpreted as "skip backfill") so a
    fresh CI database doesn't error trying to read a repo path
    that doesn't exist in the migration sandbox.
    """
    override = os.environ.get("STATION_STUDIO_STATIONS_DIR", "").strip()
    if override == "":
        # Default location relative to the repo (where stations/
        # lives in dev). Falls through to `if not d.is_dir()` below
        # so a CI sandbox without `stations/` quietly skips.
        return Path("stations")
    return Path(override)


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add the column.
    with op.batch_alter_table('station', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sidecar', sa.JSON(), nullable=True))

    # 2. Backfill from disk for existing rows.
    stations_dir = _stations_dir()
    if stations_dir is None or not stations_dir.is_dir():
        logger.info(
            "sidecar backfill skipped: stations dir %s not present "
            "(fresh-install or CI sandbox).",
            stations_dir,
        )
        return

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, slug FROM station")).fetchall()
    if not rows:
        return

    backfilled = 0
    skipped: list[tuple[str, str]] = []  # (slug, reason)
    for row in rows:
        slug = row.slug
        sidecar_path = stations_dir / f"{slug}.editor.json"
        if not sidecar_path.is_file():
            continue
        try:
            content = json.loads(sidecar_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            skipped.append((slug, f"{type(e).__name__}: {e}"))
            continue
        # NOTE — no parse_sidecar validation here. The migration is
        # a *transport* (file → DB column); validation belongs to
        # the runtime GET path, which already handles bad sidecars
        # (Decision E in §16.2.2: trivial-fallback + sidecar_error).
        # Validating here would either fail the migration on
        # operator-committed-but-broken sidecars OR force us to
        # import engine.editor_metadata, which couples the
        # migration to engine code that may evolve.
        conn.execute(
            sa.text("UPDATE station SET sidecar = :s WHERE id = :i"),
            {"s": json.dumps(content), "i": row.id},
        )
        backfilled += 1

    if backfilled:
        logger.info(
            "sidecar backfill: copied %d sidecar(s) from %s into "
            "Station.sidecar.", backfilled, stations_dir,
        )
    if skipped:
        logger.warning(
            "sidecar backfill: skipped %d unreadable sidecar(s): %s",
            len(skipped), skipped,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('station', schema=None) as batch_op:
        batch_op.drop_column('sidecar')
