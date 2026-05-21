"""Import a weekly-format template JSON as a station-studio station.

Sub-slice #16 of the §17 Phase B port roadmap. Reads a JSON file
produced by `scripts/dump_weekly_template.py` (run inside a weekly/
checkout), translates it via `engine.template_importer`, and upserts
the resulting station-studio template into the Station table.

Usage
-----

    # Via the Makefile (recommended — adds a pre-import DB backup):
    make import-station slug=rebirth-radio json=weekly-template.json

    # Direct:
    python -m scripts.import_station rebirth-radio weekly-template.json

    # Re-import without a backup (idempotent re-runs):
    python -m scripts.import_station rebirth-radio weekly-template.json \\
        --no-backup

    # Custom display name (defaults to slug-title-cased):
    python -m scripts.import_station rebirth-radio weekly-template.json \\
        --name "Rebirth Radio"

Behaviour
---------

* Idempotent: re-running with the same JSON is a no-op (action=
  "unchanged"); re-running with a modified JSON updates the existing
  row's `template` field.
* Errors propagate `WeeklyImportError` (malformed input) or
  `TemplateError` (the translator's output didn't parse — a station-
  studio bug, not user error) with a clear non-zero exit and stderr
  message.
* Does NOT touch source pools (iTunes XML import is a separate flow).
  After import, the station is in the same state as a fresh
  `seed_station` — ready to generate once pools are loaded.
* Does NOT advance cursor_state or write playlists. The first
  generation after import runs with positions from `cursors_init`
  (§14.30 stateful mode hydrates cursor_state lazily on first use).

The pre-import DB backup is handled by the Makefile target, not by
this script — keeping backup concerns in one place (§14.18 / the
`backup` target) means this script stays focused on the upsert and
the backup safety net works the same as for `make restore`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station
from engine.template import TemplateError
from engine.template_importer import WeeklyImportError, import_weekly_template


def _default_name(slug: str) -> str:
    """Convert 'rebirth-radio' → 'Rebirth Radio' for the default display name.

    Caller can override with --name. This is just a more-readable
    fallback than echoing the slug verbatim — every other seeded
    station has a separate human-friendly `name` field, so importing
    with `name = slug` would look out-of-place in the UI's station
    list."""
    return " ".join(part.capitalize() for part in slug.split("-"))


def translate_only(
    *,
    slug: str,
    json_path: Path,
    name: str | None,
) -> dict:
    """Run the weekly→station-studio translation only, no DB writes.

    Pure-function wrapper around `import_weekly_template`; identical
    output to what `import_one` would write to the DB minus the upsert.
    Used by the `--output FILE` CLI mode (§14.36) to produce the
    committed port artifact `stations/rebirth-radio.json`.
    """
    raw = json.loads(json_path.read_text())
    display_name = name if name is not None else _default_name(slug)
    return import_weekly_template(raw, slug=slug, name=display_name)


def import_one(
    db: Session,
    *,
    slug: str,
    json_path: Path,
    name: str | None,
) -> dict:
    """Import one weekly-format JSON file as a station.

    Returns a summary dict: slug, action (created|updated|unchanged),
    slot_count, cursor_count.
    """
    raw = json.loads(json_path.read_text())
    display_name = name if name is not None else _default_name(slug)

    template = import_weekly_template(raw, slug=slug, name=display_name)

    existing = db.query(Station).filter(Station.slug == slug).first()
    if existing is None:
        db.add(Station(slug=slug, name=display_name, template=template))
        action = "created"
    elif existing.template != template:
        existing.template = template
        existing.name = display_name
        action = "updated"
    else:
        action = "unchanged"

    return {
        "slug": slug,
        "action": action,
        "slot_count": len(template["layout"]["flat_slots"]),
        "cursor_count": len(template["layout"]["cursors_init"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import a weekly-format template JSON as a station-studio station.",
    )
    parser.add_argument("slug", help="Station slug (URL-safe identifier).")
    parser.add_argument(
        "json_path",
        type=Path,
        help="Path to the weekly-format template JSON "
        "(produced by scripts/dump_weekly_template.py inside a weekly/ checkout).",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Display name for the station. Defaults to slug title-cased "
        "(e.g. rebirth-radio → 'Rebirth Radio').",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Suppress the Makefile's pre-import DB backup. The Makefile "
        "honours this flag; this script reads it only for `--help` "
        "documentation parity with the Makefile UX.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="If set, translate and write the station-studio template JSON "
        "to FILE — DOES NOT touch the DB. Used to refresh the committed "
        "port artifact `stations/rebirth-radio.json` (§14.36). The output "
        "is exactly what would have been upserted to the Station row's "
        "`template` column.",
    )
    args = parser.parse_args(argv)

    if not args.json_path.is_file():
        print(f"error: no such file: {args.json_path}", file=sys.stderr)
        return 1

    # --output: translate-only path, no DB writes.
    if args.output is not None:
        try:
            template = translate_only(
                slug=args.slug, json_path=args.json_path, name=args.name,
            )
        except WeeklyImportError as e:
            print(f"import failed (input data problem): {e}", file=sys.stderr)
            return 2
        except TemplateError as e:
            print(
                f"import failed (station-studio bug): translator produced an "
                f"invalid template that parse_template later rejected: {e}",
                file=sys.stderr,
            )
            return 3
        # Pretty-printed + trailing newline for clean diff/grep ergonomics.
        args.output.write_text(json.dumps(template, indent=2) + "\n")
        print(
            f"  wrote     {args.slug:<30}  "
            f"{len(template['layout']['flat_slots']):>6} slots, "
            f"{len(template['layout']['cursors_init']):>2} cursors  → {args.output}"
        )
        return 0

    engine = make_engine()
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)

    try:
        with SessionLocal() as db:
            summary = import_one(
                db, slug=args.slug, json_path=args.json_path, name=args.name,
            )
            db.commit()
    except WeeklyImportError as e:
        # User-input problem: malformed weekly JSON, unknown slot type,
        # missing required field, undeclared cursor. Phrased so the user
        # knows to fix the JSON, not file a bug.
        print(f"import failed (input data problem): {e}", file=sys.stderr)
        return 2
    except TemplateError as e:
        # Translator-output problem: the importer's round-trip check
        # didn't catch this. Shouldn't happen; this is a station-studio
        # bug, not user error.
        print(
            f"import failed (station-studio bug): translator produced an "
            f"invalid template that parse_template later rejected: {e}",
            file=sys.stderr,
        )
        return 3

    print(
        f"  {summary['action']:<9}  {summary['slug']:<30}  "
        f"{summary['slot_count']:>6} slots, "
        f"{summary['cursor_count']:>2} cursors"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
