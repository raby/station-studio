"""FastAPI app for Station Studio."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import stations, sources, generate, meta, rules
from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot-time tasks.

    Auto-seed `stations/*.json` on FIRST boot when the Station table
    is empty. README's "Quick start" promises that two example
    stations (Rebirth Classics + Rebirth Radio) appear after
    `make install && make dev` with zero extra steps — this handler
    is what makes the promise true.

    Behaviour:

      * Empty Station table → seed every `stations/*.json` (skips
        files matching `*.pool-manifest.json` per scripts/seed_stations.py).
      * Non-empty Station table → no-op. Re-seeding existing rows
        risks clobbering user edits; the explicit `make
        seed-stations` flow handles that on demand.

    Errors during auto-seed log a warning but DO NOT crash the
    app — the user can still recover by running `make seed-stations`
    or fixing the broken JSON. The API must come up so the user can
    investigate.

    The handler runs synchronously inside `lifespan`. For a single-
    user local app with 2-3 stations this is fine (<100ms). When
    multi-station scale arrives, consider deferring to a background
    task. Today, single-user-tool pragmatism wins.
    """
    import os

    # Test opt-out — most tests want a clean empty Station table.
    # Production NEVER sets this; the api fixture in tests/test_api.py
    # sets it so per-test fixtures aren't polluted by example stations.
    # Specific tests targeting the auto-seed behaviour use a different
    # fixture path that doesn't set this var.
    if os.environ.get("STATION_STUDIO_SKIP_AUTO_SEED", "").strip() == "1":
        yield
        return

    # Import lazily so test setups that pass their own session
    # don't need lifespan to fire.
    from scripts.seed_stations import seed_all, _collect_paths

    try:
        engine = make_engine()
        Base.metadata.create_all(engine)
        SessionLocal = make_sessionmaker(engine)
        with SessionLocal() as db:
            existing_count = db.query(Station).count()
            if existing_count == 0:
                paths = _collect_paths([])  # default glob: stations/*.json
                if paths:
                    summaries = seed_all(db, paths)
                    logger.info(
                        "auto-seeded %d station(s): %s",
                        len(summaries),
                        ", ".join(s["slug"] for s in summaries),
                    )
                else:
                    logger.info(
                        "no stations/*.json found; skipping auto-seed."
                    )
            else:
                logger.debug(
                    "Station table non-empty (%d rows); skipping auto-seed.",
                    existing_count,
                )
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(
            "auto-seed failed (%s); continuing. Run `make seed-stations` "
            "manually to recover.", e,
        )

    yield


app = FastAPI(
    title="Station Studio",
    description="Generalised radio-station playlist generator. See DESIGN.md.",
    version="0.1.0",
    lifespan=lifespan,
)

# SvelteKit dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stations.router)
app.include_router(sources.router)
app.include_router(generate.router)
app.include_router(meta.router)
app.include_router(rules.router)


@app.get("/")
def root():
    return {"service": "station-studio", "version": "0.1.0"}
