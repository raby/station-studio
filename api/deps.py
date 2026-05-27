"""FastAPI dependency providers."""
from __future__ import annotations

from engine.database import make_engine, make_sessionmaker
from engine.models import Base


_engine = make_engine()
_SessionLocal = make_sessionmaker(_engine)

# Ensure schema exists (development default; real migrations later if needed)
Base.metadata.create_all(_engine)


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
