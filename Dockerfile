# §16.13 c5 — multi-stage docker image.
#
# Three stages on purpose:
#   1. web-build  — node:22 builds the SvelteKit adapter-static SPA
#                   into web/build/. Isolated from the python toolchain;
#                   only its build/ output crosses the stage boundary.
#   2. wheel-build — python:3.12-slim runs `python -m build --wheel`
#                   against the repo, with web/build/ COPY'd in from
#                   stage 1. Produces dist/station_studio-*.whl.
#                   `pyproject.toml`'s force-include block (c4) bakes
#                   web/build/ into the wheel under
#                   station_studio/_bundled/web/.
#   3. runtime    — python:3.12-slim + the wheel installed, non-root
#                   user, /data volume mounted at $STATION_STUDIO_HOME.
#                   Final image is small (no node toolchain, no source
#                   tree — just the wheel + its deps).
#
# Per §16.13 D2: this ships the SAME static frontend as `make wheel`,
# no divergent build path. The whole point of going wheel-first is
# avoiding "the npm build worked in CI but the docker version drifted."
#
# Per §16.13 pre-c1 defaults: python:3.12-slim runtime base.
# (Node-side: node:22-bookworm-slim — newest LTS at slice time, matches
# the version `make install` resolves locally via the dev's Node.)

# ---- stage 1: SvelteKit static SPA build ----
FROM node:22-bookworm-slim AS web-build
WORKDIR /src/web
# Lockfile-first for layer caching: deps rebuild only when lockfile
# changes, not on every source edit.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Outputs to /src/web/build/ thanks to svelte.config.js's
# adapter-static config (c2).
RUN npm run build


# ---- stage 2: Python wheel build ----
FROM python:3.12-slim AS wheel-build
WORKDIR /src
# Hatchling backend + the `build` frontend. --no-cache-dir keeps the
# layer lean; this image is throwaway anyway.
RUN pip install --no-cache-dir build hatchling

# COPY only what hatchling reads. Excluding the unrelated dev
# scaffolding (tests/, web/src/, node_modules/, .venv/) keeps the
# build context honest — anything not listed here can't sneak into
# the wheel by accident.
COPY pyproject.toml README.md LICENSE alembic.ini ./
COPY station_studio/ ./station_studio/
COPY api/ ./api/
COPY engine/ ./engine/
COPY scripts/ ./scripts/
COPY stations/ ./stations/
COPY migrations/ ./migrations/
# The static SPA from stage 1 — landed at the path pyproject's
# force-include block expects (`web/build` → bundled/_web/).
COPY --from=web-build /src/web/build ./web/build

RUN python -m build --wheel --outdir /dist


# ---- stage 3: runtime ----
FROM python:3.12-slim AS runtime

# Non-root user. uvicorn doesn't need root, and station-studio writes
# to $STATION_STUDIO_HOME (a mounted volume) — not the image rootfs —
# so dropping root removes a footgun without losing functionality.
# UID 1000 to align with the common host-user UID for volume-mount
# permission compatibility.
RUN useradd --uid 1000 --create-home --home-dir /home/app --shell /bin/sh app

# c5-review 🟢-2: pin pip to a known-good version so a future
# python:3.12-slim base-image bump can't ship a breaking pip release
# into our build. The pin is loose enough (>= 25.0) to accept security
# patches without rebuilding the image. Upgrade in a deliberate commit
# when we know what's in the new release.
RUN pip install --no-cache-dir --upgrade 'pip>=25.0,<27'

# Install the wheel system-wide. `--no-cache-dir` avoids the pip cache
# layer; the wheel itself gets removed once installed so the final
# image doesn't carry the redundant .whl file.
COPY --from=wheel-build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# App-home lives at /data — a docker volume mount point. The first-run
# bootstrap (c3) creates the DB + copies bundled stations here; on
# subsequent starts it sees a populated dir and skips the seed. Setting
# the env var here means `docker run` with no extra flags Just Works.
ENV STATION_STUDIO_HOME=/data
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

# Listen on the standard port; docker-compose maps host:8001 → container:8001.
EXPOSE 8001

USER app
WORKDIR /home/app

# c5-review 🟡-2: real readiness signal.
#
# Without HEALTHCHECK, `docker compose up` returns "started" the
# moment uvicorn binds, not when the app is actually serving
# requests — a fast user gets connection-refused while the lifespan
# (alembic upgrade + auto-seed) is still running. With it,
# `docker ps` shows `(healthy)`, and any compose stack downstream
# can use `depends_on: condition: service_healthy`.
#
# Probes `/api/stations` (not just `/`) so the check exercises the
# SQLAlchemy + JSON path — the real "I can actually serve" signal.
# Uses Python's urllib (not curl, which isn't in python:3.12-slim)
# so we don't bloat the image with apt-installed extras.
#
# start-period=20s covers cold-boot lifespan (alembic + auto-seed
# of ~3,500 entries); after that, 15s interval + 3-retry tolerates
# transient blips without flapping.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8001/api/stations', timeout=2).status == 200 else 1)" || exit 1

# The CLI entry point shipped by pyproject.toml's [project.scripts].
# CMD's "serve" + the host/port flags can be overridden at run-time
# (e.g. `docker run station-studio:dev import --name DAYTIME pool.xml`)
# without rebuilding.
ENTRYPOINT ["station-studio"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8001"]
