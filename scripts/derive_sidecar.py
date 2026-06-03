"""§16.2.1 c2 / §16.14.t2 — CLI wrapper around `engine.editor_metadata.derive_sidecar`.

The core helper (the pure function that takes a station template dict
and returns a sidecar dict) lives in `engine/editor_metadata.py` as of
§16.14.t1's slice-review sweep. This module is now a thin CLI wrapper
so the historical `python -m scripts.derive_sidecar --station X --out
Y` invocation from Makefile + CONTRIBUTING.md + the
stations/README.md recipe keeps working.

Pre-lift, `engine.seeding.auto_derive_missing_sidecars` imported
`from scripts.derive_sidecar import derive_sidecar` at function-body
level — which crashed at runtime on any wheel install (the wheel
doesn't ship `scripts/` post §16.13.t9 closure). Caught by `make
wheel-smoke` during the §16.14.t1 slice-review sweep.

For library callers (`engine.seeding`, future API endpoints), import
directly from `engine.editor_metadata`:

    from engine.editor_metadata import derive_sidecar
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from engine.editor_metadata import EditorMetadataError, derive_sidecar
# Back-compat re-exports for tests/external callers that imported the
# helpers (`_spec_key`, `_detect_section_ranges`, `_build_section`,
# `MIN_MACRO_RUN`) from this module before the §16.14.t2 lift. They
# live in `engine.editor_metadata` under `_derive_*` prefixed names
# now; alias them here so `from scripts.derive_sidecar import ...`
# keeps working for the existing tests/test_derive_sidecar.py file.
from engine.editor_metadata import (  # noqa: F401 — re-export
    _DERIVE_MIN_MACRO_RUN as MIN_MACRO_RUN,
    _derive_build_section as _build_section,
    _derive_is_daytime_jingle as _is_daytime_jingle,
    _derive_section_ranges as _detect_section_ranges,
    _derive_spec_key as _spec_key,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Auto-derive an editor sidecar from a flat_slots station template.",
    )
    parser.add_argument(
        "--station",
        type=Path,
        required=True,
        help="Path to the station JSON (must use flat_slots layout form).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Path to write the derived sidecar JSON.",
    )
    args = parser.parse_args(argv)

    if not args.station.is_file():
        print(f"error: station file not found: {args.station}", file=sys.stderr)
        return 1

    station = json.loads(args.station.read_text())
    try:
        sidecar = derive_sidecar(station)
    except EditorMetadataError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    args.out.write_text(json.dumps(sidecar, indent=2) + "\n")
    n_sections = len(sidecar["sections"])
    n_macros = len(sidecar["macros"])
    print(
        f"  wrote     {args.out}  "
        f"{n_sections:>3} sections, "
        f"{n_macros:>3} macros"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
