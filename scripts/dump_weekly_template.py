"""Dump weekly's `build_template()` + INITIAL_CURSORS to a JSON file.

**Designed to run INSIDE a weekly/ checkout, NOT inside station-studio.**

Station-studio imports the resulting JSON via `scripts/import_station.py`
(or `make import-station slug=… json=…`). The decoupling — weekly-side
dump → JSON → station-studio-side import — means station-studio doesn't
need to depend on weekly's package layout, and the JSON dump is a
human-readable audit artifact (commits to a repo, attach to bug
reports, diff between weekly revisions).

Usage (from inside a weekly/ checkout, with weekly's venv active):

    python /path/to/station-studio/scripts/dump_weekly_template.py \\
        > weekly-template.json

    # Or with an explicit output path:
    python /path/to/station-studio/scripts/dump_weekly_template.py \\
        --out weekly-template.json

Output shape — matches `engine.template_importer.import_weekly_template`'s
expected input:

    {
      "_comment": "…",
      "schema_version": 1,
      "initial_cursors": { "NIGHT_1": 0, … },
      "slots": [
        { "type": "jingle", "genre": "Daytime" },
        { "type": "commercial" },
        …
      ]
    }

Implementation note: this script imports `app.services.template` from
the surrounding weekly checkout. It does NOT import anything from
station-studio — copy it standalone if needed. The two-line import
block is the only weekly coupling; if weekly's module path changes,
that's the only edit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def dump(out_path: Path | None) -> int:
    # Imported lazily so `--help` works outside a weekly checkout.
    try:
        from app.services.template import INITIAL_CURSORS, build_template
    except ImportError as e:
        print(
            f"error: failed to import weekly's template module: {e}\n"
            f"This script must run inside a weekly/ checkout with weekly's "
            f"venv active (it imports app.services.template).",
            file=sys.stderr,
        )
        return 1

    data = {
        "_comment": (
            "Dumped from weekly's build_template() + INITIAL_CURSORS via "
            "station-studio/scripts/dump_weekly_template.py. Import into "
            "station-studio via `make import-station slug=… json=…` "
            "(see DESIGN.md §14.34)."
        ),
        "schema_version": 1,
        "initial_cursors": dict(INITIAL_CURSORS),
        "slots": list(build_template()),
    }

    text = json.dumps(data, indent=2, sort_keys=False) + "\n"
    if out_path is None:
        sys.stdout.write(text)
    else:
        out_path.write_text(text)
        print(
            f"wrote {out_path} — {len(data['slots'])} slots, "
            f"{len(data['initial_cursors'])} cursors",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump weekly's template to JSON for station-studio import.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path. If omitted, writes JSON to stdout.",
    )
    args = parser.parse_args(argv)
    return dump(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
