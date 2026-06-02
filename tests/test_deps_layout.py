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


def test_raises_when_stations_dir_missing(tmp_path, monkeypatch):
    """`engine/` is present but `stations/` doesn't exist —
    second guard catches it with its own message.

    Monkeypatch ensures STATION_STUDIO_STATIONS_DIR isn't set (the
    c4 env-skip path is asserted separately below)."""
    monkeypatch.delenv("STATION_STUDIO_STATIONS_DIR", raising=False)
    repo = tmp_path
    (repo / "engine").mkdir()
    # NO stations/ dir created.
    with pytest.raises(RuntimeError, match="stations"):
        _verify_repo_layout(repo, repo / "stations")


def test_skips_stations_check_when_env_override_set(
    tmp_path, monkeypatch
):
    """§16.13 c4: when STATION_STUDIO_STATIONS_DIR is set (the
    installed-wheel path — `station_studio.bootstrap` populates it),
    the missing repo-relative stations/ is intentional and shouldn't
    raise. The env override is the authoritative source of truth in
    that mode."""
    repo = tmp_path
    (repo / "engine").mkdir()
    # NO stations/ dir at repo root.
    monkeypatch.setenv("STATION_STUDIO_STATIONS_DIR", str(tmp_path / "elsewhere"))
    # Should NOT raise even though `repo / "stations"` doesn't exist.
    _verify_repo_layout(repo, repo / "stations")


def test_engine_sibling_check_still_runs_with_env_override(
    tmp_path, monkeypatch
):
    """Engine-sibling check is about Python module resolution — it's
    load-bearing in BOTH in-repo and installed-wheel modes. The env
    override shouldn't disable it."""
    repo = tmp_path  # no engine/ subdir
    monkeypatch.setenv("STATION_STUDIO_STATIONS_DIR", str(tmp_path / "elsewhere"))
    with pytest.raises(RuntimeError, match="engine"):
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


# ---------- §16.2.3 c3: STATION_STUDIO_STATIONS_DIR env override ----------


def test_get_stations_dir_defaults_to_repo_stations(monkeypatch):
    """No env override → the repo's stations/ dir (the committed
    sidecars live there)."""
    monkeypatch.delenv("STATION_STUDIO_STATIONS_DIR", raising=False)
    from api.deps import get_stations_dir, _STATIONS_DIR
    assert get_stations_dir() == _STATIONS_DIR


def test_get_stations_dir_env_override_creates_and_returns(tmp_path, monkeypatch):
    """§16.2.3 c3 isolation fix: STATION_STUDIO_STATIONS_DIR redirects
    the editor read/write dir (so the Playwright e2e's export endpoint
    doesn't litter the repo's committed stations/). The dir is created
    if missing so the first export doesn't 500."""
    target = tmp_path / "throwaway-stations"
    assert not target.exists()
    monkeypatch.setenv("STATION_STUDIO_STATIONS_DIR", str(target))
    from api.deps import get_stations_dir
    out = get_stations_dir()
    assert out == target
    assert target.is_dir()  # auto-created
