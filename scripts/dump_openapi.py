"""§16.15 c4 — dump the FastAPI app's OpenAPI schema to a file.

Invoked by `make docs-openapi` before `make docs-build` so the docs
site's `reference/api.md` can render Swagger UI against the
just-emitted schema (via `mkdocs-render-swagger-plugin`).

Why a separate script vs. plugin-time generation:
  - The plugin reads a JSON file at build time; it doesn't import
    Python apps. Producing the JSON is OUR job.
  - Keeps the docs build hermetic — once `docs/reference/openapi.json`
    exists, `mkdocs build --strict` runs without importing the
    FastAPI app + its full dep tree (engine / sqlalchemy / etc).
  - The JSON file is gitignored — it's a build artifact, not source.
    `docs-openapi` regenerates it; CI runs that before `docs-build`.

Output is deterministic JSON with `indent=2` + sorted top-level keys
so diffs across runs only show actual schema changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from api.main import app


def main() -> int:
    out_path = Path("docs/reference/openapi.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    out_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    n_paths = len(schema.get("paths", {}))
    print(f"dump_openapi: wrote {out_path} ({n_paths} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
