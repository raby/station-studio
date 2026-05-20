"""Import weekly's 6 iTunes-XML source pools via a manifest JSON.

Sub-slice #17 commit 2 (§14.35). Bridges from the imported template
(§14.34) to a station that can generate, by loading the 6 source
pools (JINGLES / COMMERCIAL / MIXES / NIGHT / WEEKEND / MASTER) in
a single command.

The manifest is the input contract — a small JSON file pointing at
the XML paths + declaring each pool's role:

    {
      "_comment": "Optional inline documentation",
      "schema_version": 1,
      "pools": {
        "JINGLES":    {"path": "from_user/JINGLES.xml",    "role": "content"},
        "COMMERCIAL": {"path": "from_user/COMMERCIAL.xml", "role": "content"},
        "MIXES":      {"path": "from_user/MIXES.xml",      "role": "content"},
        "NIGHT":      {"path": "from_user/NIGHT.xml",      "role": "content"},
        "WEEKEND":    {"path": "from_user/WEEKEND.xml",    "role": "content"},
        "MASTER":     {"path": "from_user/MASTER.xml",     "role": "reference"}
      }
    }

Relative `path` values resolve against the manifest file's parent
directory — typical workflow has the manifest sit next to the XMLs.

Usage
-----

    # Via the Makefile (recommended — adds a pre-import DB backup):
    make import-weekly-pools manifest=path/to/manifest.json

    # Direct:
    python -m scripts.import_weekly_pools path/to/manifest.json

    # Re-run without the auto-backup (still idempotent):
    python -m scripts.import_weekly_pools path/to/manifest.json --no-backup

Behaviour
---------

* Strict-mode manifest parsing (mirrors §14.32 / §14.34 policy):
  - Allowed top-level keys: ``pools``, ``_comment``, ``schema_version``
  - Required per-pool keys: ``path``, ``role`` (no defaults — explicit)
  - Unknown keys at either level raise ``ManifestError``
  - ``role`` must be ``"content"`` or ``"reference"``

* Per-pool action reporting (created / updated), mirroring
  ``import_station.py``'s output style. "Unchanged" isn't reported
  because import_source's existing semantics drop-and-recreate the
  SourcePlaylist row on every run; tracking field-level idempotency
  would need a separate comparison pass not worth the complexity.

* Errors propagate ``ManifestError`` (user-input problem) with a
  clear non-zero exit. The importer's own ``ValueError`` (malformed
  XML, invalid role at runtime) also returns non-zero.

* Auto-backup lives on the Makefile target, not here — matches
  ``import_station.py`` / §14.18.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.importer import import_source
from engine.models import Base, SourcePlaylist


_VALID_ROLES = frozenset({"content", "reference"})
_ALLOWED_TOP_LEVEL = {"pools", "_comment", "schema_version"}
_REQUIRED_TOP_LEVEL = {"pools"}
_ALLOWED_POOL_FIELDS = {"path", "role"}


class ManifestError(ValueError):
    """Raised when a pool manifest is malformed.

    Distinct from importer errors (which raise ValueError) so the CLI
    can report "fix your manifest JSON" vs "fix your iTunes XML"
    differently — the user can act on each in isolation.
    """


def _validate_manifest(raw: Any) -> dict[str, dict[str, str]]:
    """Strict-mode validation of the manifest dict. Returns the pools mapping.

    Mirrors §14.32 / §14.34 parser policy: unknown keys are rejected
    up front so a typo (`pool` for `pools`, `cursorid` for `cursor_id`)
    surfaces as a clear error rather than a silent field-drop.
    """
    if not isinstance(raw, dict):
        raise ManifestError(
            f"manifest must be a JSON object, got {type(raw).__name__}"
        )

    missing = _REQUIRED_TOP_LEVEL - set(raw.keys())
    if missing:
        raise ManifestError(
            f"manifest missing required keys: {sorted(missing)}. "
            f"Got: {sorted(raw.keys())}"
        )
    unknown = set(raw.keys()) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise ManifestError(
            f"manifest has unknown top-level keys: {sorted(unknown)}. "
            f"Allowed: {sorted(_ALLOWED_TOP_LEVEL)}. "
            f"Strict mode mirrors §14.32 / §14.34 parser policy."
        )

    pools = raw["pools"]
    if not isinstance(pools, dict):
        raise ManifestError(
            f"manifest 'pools' must be an object, got {type(pools).__name__}"
        )
    if not pools:
        raise ManifestError(
            "manifest 'pools' must contain at least one pool entry. "
            "An empty manifest has nothing to import."
        )

    for name, entry in pools.items():
        if not isinstance(name, str) or not name:
            raise ManifestError(
                f"pool name must be a non-empty string, got {name!r}"
            )
        if not isinstance(entry, dict):
            raise ManifestError(
                f"pool {name!r}: entry must be an object, got "
                f"{type(entry).__name__}"
            )
        missing = {"path", "role"} - set(entry.keys())
        if missing:
            raise ManifestError(
                f"pool {name!r}: missing required fields {sorted(missing)}. "
                f"Got: {sorted(entry.keys())}. "
                f"Both 'path' and 'role' are required — no defaults "
                f"(explicit role prevents the 'forgot to set role on MASTER' mistake)."
            )
        unknown = set(entry.keys()) - _ALLOWED_POOL_FIELDS
        if unknown:
            raise ManifestError(
                f"pool {name!r}: unknown fields {sorted(unknown)}. "
                f"Allowed per pool: {sorted(_ALLOWED_POOL_FIELDS)}."
            )
        if entry["role"] not in _VALID_ROLES:
            raise ManifestError(
                f"pool {name!r}: invalid role {entry['role']!r}. "
                f"Allowed values are {sorted(_VALID_ROLES)}."
            )
        if not isinstance(entry["path"], str) or not entry["path"]:
            raise ManifestError(
                f"pool {name!r}: 'path' must be a non-empty string, "
                f"got {entry['path']!r}"
            )
    return pools


def import_manifest(db: Session, manifest_path: Path) -> list[dict]:
    """Read a manifest JSON, import each declared pool, return a per-pool
    summary list. The manifest's parent directory is the base for
    relative XML paths.

    Raises ManifestError on a malformed manifest. Importer errors
    (malformed XML, etc.) propagate as ValueError.
    """
    raw = json.loads(manifest_path.read_text())
    pools = _validate_manifest(raw)
    manifest_dir = manifest_path.parent

    summaries: list[dict] = []
    for name, entry in pools.items():
        xml_path = (manifest_dir / entry["path"]).resolve()
        if not xml_path.is_file():
            raise ManifestError(
                f"pool {name!r}: file not found at {xml_path} "
                f"(relative paths resolve against the manifest's parent "
                f"directory: {manifest_dir})"
            )

        # Track the action label BEFORE import_source drops the
        # existing row. import_source itself is idempotent (drops + recreates).
        existing = db.query(SourcePlaylist).filter_by(name=name).first()
        action = "updated" if existing else "created"

        summary = import_source(db, xml_path, name, role=entry["role"])
        summary["action"] = action
        summaries.append(summary)

    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import weekly's 6 iTunes-XML source pools via a manifest JSON. "
            "See DESIGN.md §14.35."
        ),
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="Path to the pool manifest JSON. Relative paths in the "
        "manifest resolve against this file's parent directory.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Suppress the Makefile's pre-import DB backup. The Makefile "
        "honours this flag; this script reads it only for --help parity "
        "with the Makefile UX.",
    )
    args = parser.parse_args(argv)

    if not args.manifest.is_file():
        print(f"error: no such manifest file: {args.manifest}", file=sys.stderr)
        return 1

    engine = make_engine()
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)

    try:
        with SessionLocal() as db:
            summaries = import_manifest(db, args.manifest)
            db.commit()
    except ManifestError as e:
        # User-input problem with the manifest itself — phrased so the
        # user knows to fix the JSON, not the XMLs. Phrasing mirrors
        # scripts/import_station.py's "import failed (XYZ): ..." pattern.
        print(f"import failed (manifest problem): {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        # Importer error: malformed XML, invalid role at runtime
        # (Pydantic Literal would have caught this on the API path,
        # but the CLI calls import_source directly).
        print(f"import failed (XML problem): {e}", file=sys.stderr)
        return 3

    for s in summaries:
        print(
            f"  {s['action']:<7}  {s['name']:<15}  {s['role']:<9}  "
            f"{s['tracks_imported']:>5} tracks, "
            f"{s['entries_imported']:>5} entries"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
