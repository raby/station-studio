"""Pin §16.2.2.t4 review fixes:

  🟡-1 — `_verify_repo_layout` uses `if ... raise`, not `assert`,
        so a `python -O` invocation can't silently disable the
        guard. RuntimeError signals "runtime layout is wrong".
  🟡-2 — the check is extracted to a testable helper. Without
        these tests, a future refactor that re-introduces `assert`
        (or skips the check) silently regresses the fail-fast
        intent.

Tests build temporary directory layouts with `tmp_path` to drive
the failure paths deterministically.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from api.deps import _verify_repo_layout


def test_passes_on_correct_layout(tmp_path):
    """Sibling `engine/` + child `stations/` — happy path."""
    repo = tmp_path
    (repo / "engine").mkdir()
    stations = repo / "stations"
    stations.mkdir()
    _verify_repo_layout(repo, stations)  # no raise


def test_raises_when_engine_sibling_missing(tmp_path):
    """Missing `engine/` → RuntimeError with the misleading-layout
    diagnostic. RuntimeError (not AssertionError) because it
    survives `python -O` stripping."""
    repo = tmp_path
    (repo / "stations").mkdir()
    # NO engine/ dir created.
    with pytest.raises(RuntimeError, match="engine"):
        _verify_repo_layout(repo, repo / "stations")


def test_raises_when_stations_dir_missing(tmp_path):
    """`engine/` is present but `stations/` doesn't exist —
    second guard catches it with its own message."""
    repo = tmp_path
    (repo / "engine").mkdir()
    # NO stations/ dir created.
    with pytest.raises(RuntimeError, match="stations"):
        _verify_repo_layout(repo, repo / "stations")


def test_raises_runtime_error_not_assertion_error(tmp_path):
    """Belt-and-suspenders pin: the failure class is RuntimeError,
    not AssertionError. Catches a future refactor that swaps
    `if ... raise` back to `assert` (which `python -O` strips).
    Without this test, the `-O` regression is invisible to CI."""
    repo = tmp_path
    # No engine/ + no stations/ — fails on the first guard.
    err: BaseException | None = None
    try:
        _verify_repo_layout(repo, repo / "stations")
    except BaseException as e:  # noqa: BLE001 — we want to inspect the class
        err = e
    assert isinstance(err, RuntimeError), (
        f"layout guard raised {type(err).__name__}, expected RuntimeError; "
        f"`assert` statements get stripped by `python -O` so we need "
        f"explicit `if ... raise RuntimeError(...)` instead."
    )


def test_helper_accepts_arbitrary_paths(tmp_path):
    """The helper takes paths as arguments rather than reading
    module globals — so tests can drive it with `tmp_path` and so
    a fork into `my-station-fork/` works without name-based checks.
    This test pins that the check is structural, not name-based:
    a `tmp_path` with no station-studio in its name passes."""
    weird_root = tmp_path / "totally-different-name"
    weird_root.mkdir()
    (weird_root / "engine").mkdir()
    stations = weird_root / "stations"
    stations.mkdir()
    _verify_repo_layout(weird_root, stations)  # no raise — name doesn't matter
