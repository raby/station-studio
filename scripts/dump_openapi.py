"""§16.15 c4 — dump the FastAPI app's OpenAPI schema to a file.

Invoked by `make docs-openapi` before `make docs-build` so the docs
site's `reference/api.md` can render Swagger UI against the
just-emitted schema (via `mkdocs-render-swagger-plugin`).

Why a separate script vs. plugin-time generation:
  - The plugin reads a JSON file at build time; it doesn't import
    Python apps. Producing the JSON is OUR job.
  - Lets us neutralise import-time side effects (see R1 fix
    below) before `app.openapi()` runs — `mkdocs build` itself
    never needs to touch sqlalchemy / engine / etc, even though
    the Make chain does because docs-openapi is a prereq.
  - The JSON file is gitignored — it's a build artifact, not source.
    `docs-openapi` regenerates it; CI runs that before `docs-build`.

Output is deterministic JSON with `indent=2` + sorted top-level keys
so diffs across runs only show actual schema changes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# §16.15 c4-review R1 — `from api.main import app` transitively
# imports `api.deps`, which runs `make_engine() +
# Base.metadata.create_all()` at MODULE LOAD (api/deps.py:11-15).
# Without this override, a fresh `make docs-build` would write or
# migrate the contributor's real `station_studio.db` (or whatever
# `STATION_STUDIO_DB_URL` points at — including production paths).
# Point at an in-memory SQLite before the api import fires so the
# side effect lands on a throwaway database that gc's at process
# exit. Also opt out of the §sweep-1 auto-seed lifespan handler
# defensively — the dump never starts the app, so it shouldn't
# fire anyway, but explicit is cheaper than a future regression.
os.environ.setdefault("STATION_STUDIO_DB_URL", "sqlite:///:memory:")
os.environ.setdefault("STATION_STUDIO_SKIP_AUTO_SEED", "1")

from api.main import app  # noqa: E402 — import after env setup is load-bearing

# §16.15 c4-review R4 — anchor out_path to this file's location
# instead of CWD so an ad-hoc `python scripts/dump_openapi.py`
# (or an IDE auto-launcher with cwd=script-dir) still writes
# to the canonical docs/reference/ path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT_PATH = _REPO_ROOT / "docs" / "reference" / "openapi.json"


def main() -> int:
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    _OUT_PATH.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    n_paths = len(schema.get("paths", {}))
    print(f"dump_openapi: wrote {_OUT_PATH} ({n_paths} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
