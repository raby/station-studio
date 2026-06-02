"""FastAPI dependency providers."""
from __future__ import annotations

import os
from pathlib import Path

from engine.database import make_engine, make_sessionmaker
from engine.models import Base


_engine = make_engine()
_SessionLocal = make_sessionmaker(_engine)

# Ensure schema exists (development default; real migrations later if needed)
Base.metadata.create_all(_engine)


# §16.2.2 c1: sidecar files live at `stations/{slug}.editor.json` next
# to the canonical `stations/{slug}.json`. Same directory the seeder +
# bootstrap scripts read from. Path is resolved relative to the repo
# root (api/deps.py → api/ → repo root) so it works both from `make
# dev` (cwd = repo root) and from arbitrary cwds.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATIONS_DIR = _REPO_ROOT / "stations"

# §16.2.2.t4 (post-c5 sweep) — fail-fast against silent brittleness.
# If api/deps.py ever moves deeper into the package, `parent.parent`
# would resolve to the wrong directory. The sibling `engine/` dir is
# a load-bearing presence at the repo root — the whole codebase
# imports from it — so verifying it lives next to `_STATIONS_DIR`
# catches the misconfiguration at import time, with a clear stack
# trace, instead of failing later with "stations directory not
# found" once the editor endpoint is hit.
#
# Repo-name-agnostic on purpose: someone forking the repo into
# `my-station-fork/` should still pass. The checks verify
# STRUCTURE (engine/ sibling, stations/ exists), not naming.
#
# `if ... raise` (not `assert`) because `python -O` strips `assert`
# — and the whole point of the guard is to fail loudly when the
# runtime layout is wrong; `-O` would silently disable it.

def _verify_repo_layout(repo_root: Path, stations_dir: Path) -> None:
    """Raise RuntimeError if the path-derivation produced a layout
    that won't work. Extracted so tests can drive failure cases
    deterministically (tests/test_deps_layout.py).

    §16.13 c4: when `STATION_STUDIO_STATIONS_DIR` is set (the
    installed-wheel path — `station_studio.bootstrap` populates it
    before uvicorn imports this module), the stations-dir check is
    SKIPPED. The env-override is the authoritative source of truth in
    that mode, and the wheel's site-packages layout has no `stations/`
    sibling. The engine-sibling check stays — it's about Python module
    resolution, which IS load-bearing in both modes.
    """
    if not (repo_root / "engine").is_dir():
        raise RuntimeError(
            f"api/deps.py: repo_root={repo_root} doesn't contain an "
            f"`engine/` sibling. Did api/deps.py move deeper into the package?"
        )
    # If the env override is set, defer the stations check to
    # `get_stations_dir()` (which `mkdir(parents=True, exist_ok=True)`s
    # the override path at call time anyway). No need to assert the
    # repo-relative stations/ exists when the operator told us to use
    # a different dir.
    if os.environ.get("STATION_STUDIO_STATIONS_DIR", "").strip():
        return
    if not stations_dir.is_dir():
        raise RuntimeError(
            f"api/deps.py: stations_dir={stations_dir} doesn't exist. "
            f"The `stations/` directory should live at the repo root."
        )


_verify_repo_layout(_REPO_ROOT, _STATIONS_DIR)


def get_db():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """Return the SessionLocal factory. Endpoints that spawn background
    work (§16.11 c3 job runner) need to open fresh sessions INSIDE the
    worker thread — the request's `db` from `get_db()` is closed when
    the endpoint returns. Tests override this dep alongside `get_db` so
    the worker uses the throwaway-tmp test DB, not the dev DB.
    """
    return _SessionLocal


def get_stations_dir() -> Path:
    """Return the directory holding the committed station JSON +
    sidecar files (`stations/{slug}.json`, `stations/{slug}.editor.json`).

    §16.2.2 c1: the editor endpoint reads sidecars from this directory.
    Pytest overrides this dep to a tmp_path so fixtures drop in
    isolation without polluting the repo's committed sidecars.

    §16.2.3 c3: `STATION_STUDIO_STATIONS_DIR` env override. The export
    endpoint WRITES `stations/{slug}.json` here — in a real server
    process (Playwright e2e) there's no dependency-override, so without
    this the e2e would write stray files into the repo's `stations/`.
    The Playwright webServer points it at a throwaway dir. Read at call
    time (not import) so the env applies per-process; the dir is
    created if missing so the first export doesn't 500.
    """
    override = os.environ.get("STATION_STUDIO_STATIONS_DIR", "").strip()
    if override:
        d = Path(override)
        d.mkdir(parents=True, exist_ok=True)
        return d
    return _STATIONS_DIR
