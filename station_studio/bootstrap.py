"""§16.13 c3 — first-run bootstrap.

What happens when `station-studio serve` runs for the first time:

  1. Create `~/.station-studio/` (or `$STATION_STUDIO_HOME`) + the
     `stations/` and `exports/` subdirs.
  2. Set `STATION_STUDIO_DB_URL` + `STATION_STUDIO_STATIONS_DIR` env
     vars so engine.database / api.deps / scripts.seed_stations all
     resolve against the app-home.
  3. Run `alembic upgrade head` against the bundled migrations to
     create / migrate the DB.
  4. If the stations dir is EMPTY, copy the bundled example stations
     into it (D6: copy on first run; user edits are preserved
     forever because we only seed when the dir starts empty).

Idempotent — running on an existing app-home is a no-op (each step
checks before mutating). Cheap enough to call on EVERY CLI
invocation (~10ms when the DB is up-to-date, < 1s on cold start).
"""
from __future__ import annotations

import importlib.resources
import logging
import os
import shutil
from pathlib import Path
from typing import Callable

from station_studio import home as _home


logger = logging.getLogger(__name__)

# Default callable for "say nothing" — bootstrap is also called from
# non-CLI code paths (e.g. tests) where click.echo would be the wrong
# thing.
def _silent(_msg: str) -> None:
    pass


def bundled_path(subpath: str) -> Path:
    """Return the filesystem path to a bundled data file inside the
    wheel.

    Resolves via `importlib.resources` so it works for both editable
    installs (the in-repo `station_studio/_bundled/` if present — not
    populated today; c4 will wire that) AND production wheels
    (force-included by c1's `[tool.hatch.build.targets.wheel.force-include]`
    block).

    NOTE: editable installs don't carry the `_bundled/` data because
    `force-include` only fires at wheel build time. In dev, bootstrap
    falls back to walking up from `__file__` to find the repo root.
    """
    try:
        files = importlib.resources.files("station_studio")
        candidate = Path(str(files / "_bundled" / subpath))
        if candidate.exists():
            return candidate
    except (ModuleNotFoundError, AttributeError):
        pass

    # Editable / in-repo fallback. station_studio/_bundled/ → walk to
    # repo root + use the canonical subdir name. The repo layout has:
    #   <repo>/migrations/
    #   <repo>/stations/
    #   <repo>/alembic.ini
    repo_root = Path(__file__).resolve().parent.parent
    fallback_map = {
        "alembic.ini": repo_root / "alembic.ini",
        "migrations": repo_root / "migrations",
        "stations": repo_root / "stations",
    }
    top = subpath.split("/", 1)[0]
    if top in fallback_map:
        base = fallback_map[top]
        rest = subpath[len(top):].lstrip("/")
        return base / rest if rest else base
    raise FileNotFoundError(f"bundled data not found: {subpath}")


def _run_alembic_upgrade(db_url: str) -> None:
    """Run `alembic upgrade head` against the bundled migrations.

    Builds the alembic Config WITHOUT reading the bundled `alembic.ini`
    — only the script_location + sqlalchemy.url options matter at
    runtime, and the ini's `[loggers]/[handlers]/[formatters]` sections
    call `logging.config.fileConfig` which has `disable_existing_loggers
    =True` by default. That silently nukes every logger configured
    before bootstrap runs (e.g. pytest's caplog-targeted ones), making
    tests order-sensitive in subtle ways.

    Side benefit: skipping ini reads also makes this work cleanly when
    the bundled alembic.ini is unreachable (some installer setups).
    """
    from alembic import command
    from alembic.config import Config

    migrations_dir = bundled_path("migrations")

    # No `file_=` arg → fileConfig isn't invoked.
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(config, "head")


def _copy_missing_stations(target_dir: Path) -> int:
    """Copy bundled station JSONs (+ sidecars) into `target_dir`,
    skipping files that already exist.

    Returns the number of new files written. Existing files survive
    (so user edits aren't clobbered between `station-studio seed`
    runs). A future `seed --force` would overwrite — out of scope for
    c3.
    """
    source_dir = bundled_path("stations")
    if not source_dir.is_dir():
        return 0
    copied = 0
    for src in sorted(source_dir.iterdir()):
        if not src.is_file():
            continue
        dst = target_dir / src.name
        if dst.exists():
            continue
        shutil.copy2(src, dst)
        copied += 1
    return copied


def bootstrap(*, echo: Callable[[str], None] = _silent) -> Path:
    """Run the first-run bootstrap. Returns the resolved app-home Path.

    Steps (each idempotent):
      1. ensure_home() — mkdir app-home + subdirs.
      2. Set STATION_STUDIO_DB_URL + STATION_STUDIO_STATIONS_DIR via
         `os.environ.setdefault` (existing env wins — tests use this
         to point at tmp_path).
      3. alembic upgrade head against the app-home's DB.
      4. Copy bundled stations into the stations dir, skipping
         already-present files.

    `echo` defaults to silent. CLI callers pass `click.echo` so the
    user sees each step.
    """
    home = _home.ensure_home()
    echo(f"App home: {home}")

    # Env var setup. `setdefault` lets a test (or sophisticated user)
    # override before bootstrap runs — useful for pytest fixtures that
    # point at tmp_path, and for the future docker case where the
    # docker-compose volume mount might pick a different DB URL.
    db_url = _home.db_url()
    os.environ.setdefault("STATION_STUDIO_DB_URL", db_url)
    os.environ.setdefault(
        "STATION_STUDIO_STATIONS_DIR", str(_home.stations_dir())
    )

    # DB migrations. The bundled alembic.ini's `sqlalchemy.url` is a
    # placeholder; we override it to the resolved app-home URL.
    echo(f"DB: {_home.db_path()}")
    echo("Running migrations (alembic upgrade head)...")
    _run_alembic_upgrade(os.environ["STATION_STUDIO_DB_URL"])

    # Station copy — only does work on first run.
    copied = _copy_missing_stations(_home.stations_dir())
    if copied:
        echo(f"Copied {copied} example station file(s) into stations dir.")
    else:
        echo("Stations dir already populated; skipping example copy.")

    return home
