"""FastAPI app for Station Studio."""
from __future__ import annotations

import importlib.resources
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routers import editor, stations, sources, generate, jobs, meta, rules
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
    # §16.13.t9: source-of-truth is `engine.seeding`. The legacy
    # `scripts.seed_stations` re-exports for backward compat with
    # CLI invocations + older test imports.
    # §16.14 slice-review 🟡-1: also call `auto_import_starter_pools`
    # here so `make dev` (which runs uvicorn directly via run.py,
    # bypassing station_studio.cli.serve + bootstrap) doesn't ship
    # a half-working starter station to dev contributors. The Q8
    # first-boot guard makes this idempotent with the
    # bootstrap-path call (CLI/wheel/docker users hit auto-import
    # first there; the lifespan re-check sees SourcePlaylist.count()
    # > 0 and is a no-op).
    from engine.seeding import (
        _collect_paths, auto_import_starter_pools, seed_all,
    )
    from api.deps import get_stations_dir

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

            # §16.14 slice-review 🟡-1: starter pool auto-import.
            # Internal first-boot guard fires on
            # SourcePlaylist.count() == 0; subsequent boots no-op.
            imported = auto_import_starter_pools(db, get_stations_dir())
            if imported:
                db.commit()
                logger.info(
                    "auto-imported %d starter pool(s) (first-run only).",
                    imported,
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

# §16.2.2.t3 (post-c5 sweep) — gzip-compress responses ≥1KB. Driven
# by the visualise endpoint's synthesized-trivial sidecar: Rebirth
# Radio's 2,196-atomic-block JSON is ~100KB raw / ~10KB gzipped.
# Compresses any JSON response above the threshold; small responses
# (most CRUD endpoints, GET /api/jobs/{id}, etc.) skip the overhead.
# Browsers + httpx both send `Accept-Encoding: gzip` by default;
# starlette's middleware no-ops when the client doesn't.
#
# Registered BEFORE CORS so gzip sits INSIDE the CORS wrapper at
# request time. `add_middleware` registers in reverse-wrap order:
# last-registered = outermost. CORS-outermost is the conventional
# layering — CORS headers wrap the already-compressed body, so a
# preflight that reads response metadata sees coherent
# Content-Length etc. (§16.2.2.t3 review 🟡-1).
app.add_middleware(GZipMiddleware, minimum_size=1000)

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
app.include_router(jobs.router)
app.include_router(editor.router)
app.include_router(meta.router)
app.include_router(rules.router)


# §16.13 c2 — serve the SvelteKit SPA at / (catch-all under /api/*).
#
# Resolution order for the built static assets:
#   1. Wheel-bundled (`station_studio/_bundled/web/`) — c4 will plumb
#      the npm-then-python build orchestration that populates this.
#   2. In-repo (`<repo>/web/build/`) — present after `npm run build`,
#      the `make dev` / Playwright preview path.
#   3. Neither → log a warning + leave `GET /` returning the
#      service-identity blob. That's the dev-loop case (vite dev on
#      :5173 serves the UI; FastAPI on :8001 is API-only). The blob
#      also keeps `test_root_endpoint_identifies_the_service` green
#      in CI where no static build runs.
#
# The order is intentional: an installed wheel's bundled assets win
# over a stale in-repo `web/build/` if both exist (the installed
# wheel is the canonical artifact in that case).
def _resolve_static_dir() -> Path | None:
    try:
        bundled = importlib.resources.files("station_studio") / "_bundled" / "web"
        if bundled.is_dir():
            return Path(str(bundled))
    except (ModuleNotFoundError, AttributeError):
        # `station_studio` isn't installed yet (in-repo dev pre-pip-install).
        pass
    # §16.13.t5: pyproject.toml sanity-check on the walked-up root.
    # Without the check, a future move of api/main.py to e.g.
    # `api/server/main.py` would silently resolve `parent.parent` to
    # `api/` (not the repo root), `api/web/build/` would obviously
    # not exist, and the function would return None — making the SPA
    # mount silently disappear. With the check, depth drift short-
    # circuits to None at the guard, no surprise behaviour.
    repo_root = _resolve_repo_root()
    if repo_root is None:
        return None
    in_repo = repo_root / "web" / "build"
    if in_repo.is_dir():
        return in_repo
    return None


def _resolve_repo_root() -> Path | None:
    """§16.13.t5 (+ post-t5-review 🟡-1) — depth-adaptive, sanity-
    checked walk. Mirrors `station_studio/bootstrap.py:_resolve_repo_root`
    line-for-line. Inlined (not imported from station_studio) because
    api/ shouldn't depend on station_studio for what's essentially a
    one-line filesystem check, and the dependency direction is
    currently `station_studio → api`, not the other way.

    Walks `Path.parents` until pyproject.toml appears so a future
    move of api/main.py (deeper subpackage, layout reorg) doesn't
    need a coordinated `parent.parent → parent.parent.parent` patch.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


STATIC_DIR = _resolve_static_dir()


def attach_spa_routes(app: FastAPI, static_dir: Path) -> None:
    """Wire SvelteKit's SPA build into `app` from `static_dir`.

    Three routes get added:
      * `GET /` → index.html (the SPA entry HTML).
      * `GET /_app/...` → built JS/CSS/fonts via StaticFiles (etag,
        Cache-Control, range support).
      * `GET /{anything}` → real file if it exists (e.g. /favicon.ico),
        otherwise index.html (SPA fallback — client-side router takes
        over once the bundle boots).

    Extracted as a function so tests can mount the SPA against a
    fake `static_dir` without monkeypatching module-level state.

    c2 mid-slice review 🟡-1: index.html is served with
    `Cache-Control: no-cache`. SvelteKit emits content-hashed asset
    filenames under `_app/immutable/` so those can cache forever
    (StaticFiles handles their headers), but index.html REFERENCES
    those hashed names — a stale-cached index.html after a deploy
    would point at old hashes that 404 on the new server, leaving
    the user in a broken state until hard-refresh. `no-cache` tells
    browsers to revalidate every load.
    """
    # Caller-visible constant — also referenced by the SPA-fallback
    # branch below so both `index.html` serves are headered identically.
    _NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}

    app.mount(
        "/_app",
        StaticFiles(directory=static_dir / "_app"),
        name="spa-assets",
    )

    @app.get("/", include_in_schema=False)
    async def spa_index() -> FileResponse:
        return FileResponse(
            static_dir / "index.html", headers=_NO_CACHE_HEADERS,
        )

    # Catch-all SPA fallback. Registered LAST so /api/* routers + the
    # /_app mount + explicit / handler all match first. The
    # `{path:path}` converter swallows slashes — `/stations/foo/bar`
    # all routes to here.
    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa_fallback(spa_path: str) -> FileResponse:
        # Defensive: an /api/* path shouldn't reach this — every
        # router uses the `/api/` prefix and routers match first. But
        # if a router IS missing a route, returning the SPA shell
        # would be a confusing 200-shaped 404. Surface as a real 404.
        if spa_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # Real file (e.g. `/favicon.ico`) — serve it. No explicit cache
        # header; FileResponse's default (no Cache-Control) lets the
        # browser apply its heuristics, which is fine for static
        # top-level files since they rarely change.
        target = static_dir / spa_path
        if target.is_file():
            return FileResponse(target)
        # Unknown path → SPA route → client-side router handles it
        # once the bundle boots from index.html. `no-cache` for the
        # same reason as the spa_index() root: index.html references
        # hashed asset names that shift with each deploy.
        return FileResponse(
            static_dir / "index.html", headers=_NO_CACHE_HEADERS,
        )


if STATIC_DIR is not None:
    attach_spa_routes(app, STATIC_DIR)
else:
    logger.info(
        "no static frontend assets found (looked under bundled wheel + "
        "<repo>/web/build/). Falling back to API-only mode at `/`. "
        "Run `npm run build` in `web/` to enable single-process serving."
    )

    @app.get("/")
    def root() -> dict:
        return {"service": "station-studio", "version": "0.1.0"}
