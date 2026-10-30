"""The docs build's two dump scripts (`make docs-openapi`, `make docs-cli`).

The docs workflow only runs on pushes to main, so without these a PR
that breaks either script would only fail after it merged.

Each script runs in its own Python process, pointed at a temp file
instead of docs/reference/. In-process would leak: both scripts set
STATION_STUDIO_DB_URL to sqlite:///:memory: and then import api.main /
station_studio.cli, which build their DB engine from it at import time,
and later tests would get that engine. Coverage still counts these runs
(`patch = subprocess` in .coveragerc).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Runs scripts.<name>.main() with _OUT_PATH swapped for argv[2].
_RUN = (
    "import importlib, pathlib, sys\n"
    "mod = importlib.import_module('scripts.' + sys.argv[1])\n"
    "mod._OUT_PATH = pathlib.Path(sys.argv[2])\n"
    "sys.exit(mod.main())\n"
)


def _run_dump(name: str, out: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _RUN, name, str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_dump_openapi_writes_the_api_schema(tmp_path):
    out = tmp_path / "openapi.json"
    stdout = _run_dump("dump_openapi", out)

    schema = json.loads(out.read_text(encoding="utf-8"))
    assert schema["openapi"].startswith("3.")
    assert "/api/stations" in schema["paths"]
    assert f"({len(schema['paths'])} paths)" in stdout


def test_dump_cli_help_writes_every_subcommand(tmp_path):
    from station_studio.cli import main as cli_main

    out = tmp_path / "cli.md"
    stdout = _run_dump("dump_cli_help", out)

    page = out.read_text(encoding="utf-8")
    assert page.startswith("# CLI reference")
    assert "## `station-studio --help`" in page
    for cmd in cli_main.commands:
        assert f"## `station-studio {cmd} --help`" in page
    assert f"{len(cli_main.commands)} subcommands" in stdout
