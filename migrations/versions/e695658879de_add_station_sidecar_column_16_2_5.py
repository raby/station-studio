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
    `api.deps.get_stations_dir`.

    c1 review #4: the prior default `Path("stations")` was
    CWD-RELATIVE — common alembic invocations from a non-repo-
    root cwd (e.g. `cd migrations && alembic upgrade head`,
    operator-typed paths, Docker shells starting in /app) would
    miss the directory + silently no-op the backfill. The runtime
    side (`api/deps.py:_STATIONS_DIR`) is absolute: it climbs from
    `__file__` to the repo root. Mirror that exactly so the
    backfill + runtime agree on the same `stations/` directory
    regardless of cwd.

    Returns the absolute Path, or None when the override is set
    but empty (explicit "skip").
    """
    override = os.environ.get("STATION_STUDIO_STATIONS_DIR")
    if override is not None:
        override = override.strip()
        if override == "":
            # Explicit empty override → skip (some CI configs use
            # this signal to disable disk IO during migration).
            return None
        return Path(override).resolve()
    # Default: climb from this migration file to the repo root,
    # then `stations/`. Matches api/deps.py's resolution:
    # migrations/versions/<file>.py → migrations/ → repo root.
    return (Path(__file__).resolve().parent.parent.parent / "stations")


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
