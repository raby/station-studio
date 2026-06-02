"""§16.13.t5 — repo-root depth sanity check.

Two surfaces walk up from `__file__` to find the repo root:
  * `station_studio/bootstrap.py:_resolve_repo_root` — used by the
    in-repo fallback in `bundled_path()`.
  * `api/main.py:_resolve_repo_root` — used by the in-repo fallback
    in `_resolve_static_dir()`.

Both check that the walked-up candidate contains a `pyproject.toml`.
Without the check, a future move of either file silently produces
the wrong directory, and the failure surfaces opaquely a step later
(`shutil.copytree`, missing SPA mount, etc).

Tests:
  1. Happy path — both helpers find the live repo root (with a
     `pyproject.toml` next to it).
  2. Failure path — monkeypatch `Path(__file__).resolve()` to a
     pyproject-less sandbox; both helpers return None.
  3. Integration — bootstrap's `bundled_path()` raises a CLEAR
     FileNotFoundError when the guard rejects the candidate
     (instead of returning a non-existent path that fails opaquely
     downstream).
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_bootstrap_resolve_repo_root_happy_path() -> None:
    """The live repo has a pyproject.toml — the helper finds it."""
    from station_studio.bootstrap import _resolve_repo_root
    root = _resolve_repo_root()
    assert root is not None
    assert root == REPO_ROOT
    assert (root / "pyproject.toml").is_file()


def test_api_main_resolve_repo_root_happy_path() -> None:
    """Same happy path for api/main.py's helper."""
    from api.main import _resolve_repo_root
    root = _resolve_repo_root()
    assert root is not None
    assert root == REPO_ROOT
    assert (root / "pyproject.toml").is_file()


def test_bootstrap_resolve_repo_root_returns_none_without_pyproject(
    tmp_path, monkeypatch
) -> None:
    """Simulate a future package move where parent.parent lands on a
    directory that ISN'T the repo root (no pyproject.toml). The
    helper must return None, not silently hand back a junk path."""
    import station_studio.bootstrap as boot

    # Build a fake file inside a sandbox with no pyproject at parent^2.
    sandbox = tmp_path / "fake_repo"
    sandbox.mkdir()
    (sandbox / "station_studio").mkdir()
    # No pyproject.toml at `sandbox/`.
    fake_file = sandbox / "station_studio" / "bootstrap.py"
    fake_file.write_text("# fake")

    # Patch the module's `__file__` so the helper's `Path(__file__)`
    # resolves to our sandbox. _resolve_repo_root reads
    # `Path(__file__).resolve().parent.parent` — so we point __file__
    # at the sandbox copy and check the result is None.
    monkeypatch.setattr(boot, "__file__", str(fake_file))
    assert boot._resolve_repo_root() is None


def test_api_main_resolve_repo_root_returns_none_without_pyproject(
    tmp_path, monkeypatch
) -> None:
    """Same negative case for api/main.py's helper."""
    import api.main as main_mod
    sandbox = tmp_path / "fake_repo"
    sandbox.mkdir()
    (sandbox / "api").mkdir()
    fake_file = sandbox / "api" / "main.py"
    fake_file.write_text("# fake")
    monkeypatch.setattr(main_mod, "__file__", str(fake_file))
    assert main_mod._resolve_repo_root() is None


def test_bundled_path_raises_clear_error_when_guard_fails(
    tmp_path, monkeypatch
) -> None:
    """Integration: when the depth-guard rejects the candidate,
    `bundled_path()` raises FileNotFoundError WITH the diagnostic
    pointing at the resolved `__file__`. Pre-fix, this would have
    silently returned a path that later imploded in `shutil.copytree`."""
    import station_studio.bootstrap as boot

    sandbox = tmp_path / "fake_repo"
    sandbox.mkdir()
    (sandbox / "station_studio").mkdir()
    fake_file = sandbox / "station_studio" / "bootstrap.py"
    fake_file.write_text("# fake")
    monkeypatch.setattr(boot, "__file__", str(fake_file))

    # Also need to bypass the importlib.resources branch — install a
    # broken stub so it falls through to the fallback path. The repo's
    # real importlib.resources call would find the real package's
    # _bundled/. We want to test the OTHER branch.
    import importlib.resources as resources

    class _FakeFiles:
        def __truediv__(self, _other):
            raise AttributeError("force fallback")

    def _fake_files(_name):
        return _FakeFiles()

    monkeypatch.setattr(resources, "files", _fake_files)

    with pytest.raises(FileNotFoundError, match="bundled data not found"):
        boot.bundled_path("stations")