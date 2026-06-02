"""§16.13 c1 — CLI entry point tests.

Uses Click's `CliRunner` so the tests run in-process (no subprocess
fork). `serve` is asserted at the dispatch boundary — we mock uvicorn
so the test doesn't actually start a server, then assert the call's
arguments (host, port, workers=1 hardcoded per §16.11).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from station_studio import __version__
from station_studio.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_version_flag_prints_package_version(runner: CliRunner) -> None:
    """`station-studio --version` surfaces the version from
    `station_studio.__version__`. Pin so a bump anywhere shows up
    here (and so a stranger can sanity-check what they installed)."""
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_install_essentials(runner: CliRunner) -> None:
    """The D5 install-essentials trio is visible to `--help`. (Full
    list including generate/export/backup is asserted by
    `test_help_now_lists_all_six_subcommands` at the bottom of this
    file, added in §16.13.t2.)"""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "import" in result.output
    assert "seed" in result.output


def test_serve_invokes_uvicorn_with_workers_1_and_defaults(
    runner: CliRunner,
) -> None:
    """`station-studio serve` calls uvicorn.run with the app target +
    the §16.11 single-worker constraint hardcoded. Defaults match
    `run.py` (127.0.0.1:8001) so muscle memory carries over."""
    with patch("uvicorn.run") as uv:
        result = runner.invoke(main, ["serve"])
    assert result.exit_code == 0
    assert uv.called
    kwargs = uv.call_args.kwargs
    args = uv.call_args.args
    # First positional is the app import string.
    assert args[0] == "api.main:app"
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8001
    # §16.11 constraint: hardcoded; not user-overridable.
    assert kwargs["workers"] == 1
    # Don't reload-watch in installed mode.
    assert kwargs["reload"] is False


def test_serve_respects_host_and_port_flags(runner: CliRunner) -> None:
    """`--host` + `--port` pass through; everything else stays pinned."""
    with patch("uvicorn.run") as uv:
        result = runner.invoke(
            main, ["serve", "--host", "0.0.0.0", "--port", "9999"]
        )
    assert result.exit_code == 0
    kwargs = uv.call_args.kwargs
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9999
    # Workers stays pinned even with flag overrides — there's no
    # `--workers` flag (deliberate).
    assert kwargs["workers"] == 1


def test_serve_has_no_workers_flag(runner: CliRunner) -> None:
    """Belt-and-suspenders against accidentally exposing --workers.
    The constraint is a single-worker invariant; surfacing it as a
    flag would make it look user-configurable. Asserting on the help
    output catches a future regression that adds the flag without
    thinking through §16.11."""
    result = runner.invoke(main, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--workers" not in result.output


def test_serve_logs_startup_banner(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Verbose first-run feedback (the locked default): user sees the
    host:port + the single-worker caveat + the app-home path. Quiet-
    failure mode where the server just starts with no output makes
    debugging needlessly hard."""
    monkeypatch.setenv("STATION_STUDIO_HOME", str(tmp_path / "ss-home"))
    with patch("uvicorn.run"):
        result = runner.invoke(main, ["serve"])
    assert result.exit_code == 0, result.output
    assert "127.0.0.1:8001" in result.output
    assert "single worker" in result.output
    # c3: bootstrap banner exposes the app-home path.
    assert "App home:" in result.output


def test_import_role_validates_choice(runner: CliRunner) -> None:
    """Click's Choice() rejects unknown roles at the CLI layer (same
    422-equivalent as the API's `Literal[content,reference]` Form
    validation in §16.25 c1). Locks the role allowlist."""
    result = runner.invoke(
        main, ["import", "p.xml", "--name", "X", "--role", "banana"]
    )
    assert result.exit_code != 0
    assert "banana" in result.output or "Invalid value" in result.output


# ---- §16.13 c3 — wired subcommands ---------------------------------


@pytest.fixture
def isolated_cli_home(monkeypatch, tmp_path):
    """Run CLI subcommands against a tmp app-home + clean DB env.

    Bootstrap calls `os.environ.setdefault` which monkeypatch's
    auto-cleanup doesn't catch — explicit teardown prevents pollution
    leaking into later tests."""
    import os
    home = tmp_path / "ss-home"
    monkeypatch.setenv("STATION_STUDIO_HOME", str(home))
    monkeypatch.delenv("STATION_STUDIO_DB_URL", raising=False)
    monkeypatch.delenv("STATION_STUDIO_STATIONS_DIR", raising=False)
    yield home
    os.environ.pop("STATION_STUDIO_DB_URL", None)
    os.environ.pop("STATION_STUDIO_STATIONS_DIR", None)


def test_seed_subcommand_populates_stations_dir(
    runner: CliRunner, isolated_cli_home
) -> None:
    """`station-studio seed` on a fresh app-home → bundled stations
    land. End-to-end through the CLI (bootstrap inside)."""
    result = runner.invoke(main, ["seed"])
    assert result.exit_code == 0, result.output
    stations = isolated_cli_home / "stations"
    names = {p.name for p in stations.glob("*.json")}
    assert "rebirth-classics.json" in names
    assert "rebirth-radio.json" in names


def test_seed_subcommand_idempotent_no_crash(
    runner: CliRunner, isolated_cli_home
) -> None:
    """Re-running seed (after the first run populated the dir) succeeds
    quietly + reports "already populated"."""
    r1 = runner.invoke(main, ["seed"])
    assert r1.exit_code == 0
    r2 = runner.invoke(main, ["seed"])
    assert r2.exit_code == 0
    assert "already populated" in r2.output.lower()


def test_import_subcommand_imports_a_pool(
    runner: CliRunner, isolated_cli_home
) -> None:
    """`station-studio import path/to/lib.xml --name X` actually
    imports the pool into the app-home's DB. We assert by hitting
    SQLite directly — quick, no API server, end-to-end coverage of
    the wired path."""
    fixture = (
        Path(__file__).resolve().parent / "fixtures" / "tiny-library.xml"
    )
    assert fixture.is_file()

    result = runner.invoke(
        main, ["import", str(fixture), "--name", "CLI_POOL"]
    )
    assert result.exit_code == 0, result.output
    assert "Imported" in result.output
    assert "CLI_POOL" in result.output

    import sqlite3
    db = sqlite3.connect(isolated_cli_home / "station_studio.db")
    try:
        row = db.execute(
            "SELECT name, role FROM source_playlist WHERE name = ?",
            ("CLI_POOL",),
        ).fetchone()
        assert row == ("CLI_POOL", "content")
    finally:
        db.close()


def test_import_subcommand_rejects_missing_file(
    runner: CliRunner, isolated_cli_home
) -> None:
    """A non-existent PATH exits non-zero with a clear error. Avoids
    a confusing alembic / SQL stack trace when the user just typed
    the path wrong."""
    result = runner.invoke(
        main, ["import", "/does/not/exist.xml", "--name", "X"]
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_import_subcommand_requires_name(
    runner: CliRunner, isolated_cli_home
) -> None:
    """Without --name the import has nowhere to land. Clear early
    error keeps the bootstrap + DB-init from running for nothing."""
    fixture = (
        Path(__file__).resolve().parent / "fixtures" / "tiny-library.xml"
    )
    result = runner.invoke(main, ["import", str(fixture)])
    assert result.exit_code != 0
    assert "--name is required" in result.output


def test_import_subcommand_rejects_whitespace_name(
    runner: CliRunner, isolated_cli_home
) -> None:
    """c3-review 🟡-1: `--name "   "` is a truthy string that slips
    past `if not name`, so without the strip + reject check
    `import_source` would happily persist a whitespace-named pool —
    the same gap §16.25 c1 closed at the API layer. CLI inherits the
    same fix here so all three entry points (API JSON, API multipart,
    CLI) reject the same way.

    (`--name ""` is also rejected, but via the existing `if not name`
    falsy check — different code path, same outcome. Both
    independently asserted below.)"""
    fixture = (
        Path(__file__).resolve().parent / "fixtures" / "tiny-library.xml"
    )
    # Empty string — caught by `if not name` (existing falsy check).
    r1 = runner.invoke(main, ["import", str(fixture), "--name", ""])
    assert r1.exit_code != 0
    assert "required" in r1.output.lower()

    # Whitespace-only — caught by the new strip + reject check.
    r2 = runner.invoke(main, ["import", str(fixture), "--name", "   "])
    assert r2.exit_code != 0
    assert (
        "empty" in r2.output.lower() or "whitespace" in r2.output.lower()
    )

    # Neither should have persisted a source_playlist row.
    import sqlite3
    db_path_ = isolated_cli_home / "station_studio.db"
    if not db_path_.exists():
        # Empty-string case errored before bootstrap created the DB.
        return
    db = sqlite3.connect(db_path_)
    try:
        try:
            rows = db.execute(
                "SELECT name FROM source_playlist"
            ).fetchall()
            assert rows == []
        except sqlite3.OperationalError:
            # source_playlist table didn't even exist — also clean.
            pass
    finally:
        db.close()


# ---------- §16.13.t2 — generate / export / backup tests ----------


@pytest.fixture
def cli_home_with_station_and_pool(
    runner: CliRunner, isolated_cli_home: Path
) -> Path:
    """Seed a one-slot one-pool station into the CLI's app-home so
    generate/export tests can hit a real end-to-end path.

    The fixture station (cli_minimal_station.json) has a single
    `window` block of 3 slots against `TINY_POOL`, which we import
    from tiny-library.xml. Both are stable test fixtures.
    """
    fixture_dir = Path(__file__).resolve().parent / "fixtures"
    station_src = fixture_dir / "cli_minimal_station.json"
    lib_src = fixture_dir / "tiny-library.xml"
    assert station_src.is_file() and lib_src.is_file()

    # Import the pool via the CLI (bootstrap runs inside).
    r = runner.invoke(main, ["import", str(lib_src), "--name", "TINY_POOL"])
    assert r.exit_code == 0, f"pool import failed: {r.output}"

    # Insert the station row via scripts.seed_stations against the
    # imported JSON — same path the api/main.py lifespan uses, so
    # we exercise the production seeding helper directly.
    from engine.database import make_engine, make_sessionmaker
    from engine.models import Base
    from scripts.seed_stations import seed_station

    engine = make_engine()
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    with SessionLocal() as db:
        seed_station(db, station_src)
        db.commit()

    return isolated_cli_home


def test_generate_subcommand_creates_a_playlist(
    runner: CliRunner, cli_home_with_station_and_pool: Path
) -> None:
    """`station-studio generate cli-minimal --week 1` runs the engine
    pipeline + persists a Playlist row. End-to-end through the CLI."""
    result = runner.invoke(
        main, ["generate", "cli-minimal", "--week", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "Generated playlist" in result.output
    assert "cli-minimal" in result.output

    # The 3-slot template should produce exactly 3 entries.
    assert "3 slots" in result.output

    import sqlite3
    db = sqlite3.connect(cli_home_with_station_and_pool / "station_studio.db")
    try:
        n_playlists = db.execute("SELECT COUNT(*) FROM playlist").fetchone()[0]
        assert n_playlists == 1
        n_entries = db.execute(
            "SELECT COUNT(*) FROM playlist_entry"
        ).fetchone()[0]
        assert n_entries == 3
    finally:
        db.close()


def test_generate_subcommand_rejects_unknown_station(
    runner: CliRunner, isolated_cli_home
) -> None:
    """Unknown slug → exit 1 + readable error (not a stack trace)."""
    result = runner.invoke(
        main, ["generate", "nonexistent-station", "--week", "1"]
    )
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_generate_subcommand_rejects_week_zero(
    runner: CliRunner, isolated_cli_home
) -> None:
    """--week < 1 mirrors the API's pydantic ge=1 constraint. Caught
    BEFORE bootstrap to avoid a confusing partial run."""
    result = runner.invoke(
        main, ["generate", "any-slug", "--week", "0"]
    )
    assert result.exit_code != 0


def test_export_subcommand_writes_xml_to_stdout(
    runner: CliRunner, cli_home_with_station_and_pool: Path
) -> None:
    """Default sink is stdout. Pipeline-friendly: `station-studio
    export 1 > playlist.xml` works the same as `--output playlist.xml`.

    Two-step: generate first to create playlist id=1, then export."""
    r_gen = runner.invoke(
        main, ["generate", "cli-minimal", "--week", "1"]
    )
    assert r_gen.exit_code == 0, r_gen.output

    # CliRunner captures stdout; click's buffer.write goes through it.
    result = runner.invoke(main, ["export", "1"])
    assert result.exit_code == 0, result.output
    # iTunes XML starts with the standard plist header.
    assert "<?xml" in result.output
    assert "<plist" in result.output


def test_export_subcommand_writes_xml_to_file(
    runner: CliRunner, cli_home_with_station_and_pool: Path, tmp_path: Path
) -> None:
    """`--output PATH` writes XML to a file + reports byte count."""
    r_gen = runner.invoke(main, ["generate", "cli-minimal", "--week", "1"])
    assert r_gen.exit_code == 0

    target = tmp_path / "out.xml"
    result = runner.invoke(main, ["export", "1", "--output", str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_file()
    assert target.read_bytes().startswith(b"<?xml")
    assert f"Wrote {target}" in result.output


def test_export_subcommand_rejects_missing_playlist(
    runner: CliRunner, isolated_cli_home
) -> None:
    """A non-existent playlist id exits 1 with a readable error
    (not a SQLAlchemy IntegrityError stack)."""
    result = runner.invoke(main, ["export", "99999"])
    assert result.exit_code != 0
    assert "99999" in result.output


def test_export_subcommand_rejects_output_and_to_disk_together(
    runner: CliRunner, isolated_cli_home, tmp_path: Path
) -> None:
    """--output and --to-disk are mutually exclusive sinks. Without
    the early check, you'd get unpredictable behavior (whichever
    code path ran first wins). Caught at usage-validation time."""
    result = runner.invoke(
        main,
        ["export", "1", "--output", str(tmp_path / "x.xml"), "--to-disk"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_backup_subcommand_writes_tarball(
    runner: CliRunner, cli_home_with_station_and_pool: Path
) -> None:
    """`station-studio backup` tars the app-home → default
    home/backups/station-studio-TS.tar.gz. The DB + bundled stations
    are inside; the backups/ subdir itself is NOT (no recursion)."""
    result = runner.invoke(main, ["backup"])
    assert result.exit_code == 0, result.output

    backups_dir = cli_home_with_station_and_pool / "backups"
    archives = list(backups_dir.glob("station-studio-*.tar.gz"))
    assert len(archives) == 1, f"unexpected backup files: {archives}"

    import tarfile
    with tarfile.open(archives[0], "r:gz") as tar:
        members = tar.getnames()
    # The DB must be in the archive (the whole point of the backup).
    assert any(m.endswith("station_studio.db") for m in members), members
    # A bundled station from the seed should be there too.
    assert any(
        m.endswith("rebirth-classics.json") for m in members
    ), members
    # The backups/ subdir + its archive must NOT be in the tar
    # (recursion safety from the _filter function).
    leaked = [m for m in members if "backups" in m.split("/")]
    assert not leaked, f"backups/ subtree leaked into archive: {leaked}"


def test_backup_subcommand_respects_output_flag(
    runner: CliRunner, isolated_cli_home: Path, tmp_path: Path
) -> None:
    """`--output PATH` overrides the default backups/ destination
    so users can land the archive on external media / a specific
    backup-rotation directory."""
    target = tmp_path / "custom-backup.tar.gz"
    result = runner.invoke(main, ["backup", "--output", str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_file()
    # Default backups/ dir should NOT have a stray file in it.
    default_backups = isolated_cli_home / "backups"
    if default_backups.is_dir():
        stray = list(default_backups.glob("station-studio-*.tar.gz"))
        assert not stray, f"default dir got an unexpected backup: {stray}"


def test_help_now_lists_all_six_subcommands(
    runner: CliRunner
) -> None:
    """t2 closes D5's six-subcommand list. `--help` advertises all of
    them; regression catch for a future `@main.command` removal."""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for cmd in ("serve", "import", "seed", "generate", "export", "backup"):
        assert cmd in result.output, f"{cmd!r} missing from --help"
