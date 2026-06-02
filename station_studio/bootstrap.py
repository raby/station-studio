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
    repo_root = _resolve_repo_root()
    if repo_root is None:
        # §16.13.t5: the depth-hardcoded walk produced a non-repo
        # directory (no pyproject.toml at the resolved candidate).
        # Fail with a CLEAR diagnostic instead of silently returning
        # a non-existent path that explodes opaquely a step later in
        # `shutil.copytree` or alembic.
        raise FileNotFoundError(
            f"bundled data not found: {subpath} (and the in-repo "
            "fallback couldn't locate the repo root from "
            f"{Path(__file__).resolve()} — did the package layout move?)"
        )
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


def _resolve_repo_root() -> Path | None:
    """§16.13.t5 — depth-adaptive, sanity-checked repo-root resolution.

    Walks `Path(__file__).resolve().parents` until a `pyproject.toml`
    appears (or the filesystem root is reached). The pyproject is the
    canonical anchor for "this is the project root"; finding it tells
    us where to mount the in-repo fallback paths
    (stations/ / migrations/ / alembic.ini) regardless of how deep
    `bootstrap.py` lives inside the package.

    Pre-t5: hardcoded `parent.parent`. A future move to a deeper
    package (e.g. `station_studio/_internal/bootstrap.py`) silently
    resolved one level too shallow and failed opaquely in
    `shutil.copytree`. Post-t5 first cut: same `parent.parent` but
    with a pyproject existence check at the resolved candidate.
    Post-t5-review 🟡-1 (this iteration): walk parents to actually
    survive depth drift — the check now ADAPTS to the new layout
    automatically instead of just failing louder.

    Same structural-not-naming-based check as
    `api/deps.py:_verify_repo_layout` (§16.2.2.t4). Repo-name-
    agnostic so forks into `my-station-fork/` still work.

    Returns the dir on success, None on failure (caller decides
    what error to raise — keeps call sites cleaner than a raise
    here).
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def _run_alembic_upgrade(db_url: str) -> bool:
    """Run `alembic upgrade head` against the bundled migrations.

    Returns True if migrations were actually applied, False if the DB
    was already at head and the upgrade was skipped (§16.13.t7).

    Builds the alembic Config WITHOUT reading the bundled `alembic.ini`
    — only the script_location + sqlalchemy.url options matter at
    runtime, and the ini's `[loggers]/[handlers]/[formatters]` sections
    call `logging.config.fileConfig` which has `disable_existing_loggers
    =True` by default. That silently nukes every logger configured
    before bootstrap runs (e.g. pytest's caplog-targeted ones), making
    tests order-sensitive in subtle ways.

    Side benefit: skipping ini reads also makes this work cleanly when
    the bundled alembic.ini is unreachable (some installer setups).

    §16.13.t7: short-circuit when the DB is already at head. Saves
    ~100ms per CLI call (alembic version-check + log overhead +
    DDL-statement parsing for no-op steps). Negligible for `serve`
    (once at boot) and `seed` (rare), but for cron'd batch import
    workflows the per-call overhead adds up. Cheap enough to always
    do, no cost when it doesn't apply.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    migrations_dir = bundled_path("migrations")

    # No `file_=` arg → fileConfig isn't invoked.
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", db_url)

    # §16.13.t7: fast-path. Open a short-lived connection, read
    # alembic_version.version_num, compare to the migration scripts'
    # `head` revision. If they match, skip the upgrade. If the table
    # doesn't exist (first-run, fresh DB), current_rev is None and we
    # fall through to the real upgrade (which creates the table +
    # stamps it).
    script_dir = ScriptDirectory.from_config(config)
    head_rev = script_dir.get_current_head()
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_rev = ctx.get_current_revision()
    finally:
        engine.dispose()

    if current_rev is not None and current_rev == head_rev:
        return False

    command.upgrade(config, "head")
    return True


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
    # §16.13.t7: print whether work was actually done so the user
    # gets honest feedback (rather than "Running migrations..." that
    # silently did nothing).
    echo(f"DB: {_home.db_path()}")
    if _run_alembic_upgrade(os.environ["STATION_STUDIO_DB_URL"]):
        echo("Ran migrations (alembic upgrade head).")
    else:
        echo("DB already at head; skipping migrations.")

    # Station copy — only does work on first run.
    copied = _copy_missing_stations(_home.stations_dir())
    if copied:
        echo(f"Copied {copied} example station file(s) into stations dir.")
    else:
        echo("Stations dir already populated; skipping example copy.")

    return home
