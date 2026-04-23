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
