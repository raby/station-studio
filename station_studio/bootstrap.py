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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from station_studio import home as _home


@dataclass(frozen=True)
class BootstrapResult:
    """§16.13.t8 — structured outcome of a bootstrap() run.

    Pre-t8 `bootstrap()` returned just the app-home Path and surfaced
    progress via an `echo` callback. Non-CLI callers wanting to know
    "did migrations actually run? how many stations were copied?" had
    to parse the printed strings — fragile and not part of the
    contract.

    Post-t8 `bootstrap()` returns this dataclass; callers inspect the
    fields directly. The CLI keeps using `echo` for user feedback;
    non-CLI callers (future Python API, REST endpoint exposing
    bootstrap status, integration tests) read structured data.

    Frozen because callers shouldn't mutate the record after bootstrap
    completes — it's a snapshot of "what happened on this run."

    Fields match the t8 design spec plus the post-review additions:
    `was_first_run` (🟡-1), `head_revision` (🟡-2), and `db_path`
    derived from `db_url` so the two never diverge (🟡-3).
    """
    home: Path
    db_url: str
    # t8-review 🟡-3: derived from `db_url` (not from `home`), so an
    # env-overridden DB URL never disagrees with the path the caller
    # would actually open. For non-sqlite URLs, db_path is None (no
    # meaningful filesystem path — the DB lives over the network).
    db_path: Path | None
    stations_dir: Path
    # t8-review 🟡-1: True if the app-home directory didn't exist
    # before this `bootstrap()` call (the canonical "first run"
    # signal). Captured BEFORE `ensure_home()` runs. Not derivable
    # from `migrations_applied + stations_copied` — a partial install
    # where the user manually re-ran alembic then deleted stations
    # would false-positive that combined check.
    was_first_run: bool
    # True if `alembic upgrade head` actually applied migrations on
    # this run. False if the DB was already at head (t7 fast-path) and
    # we short-circuited. Mirrors `_run_alembic_upgrade`'s return.
    migrations_applied: bool
    # t8-review 🟡-2: the migration head revision at the end of this
    # bootstrap (applied OR already-at). Lets a caller alert "we just
    # moved to revision abc123, invalidate caches" without parsing
    # logs. None if the migrations dir is empty or unreadable (which
    # is a separate failure mode, not "no migrations applied").
    head_revision: str | None
    # Number of bundled station files copied into the app-home's
    # stations dir on this run. 0 once seeded; non-zero on first run
    # (and any later run that finds a deleted file in the
    # bundle-vs-home diff).
    stations_copied: int
    # §16.14 c2: number of starter pools auto-imported on this run.
    # Always 0 once ANY SourcePlaylist exists (Q8 first-boot guard) —
    # the user is in charge of pools after the initial seed.
    pools_auto_imported: int = 0
    # §16.14 c2: number of `.editor.json` sidecars auto-derived for
    # station JSONs in the home's stations dir that were missing
    # one. Existing sidecars are never overwritten.
    sidecars_derived: int = 0

    # __fspath__ removed intentionally — the dataclass should be
    # explicit, not pretend to be a Path. Callers that want the home
    # use `.home`.


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


def _run_alembic_upgrade(db_url: str) -> tuple[bool, str | None]:
    """Run `alembic upgrade head` against the bundled migrations.

    Returns `(applied, head_revision)`:
      * `applied` — True if `command.upgrade` ran, False if the DB
        was already at head and the fast-path short-circuited
        (§16.13.t7).
      * `head_revision` — the migration head's revision id (str), or
        None if the migrations directory is empty / unreadable.
        Lifted out so `BootstrapResult.head_revision` (§16.13.t8
        review 🟡-2) carries the actual revision identifier.

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

    Assumes a SINGLE-HEAD migration tree (the project's invariant
    today). If branch migrations land later, `ScriptDirectory.
    get_current_head()` raises `CommandError` — the fast-path
    surfaces the failure loudly before the `==` comparison, doesn't
    silently misbehave. (t7-review 🟢-2.)
    """
    from urllib.parse import urlparse
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

    # Read the migration head ONCE — used by the SQLite shortcut, the
    # fast-path comparison, AND the return value (t8-review 🟡-2).
    script_dir = ScriptDirectory.from_config(config)
    head_rev = script_dir.get_current_head()

    # t7-review 🟡-1: SQLite-file-missing short-circuit. The fast-path
    # check below would open the engine, create the file, connect, see
    # an empty DB, return current_rev=None, and fall through to
    # command.upgrade anyway. Skip the round trip when we already KNOW
    # the file doesn't exist — that's a guaranteed fresh-DB run.
    parsed = urlparse(db_url)
    if parsed.scheme.startswith("sqlite") and parsed.path:
        # sqlite URLs are `sqlite:///abs/path` (parsed.path = `/abs/path`)
        # or `sqlite:///:memory:` (parsed.path = `/:memory:`). The
        # in-memory form has no on-disk file — let the normal path
        # handle it (current_rev=None → real upgrade is correct).
        if parsed.path != "/:memory:" and not Path(parsed.path).exists():
            command.upgrade(config, "head")
            return True, head_rev

    # §16.13.t7: fast-path. Open a short-lived connection, read
    # alembic_version.version_num, compare to the migration scripts'
    # `head` revision. If they match, skip the upgrade. If the table
    # doesn't exist (first-run, populated-but-unmigrated DB), current_rev
    # is None and we fall through to the real upgrade (which creates the
    # table + stamps it).
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_rev = ctx.get_current_revision()
    finally:
        engine.dispose()

    if current_rev is not None and current_rev == head_rev:
        return False, head_rev

    command.upgrade(config, "head")
    return True, head_rev


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


def bootstrap(*, echo: Callable[[str], None] = _silent) -> BootstrapResult:
    """Run the first-run bootstrap. Returns a `BootstrapResult` with
    the resolved paths + what-actually-happened flags.

    Steps (each idempotent):
      1. ensure_home() — mkdir app-home + subdirs.
      2. Set STATION_STUDIO_DB_URL + STATION_STUDIO_STATIONS_DIR via
         `os.environ.setdefault` (existing env wins — tests use this
         to point at tmp_path).
      3. alembic upgrade head against the app-home's DB.
      4. Copy bundled stations into the stations dir, skipping
         already-present files.

    `echo` defaults to silent. CLI callers pass `click.echo` so the
    user sees each step; non-CLI callers (Python API, REST endpoint,
    tests) read the returned dataclass instead of parsing strings —
    §16.13.t8.
    """
    # t8-review 🟡-1: capture first-run state BEFORE ensure_home() runs.
    # Once ensure_home() executes, the dir exists; we'd lose the
    # before-state signal. Check the canonical home (env-resolved) to
    # match what ensure_home() would create.
    was_first_run = not _home.get_home().exists()

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

    # t8-review 🟡-3: derive db_path from the URL that will ACTUALLY be
    # used (which may be an env override pointing elsewhere), not from
    # the home. Falls back to None for non-sqlite URLs.
    resolved_db_url = os.environ["STATION_STUDIO_DB_URL"]
    db_path = _db_path_from_url(resolved_db_url)

    # DB migrations. The bundled alembic.ini's `sqlalchemy.url` is a
    # placeholder; we override it to the resolved app-home URL.
    # §16.13.t7: print whether work was actually done so the user
    # gets honest feedback (rather than "Running migrations..." that
    # silently did nothing).
    echo(f"DB: {db_path if db_path is not None else resolved_db_url}")
    migrations_applied, head_revision = _run_alembic_upgrade(resolved_db_url)
    if migrations_applied:
        echo("Ran migrations (alembic upgrade head).")
    else:
        echo("DB already at head; skipping migrations.")

    # Station copy — only does work on first run.
    copied = _copy_missing_stations(_home.stations_dir())
    if copied:
        echo(f"Copied {copied} example station file(s) into stations dir.")
    else:
        echo("Stations dir already populated; skipping example copy.")

    # §16.14 c2 — starter pool auto-import (D6 + Q8 first-boot guard)
    # + sidecar auto-derive (D8). Both are idempotent + tolerant of
    # missing files; sidecars never overwrite existing ones.
    from engine.database import make_engine, make_sessionmaker
    from engine.models import Base
    from engine.seeding import (
        auto_derive_missing_sidecars, auto_import_starter_pools,
    )

    pools_imported = 0
    db_engine = make_engine()
    Base.metadata.create_all(db_engine)
    SessionLocal = make_sessionmaker(db_engine)
    with SessionLocal() as db:
        pools_imported = auto_import_starter_pools(db, _home.stations_dir())
        db.commit()
    db_engine.dispose()
    if pools_imported:
        echo(
            f"Auto-imported {pools_imported} starter pool(s) "
            "(first-run only)."
        )
    else:
        echo("Sources already present (or no starter pools bundled); "
             "skipping auto-import.")

    sidecars_derived = auto_derive_missing_sidecars(_home.stations_dir())
    if sidecars_derived:
        echo(f"Auto-derived {sidecars_derived} editor sidecar(s).")

    return BootstrapResult(
        home=home,
        db_url=resolved_db_url,
        db_path=db_path,
        stations_dir=_home.stations_dir(),
        was_first_run=was_first_run,
        migrations_applied=migrations_applied,
        head_revision=head_revision,
        stations_copied=copied,
        pools_auto_imported=pools_imported,
        sidecars_derived=sidecars_derived,
    )


def _db_path_from_url(db_url: str) -> Path | None:
    """§16.13.t8 review 🟡-3: parse the filesystem path out of a
    SQLite URL. Returns None for non-sqlite schemes (postgres, mysql,
    in-memory sqlite) — the BootstrapResult's `db_path` is `None` in
    those cases since "the DB lives over the network / in-process"
    isn't a meaningful filesystem path.

    Uses sqlalchemy's `make_url` instead of `urllib.parse.urlparse`
    so the SQLite 4-slash absolute-path form
    (`sqlite:////abs/path`) is decoded correctly. urlparse leaves a
    spurious leading `//` on `.path` for that form; `make_url` knows
    the dialect's URL grammar and returns the bare filesystem path
    via `.database`.
    """
    from sqlalchemy.engine.url import make_url

    try:
        url = make_url(db_url)
    except Exception:
        return None
    if not url.drivername.startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database)
