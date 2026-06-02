"""§16.13 c3 — first-run bootstrap tests."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from station_studio import bootstrap as bootstrap_module
from station_studio.bootstrap import bootstrap


@pytest.fixture
def isolated_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Point STATION_STUDIO_HOME at tmp_path + ensure DB / stations env
    vars are clean at start AND end. Bootstrap calls `os.environ.setdefault`
    which monkeypatch's auto-cleanup doesn't catch (it only restores
    vars IT touched). Explicit teardown prevents pollution leaking into
    later tests (the slot_type backfill tests in particular are
    order-sensitive to STATION_STUDIO_DB_URL)."""
    home = tmp_path / "ss-home"
    monkeypatch.setenv("STATION_STUDIO_HOME", str(home))
    monkeypatch.delenv("STATION_STUDIO_DB_URL", raising=False)
    monkeypatch.delenv("STATION_STUDIO_STATIONS_DIR", raising=False)
    yield home
    # bootstrap() set these via os.environ.setdefault — monkeypatch
    # didn't track them, so clean explicitly.
    os.environ.pop("STATION_STUDIO_DB_URL", None)
    os.environ.pop("STATION_STUDIO_STATIONS_DIR", None)


def test_bootstrap_creates_app_home_and_subdirs(isolated_home: Path) -> None:
    """Bootstrap on a fresh machine: dirs come into existence + the DB
    file lands. Smoke-level coverage of the happy path."""
    bootstrap()
    assert isolated_home.is_dir()
    assert (isolated_home / "stations").is_dir()
    assert (isolated_home / "exports").is_dir()
    assert (isolated_home / "station_studio.db").is_file()


def test_bootstrap_sets_env_vars_for_downstream(isolated_home: Path) -> None:
    """Bootstrap exports STATION_STUDIO_DB_URL + ..._STATIONS_DIR so
    engine.database + scripts.seed_stations all resolve against the
    app-home when called downstream (e.g. uvicorn loading api.main).
    Without this, the wired serve / import / seed paths would silently
    fall back to the repo defaults."""
    bootstrap()
    assert os.environ["STATION_STUDIO_DB_URL"] == (
        f"sqlite:///{isolated_home / 'station_studio.db'}"
    )
    assert os.environ["STATION_STUDIO_STATIONS_DIR"] == str(
        isolated_home / "stations"
    )


def test_bootstrap_runs_alembic_migrations(isolated_home: Path) -> None:
    """`alembic upgrade head` creates the schema. We inspect the SQLite
    directly to assert the expected tables exist + the
    `alembic_version` row was stamped."""
    bootstrap()
    db = sqlite3.connect(isolated_home / "station_studio.db")
    try:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        # A representative set — adjust if the schema baseline shifts.
        assert "station" in tables
        assert "playlist" in tables
        assert "alembic_version" in tables
        # alembic_version row exists (so a re-bootstrap is a no-op
        # rather than re-running migrations).
        version = db.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version is not None
    finally:
        db.close()


def test_bootstrap_copies_bundled_stations_on_first_run(
    isolated_home: Path,
) -> None:
    """First bootstrap with an empty stations dir → bundled examples
    get copied. This is D6's user-visible behaviour: a fresh `pipx`
    install + `station-studio serve` shows Rebirth Classics + Rebirth
    Radio in the sidebar with zero extra steps."""
    bootstrap()
    stations = isolated_home / "stations"
    files = list(stations.glob("*.json"))
    assert len(files) >= 1, "bootstrap should have copied bundled stations"
    # Specifically: the canonical examples.
    names = {p.name for p in files}
    assert "rebirth-classics.json" in names


def test_bootstrap_preserves_user_edits_on_re_run(
    isolated_home: Path,
) -> None:
    """Second `station-studio serve` (or `seed`) does NOT clobber the
    user's edits. We seed once, mutate Classics, run bootstrap again,
    and verify our edit survives."""
    bootstrap()
    classics = isolated_home / "stations" / "rebirth-classics.json"
    assert classics.is_file()
    original = classics.read_text()
    edited = original.replace("Classics", "MY-EDITED-CLASSICS")
    classics.write_text(edited)

    # Re-run bootstrap. The mutation must survive.
    bootstrap()
    assert classics.read_text() == edited


def test_bootstrap_idempotent_on_re_run(isolated_home: Path) -> None:
    """Re-running bootstrap on an existing app-home: no crash, env
    vars stable, no destructive side effects. The `alembic upgrade
    head` no-ops when the DB is already at head."""
    bootstrap()
    first_url = os.environ["STATION_STUDIO_DB_URL"]
    bootstrap()
    second_url = os.environ["STATION_STUDIO_DB_URL"]
    assert first_url == second_url


def test_bundled_path_resolves_for_in_repo_dev() -> None:
    """The in-repo fallback path (no installed wheel) finds the repo's
    own `stations/` + `migrations/` + `alembic.ini`. The wheel-bundled
    path will populate once c4 ships; until then editable installs
    rely on this walk."""
    stations = bootstrap_module.bundled_path("stations")
    assert stations.is_dir()
    assert (stations / "rebirth-classics.json").is_file()

    migrations = bootstrap_module.bundled_path("migrations")
    assert migrations.is_dir()
    assert (migrations / "env.py").is_file()

    ini = bootstrap_module.bundled_path("alembic.ini")
    assert ini.is_file()


def test_bootstrap_echo_callback_is_called(
    isolated_home: Path,
) -> None:
    """The `echo` callable receives one line per major step so the CLI
    can surface progress. Empty default lets non-CLI callers stay
    silent."""
    seen: list[str] = []
    bootstrap(echo=seen.append)
    # Spot-check a few key lines.
    assert any("App home:" in line for line in seen)
    assert any("DB:" in line for line in seen)
    assert any("migrations" in line.lower() for line in seen)


# ---------- §16.13.t7 — skip alembic upgrade when at head ----------


def test_run_alembic_upgrade_returns_true_on_first_run(
    isolated_home: Path,
) -> None:
    """First call against a fresh DB applies migrations and returns
    True (the "did work" signal). Pin the contract."""
    from station_studio.bootstrap import _run_alembic_upgrade
    from station_studio.home import db_url as _resolve_db_url, ensure_home

    # Bootstrap normally creates the home + subdirs before alembic;
    # this test calls the helper directly so it has to mkdir manually.
    ensure_home()
    did_work = _run_alembic_upgrade(_resolve_db_url())
    assert did_work is True


def test_run_alembic_upgrade_skips_when_already_at_head(
    isolated_home: Path,
) -> None:
    """§16.13.t7 fast-path: second call against an at-head DB returns
    False without running `command.upgrade`. This is the cron'd
    batch-import optimisation — saves ~100ms × N invocations."""
    from station_studio.bootstrap import _run_alembic_upgrade
    from station_studio.home import db_url as _resolve_db_url, ensure_home

    ensure_home()
    url = _resolve_db_url()
    first = _run_alembic_upgrade(url)
    assert first is True
    # Same DB, no new migrations land in between — at head now.
    second = _run_alembic_upgrade(url)
    assert second is False


def test_run_alembic_upgrade_falls_through_when_head_rev_is_none(
    isolated_home: Path, monkeypatch,
) -> None:
    """t7-review 🟢-1 edge-case pin: if `script_dir.get_current_head()`
    returns None (migrations dir empty / no scripts), the fast-path
    must NOT skip (the `current_rev is not None and current_rev ==
    head_rev` guard handles it). It falls through to
    `command.upgrade`, which surfaces the real "no migrations found"
    error rather than silently no-op-passing.

    Approach: monkeypatch ScriptDirectory.get_current_head to return
    None against a fresh DB. The function should attempt
    command.upgrade (and either succeed against the real bundled
    migrations OR raise a clear alembic error — either way, NOT
    silently return False from the fast-path)."""
    from station_studio.bootstrap import _run_alembic_upgrade
    from station_studio.home import db_url as _resolve_db_url, ensure_home
    from alembic.script import ScriptDirectory

    ensure_home()
    monkeypatch.setattr(
        ScriptDirectory, "get_current_head", lambda self: None
    )
    # current_rev is None (fresh DB) + head_rev is None (monkeypatched).
    # The guard `current_rev is not None` short-circuits the False
    # return → we fall through to command.upgrade against the REAL
    # bundled migrations (which we didn't monkeypatch away), so the
    # upgrade actually runs + we get True.
    assert _run_alembic_upgrade(_resolve_db_url()) is True


# ---------- §16.13.t8 — BootstrapResult dataclass ----------


def test_bootstrap_returns_structured_result(
    isolated_home: Path,
) -> None:
    """§16.13.t8: `bootstrap()` returns a `BootstrapResult` with
    structured fields, not a bare Path. Non-CLI callers can inspect
    outcome without parsing the printed strings."""
    from station_studio.bootstrap import BootstrapResult

    result = bootstrap()
    assert isinstance(result, BootstrapResult)

    # Paths reflect the isolated_home fixture.
    assert result.home == isolated_home
    assert result.db_path == isolated_home / "station_studio.db"
    assert result.stations_dir == isolated_home / "stations"
    assert result.db_url.startswith("sqlite://")
    assert str(result.db_path) in result.db_url

    # First run: migrations applied + bundled stations copied.
    assert result.migrations_applied is True
    assert result.stations_copied > 0


def test_bootstrap_result_reflects_second_run(
    isolated_home: Path,
) -> None:
    """A second bootstrap on the same home: migrations skipped (t7
    fast-path) + stations already populated. The result dataclass
    surfaces both signals so a caller can branch on "did anything
    actually happen?" without re-reading the echo log."""
    first = bootstrap()
    assert first.migrations_applied is True
    assert first.stations_copied > 0

    second = bootstrap()
    assert second.migrations_applied is False
    assert second.stations_copied == 0
    # Paths stable across runs.
    assert second.home == first.home
    assert second.db_path == first.db_path


def test_bootstrap_result_is_frozen(isolated_home: Path) -> None:
    """The dataclass is a snapshot of what happened — callers
    mutating fields would create a confusing "is this the post-
    bootstrap state or my mutation?" question. Frozen=True
    forbids it; pin the contract so a future un-freeze gets
    challenged."""
    import dataclasses
    result = bootstrap()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.migrations_applied = False  # type: ignore[misc]


def test_bootstrap_no_echo_returns_silent_but_complete_result(
    isolated_home: Path,
) -> None:
    """Non-CLI callers (Python API, REST endpoint, scripts) don't
    need to register an echo callback. The default `_silent` runs
    bootstrap silently but the structured result still tells the
    caller everything that happened."""
    import io, contextlib

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        result = bootstrap()  # no echo= → default _silent
    assert captured.getvalue() == ""  # nothing printed to stdout
    assert result.migrations_applied is True
    assert result.stations_copied > 0


def test_bootstrap_echo_reflects_skip_vs_apply(
    isolated_home: Path,
) -> None:
    """The CLI banner now tells the truth: it says "Ran migrations"
    on the upgrade run + "already at head; skipping" on subsequent
    runs. Pre-fix it said "Running migrations..." both times, which
    was a small but real honesty issue for cron'd callers reading
    the log."""
    seen_first: list[str] = []
    bootstrap(echo=seen_first.append)
    assert any("Ran migrations" in line for line in seen_first)

    seen_second: list[str] = []
    bootstrap(echo=seen_second.append)
    assert any("already at head" in line for line in seen_second)
    # Belt-and-suspenders: the "Ran migrations" string must NOT
    # appear on the second run.
    assert not any("Ran migrations" in line for line in seen_second)
