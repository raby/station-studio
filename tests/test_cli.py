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


def test_help_lists_all_three_subcommands(runner: CliRunner) -> None:
    """The D5 install-essentials trio is visible to `--help` callers.
    The full list (D5 punt → §16.13.t2) intentionally doesn't appear."""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output
    assert "import" in result.output
    assert "seed" in result.output
    # The §16.13.t2-punted subcommands MUST NOT appear yet — surfacing
    # them as unimplemented would invite confused issues.
    assert "generate" not in result.output
    assert "export" not in result.output
    assert "backup" not in result.output


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
