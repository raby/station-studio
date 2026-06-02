"""§16.13.t4 — verify scripts/check_deps_parity.py works AND that the
repo's actual dep surfaces are in sync.

Two tests:
  1. The repo's CURRENT pyproject + requirements files are in parity.
     If you bump fastapi in one place but forget the other, this
     fails INSIDE pytest before it fails in CI.
  2. The checker correctly detects synthetic drift via a tmp_path
     scenario — proves the script's diff logic, not just its
     happy-path exit code.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_deps_parity.py"


def test_real_repo_pyproject_and_requirements_in_parity() -> None:
    """Run the checker against the live repo state. The CI step does
    the same thing — having a pytest mirror means contributors who
    bump deps locally see the failure in `pytest` before pushing."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"check_deps_parity reported drift in the live repo:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "OK" in result.stdout


def test_checker_detects_synthetic_runtime_drift(
    tmp_path: Path, monkeypatch
) -> None:
    """Drop a deliberately-broken pyproject + requirements pair into a
    tmp_path repo + run the checker. The drift must surface as exit 1
    + a diff naming the missing package.

    Approach: copy the script to tmp_path so its `REPO_ROOT` resolves
    to a sandbox we control. Avoids any chance of false-passing
    against the real repo's files.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "scripts").mkdir()

    # Copy the script verbatim — its REPO_ROOT calculation
    # (`__file__.parent.parent`) resolves to `sandbox` because of
    # where we put it.
    target_script = sandbox / "scripts" / "check_deps_parity.py"
    target_script.write_text(SCRIPT.read_text())

    # Drop drift-inducing files: pyproject has fastapi, requirements
    # doesn't (the inverse of the bug t4 was queued for).
    (sandbox / "pyproject.toml").write_text(
        '[project]\n'
        'name = "sandbox"\n'
        'version = "0"\n'
        'dependencies = ["fastapi>=0.110", "click>=8.1"]\n'
        '[project.optional-dependencies]\n'
        'dev = ["pytest>=8.0"]\n'
    )
    (sandbox / "requirements.txt").write_text(
        "click>=8.1\n"   # missing fastapi vs pyproject above
    )
    (sandbox / "requirements-dev.txt").write_text(
        "-r requirements.txt\n"
        "pytest>=8.0\n"
    )

    result = subprocess.run(
        [sys.executable, str(target_script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, (
        f"checker missed the drift; exit={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "DRIFT" in result.stdout
    assert "fastapi" in result.stdout
    # Should specifically call out fastapi as "pyproject only".
    assert "pyproject only" in result.stdout


def test_checker_handles_missing_files(tmp_path: Path) -> None:
    """Sanity: running against a sandbox missing pyproject.toml exits
    non-zero with a clear error message (not a traceback)."""
    sandbox = tmp_path / "empty"
    sandbox.mkdir()
    (sandbox / "scripts").mkdir()
    target_script = sandbox / "scripts" / "check_deps_parity.py"
    target_script.write_text(SCRIPT.read_text())
    # No pyproject.toml + no requirements.txt — the script should
    # exit 1 with a "not found" message via sys.exit().

    result = subprocess.run(
        [sys.executable, str(target_script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    # Either stdout or stderr carries the message (sys.exit(str) goes
    # to stderr).
    combined = result.stdout + result.stderr
    assert "not found" in combined.lower()
