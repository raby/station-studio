"""FastAPI dependency providers."""
from __future__ import annotations

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
    Tests override to point at a tmp_path so they can drop fixtures
    in isolation without polluting the repo's committed sidecars.
    """
    return _STATIONS_DIR
