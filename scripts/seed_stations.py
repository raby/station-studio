"""Backward-compat CLI shim for `python -m scripts.seed_stations`.

§16.13.t9: the seed-stations LIBRARY moved to `engine.seeding` so the
wheel doesn't need to ship the whole `scripts/` package — engine/
ships in the wheel; scripts/ doesn't. This file stays for two
reasons:

  1. The legacy `python -m scripts.seed_stations` invocation continues
     to work for any cron / Makefile / docs reference that uses it.
  2. Test imports that used `from scripts.seed_stations import ...`
     keep working without a coordinated rewrite (they re-export
     unchanged from engine.seeding).

If a third reason ever surfaces — like the dev-tools shipping
becoming desirable — promote engine.seeding to the canonical home
and delete this file.
"""
from __future__ import annotations

from engine.seeding import (  # noqa: F401 — public re-exports
    REPO_ROOT,
    STATIONS_DIR,
    _collect_paths,
    _resolve_stations_dir,
    main,
    seed_all,
    seed_station,
)


if __name__ == "__main__":
    raise SystemExit(main())
