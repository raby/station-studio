"""§16.13 c3 — first-run bootstrap tests."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
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
    (True, head_revision). Pin the contract."""
    from station_studio.bootstrap import _run_alembic_upgrade
    from station_studio.home import db_url as _resolve_db_url, ensure_home

    # Bootstrap normally creates the home + subdirs before alembic;
    # this test calls the helper directly so it has to mkdir manually.
    ensure_home()
    applied, head_rev = _run_alembic_upgrade(_resolve_db_url())
    assert applied is True
    # t8-review 🟡-2: head_revision threaded out alongside applied bool.
    assert isinstance(head_rev, str) and len(head_rev) >= 8


def test_run_alembic_upgrade_skips_when_already_at_head(
    isolated_home: Path,
) -> None:
    """§16.13.t7 fast-path: second call against an at-head DB returns
    (False, head_revision) without running `command.upgrade`. The
    head_revision is reported on both branches so callers always know
    where the DB ended up."""
    from station_studio.bootstrap import _run_alembic_upgrade
    from station_studio.home import db_url as _resolve_db_url, ensure_home

    ensure_home()
    url = _resolve_db_url()
    applied_1, head_1 = _run_alembic_upgrade(url)
    assert applied_1 is True
    # Same DB, no new migrations land in between — at head now.
    applied_2, head_2 = _run_alembic_upgrade(url)
    assert applied_2 is False
    # head_revision is identical on both branches.
    assert head_2 == head_1


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
    # upgrade actually runs.
    applied, returned_head = _run_alembic_upgrade(_resolve_db_url())
    assert applied is True
    # head was monkeypatched to None — that's what we return.
    assert returned_head is None


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
    # t8-review 🟡-1: home didn't exist before this call → first run.
    assert result.was_first_run is True
    # t8-review 🟡-2: head_revision is the real alembic head id.
    assert isinstance(result.head_revision, str)
    assert len(result.head_revision) >= 8  # alembic revs are 12-hex


def test_bootstrap_result_reflects_second_run(
    isolated_home: Path,
) -> None:
    """A second bootstrap on the same home: migrations skipped (t7
    fast-path) + stations already populated + NOT a first-run + same
    head_revision. The result dataclass surfaces all of these so a
    caller can branch on "did anything actually happen?" without
    re-reading the echo log."""
    first = bootstrap()
    assert first.migrations_applied is True
    assert first.was_first_run is True

    second = bootstrap()
    assert second.migrations_applied is False
    assert second.stations_copied == 0
    # t8-review 🟡-1: home now exists → NOT first run.
    assert second.was_first_run is False
    # t8-review 🟡-2: head_revision identical to first run (same alembic
    # head reported whether we applied or skipped).
    assert second.head_revision == first.head_revision
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
    import io
    import contextlib

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        result = bootstrap()  # no echo= → default _silent
    assert captured.getvalue() == ""  # nothing printed to stdout
    assert result.migrations_applied is True
    assert result.stations_copied > 0


# ---------- §16.13.t8 review 🟡-3 — _db_path_from_url ----------


@pytest.mark.parametrize(
    "url,expected",
    [
        # sqlite-relative + absolute on-disk paths → Path object
        ("sqlite:////Users/raby/foo.db", Path("/Users/raby/foo.db")),
        ("sqlite:////tmp/station.db", Path("/tmp/station.db")),
        # in-memory sqlite → None (no filesystem path)
        ("sqlite:///:memory:", None),
        # non-sqlite schemes → None (no meaningful filesystem path)
        ("postgresql://user@host/db", None),
        ("mysql+pymysql://u:p@h/d", None),
    ],
)
def test_db_path_from_url(url: str, expected) -> None:
    """t8-review 🟡-3: db_path is derived from db_url so the two
    can't diverge under env overrides. Parse SQLite URLs into Path;
    return None for non-sqlite + in-memory (no on-disk file)."""
    from station_studio.bootstrap import _db_path_from_url
    assert _db_path_from_url(url) == expected


def test_bootstrap_db_path_follows_env_override(
    isolated_home: Path, tmp_path, monkeypatch,
) -> None:
    """Integration pin for 🟡-3: when STATION_STUDIO_DB_URL is preset
    to point ELSEWHERE than the home's default, the result's
    `db_path` reflects the OVERRIDE — not the home-derived path.

    Pre-fix `db_path` was hardcoded to `<home>/station_studio.db`,
    silently diverging from `db_url`. Pin the corrected behaviour."""
    elsewhere = tmp_path / "elsewhere.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{elsewhere}")
    result = bootstrap()
    assert result.db_url == f"sqlite:///{elsewhere}"
    assert result.db_path == elsewhere
    # The home is still the isolated_home — only the DB moved.
    assert result.home == isolated_home


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


# ---------- a copy of the database before an update migrates it ----------


def _migrated_to(url: str, revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(bootstrap_module.bundled_path("migrations")))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def _previous_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(bootstrap_module.bundled_path("migrations")))
    head = ScriptDirectory.from_config(config).get_current_head()
    down = ScriptDirectory.from_config(config).get_revision(head).down_revision
    assert isinstance(down, str), "a single previous revision"
    return down


def _revision(db: Path) -> str:
    # closing(): sqlite3's own `with` ends a transaction, not the connection.
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


@pytest.fixture
def older_database(isolated_home: Path) -> Path:
    """The app-home's database as the version before this one left it."""
    from station_studio.home import db_path, db_url, ensure_home

    ensure_home()
    _migrated_to(db_url(), _previous_revision())
    return db_path()


def test_an_update_copies_the_database_before_migrating_it(older_database: Path) -> None:
    said: list[str] = []
    result = bootstrap(echo=said.append)

    copy = result.copy_before_update
    assert copy is not None and copy.parent == older_database.parent / "backups"
    assert copy.name.startswith("before-update-") and copy.suffix == ".db"
    # The copy is the database as it was; the database itself moved on.
    assert _revision(copy) == _previous_revision()
    assert _revision(older_database) == result.head_revision != _previous_revision()
    assert f"Saved a copy of the database before updating it: {copy}" in said
    assert not list(copy.parent.glob("*.part"))


def test_no_copy_when_nothing_existing_is_migrated(isolated_home: Path) -> None:
    first = bootstrap()  # a new database
    again = bootstrap()  # already up to date
    assert first.migrations_applied and first.copy_before_update is None
    assert not again.migrations_applied and again.copy_before_update is None
    assert not (isolated_home / "backups").exists() or not list((isolated_home / "backups").glob("before-update-*"))


def test_an_empty_database_file_isnt_copied(isolated_home: Path) -> None:
    from station_studio.home import db_path, ensure_home

    ensure_home()
    db_path().touch()
    result = bootstrap()
    assert result.migrations_applied and result.copy_before_update is None


def test_if_the_copy_fails_the_database_isnt_migrated(older_database: Path) -> None:
    (older_database.parent / "backups").write_text("a file where the folder would go")
    with pytest.raises(bootstrap_module.CopyBeforeUpdateError) as caught:
        bootstrap()
    assert "so it hasn't been updated" in str(caught.value)
    assert _revision(older_database) == _previous_revision()


def test_the_copy_includes_what_is_still_in_the_write_ahead_log(tmp_path: Path) -> None:
    """Studio's database runs in WAL mode: recent changes can sit in the
    -wal file. SQLite's backup copies them; copying the file wouldn't."""
    db = tmp_path / "studio.db"
    live = sqlite3.connect(db)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("CREATE TABLE t (x)")
    live.execute("INSERT INTO t VALUES ('recent')")
    live.commit()
    assert (tmp_path / "studio.db-wal").stat().st_size > 0
    try:
        copy = bootstrap_module.copy_before_update(db)
        with closing(sqlite3.connect(copy)) as conn:
            assert conn.execute("SELECT x FROM t").fetchall() == [("recent",)]
    finally:
        live.close()


def test_the_last_three_copies_are_kept(tmp_path: Path) -> None:
    import datetime as dt

    db = tmp_path / "studio.db"
    sqlite3.connect(db).close()
    backups = tmp_path / "backups"
    backups.mkdir()
    for stamp in ("20261101-090000", "20261102-090000", "20261103-090000", "20261104-090000"):
        (backups / f"before-update-{stamp}.db").write_text("old")
    mine = backups / "station-studio-20261101-080000.tar.gz"  # `station-studio backup`'s
    mine.write_text("kept")

    now = dt.datetime(2026, 11, 21, 14, 30, 0)
    first = bootstrap_module.copy_before_update(db, now=now)
    second = bootstrap_module.copy_before_update(db, now=now)  # the same second
    assert second.name == "before-update-20261121-143000-2.db"
    assert sorted(f.name for f in backups.iterdir()) == sorted([
        "before-update-20261104-090000.db", first.name, second.name, mine.name,
    ])


def test_a_copy_cut_short_leaves_nothing_behind(tmp_path: Path) -> None:
    db = tmp_path / "studio.db"
    db.write_bytes(b"not a database at all" * 100)
    with pytest.raises(bootstrap_module.CopyBeforeUpdateError):
        bootstrap_module.copy_before_update(db)
    assert list((tmp_path / "backups").iterdir()) == []


def test_an_old_copy_that_wont_go_doesnt_stop_the_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    db = tmp_path / "studio.db"
    sqlite3.connect(db).close()
    backups = tmp_path / "backups"
    backups.mkdir()
    for stamp in ("20261101-090000", "20261102-090000", "20261103-090000"):
        (backups / f"before-update-{stamp}.db").write_text("old")
    real_unlink = Path.unlink

    def stuck(self: Path, missing_ok: bool = False) -> None:
        if self.name == "before-update-20261101-090000.db":
            raise PermissionError("in use")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", stuck)
    with caplog.at_level("WARNING"):
        copy = bootstrap_module.copy_before_update(db)
    assert copy.is_file()
    assert "Couldn't remove an old copy of the database" in caplog.text


def test_only_studios_own_copies_count(tmp_path: Path) -> None:
    """A file someone put there with a similar name isn't one of the
    copies: never removed, and never counted, so three real copies stay.
    A copy a crash cut short (.part) is cleared away."""
    import datetime as dt

    db = tmp_path / "studio.db"
    sqlite3.connect(db).close()
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "before-update-my-copy.db").write_text("someone's own")
    (backups / "before-update-20261101-090000.db.part").write_text("cut short")
    made = [bootstrap_module.copy_before_update(db, now=dt.datetime(2026, 11, 20 + i, 9, 0, 0)) for i in range(4)]
    assert sorted(f.name for f in backups.iterdir()) == sorted(
        [f.name for f in made[1:]] + ["before-update-my-copy.db"])


def test_the_command_line_says_why_without_a_traceback(older_database: Path) -> None:
    from unittest.mock import patch

    from click.testing import CliRunner

    from station_studio.cli import main

    (older_database.parent / "backups").write_text("a file where the folder would go")
    with patch("uvicorn.run") as run:
        result = CliRunner().invoke(main, ["serve"])
    assert result.exit_code == 1
    assert "Error: Couldn't save a copy of the database before updating it" in result.output
    assert "Traceback" not in result.output
    assert not run.called
    assert _revision(older_database) == _previous_revision()
