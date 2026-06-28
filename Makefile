# station-studio — convenience entry points for the test matrix.
#
# Three layers, each independently runnable:
#   make test-backend       pytest + coverage gate (Python)
#   make test-frontend-unit vitest (api.ts + component tests)
#   make test-e2e           playwright (UI ↔ API roundtrip)
#
# `make test` runs all three in order; CI does the same.

.PHONY: install test test-backend test-frontend-unit test-e2e install-e2e-browsers clean-test \
        migrate alembic-stamp alembic-revision alembic-current migration-parity \
        backfill-slot-types \
        backup backups restore \
        dev dev-backend dev-frontend \
        import-station import-weekly-pools refresh-rebirth-radio \
        refresh-port-fixture refresh-port-everything refresh-classics-fixture \
        generate-user-playlists \
        derive-sidecar

PY := .venv/bin/python
ALEMBIC := .venv/bin/alembic
DB_FILE := station_studio.db

# One-shot bootstrap for a fresh clone. README's "Quick start" calls
# this. Idempotent: re-running on a populated checkout is safe.
#
#   * Creates .venv if missing (Python 3.10+ required).
#   * Installs Python deps (runtime + dev). pip pinning is loose
#     today; pinning lands when the hobbyist-starter slice ships.
#   * Installs frontend deps via `npm ci` (uses package-lock.json
#     for reproducibility).
#
# DOES NOT seed the stations table — that happens automatically on
# first uvicorn boot (api/main.py lifespan handler) when the
# database is empty. So `make install && make dev` is the full
# "fresh clone to running app" path. See README's Quick start.
install:
	@if [ ! -d .venv ]; then \
		echo "Creating .venv …"; \
		python3 -m venv .venv; \
	fi
	@echo "Installing Python deps …"
	@.venv/bin/pip install --quiet --upgrade pip
	@.venv/bin/pip install --quiet -r requirements.txt -r requirements-dev.txt
	@echo "Installing frontend deps …"
	@cd web && npm ci --silent
	@echo "Done. Run \`make dev\` to start the app."

test: test-backend test-frontend-unit test-e2e

# Backend: pytest + fail-under so coverage can't silently drop. Threshold
# is intentionally loose — engine/api are ~100%, but bootstrap_classics
# (66%) and seed_stations (69%) are CLI entry-points whose argparse +
# print branches don't test cleanly. With those bringing the aggregate
# down, 90% leaves real headroom; the gate fires on a *regression*, not
# on the resting state. Per-file enforcement happens at the Vitest layer.
test-backend:
	$(PY) -m coverage run -m pytest
	$(PY) -m coverage report --fail-under=90

# Frontend: vitest with --coverage so the per-file lines threshold + aggregate
# statements/functions/branches thresholds in vite.config.js fire. Without
# --coverage vitest skips the report and the gate never runs.
test-frontend-unit:
	cd web && npm run test:unit -- --coverage

test-e2e:
	cd web && npm run test:e2e

# One-shot: fetches the chromium binary Playwright needs.
install-e2e-browsers:
	cd web && npx playwright install chromium

clean-test:
	rm -f .coverage playwright-test.*.db playwright-test.*.db-wal playwright-test.*.db-shm
	rm -rf web/test-results web/playwright-report

# §16.13 c4 — produce the installable wheel.
#
# Two-step orchestration: build the SvelteKit static SPA into
# `web/build/` FIRST, then `python -m build` packs everything (Python
# packages + bundled stations + bundled migrations + the freshly-
# built web assets) into `dist/station_studio-*.whl`.
#
# Skipping the npm step would fail loudly: pyproject's force-include
# block references `web/build/` and hatchling errors with "directory
# not found." That's deliberate — a wheel without the static assets
# would install but serve a broken UI to its users.
#
# Cleans `dist/` first so old wheels don't accumulate. Doesn't clean
# `web/build/`; Vite's cache keeps incremental rebuilds fast.
wheel:
	@echo "==> Building SvelteKit static SPA (web/build/)..."
	@cd web && npm run build
	@echo "==> Building Python wheel (dist/)..."
	@rm -rf dist
	@$(PY) -m build --wheel
	@echo "==> Built: $$(ls dist/*.whl)"

# Smoke-test the just-built wheel in a throwaway venv. The release-
# blocker check: prove `pip install <wheel>` → `station-studio serve`
# yields a working UI + API in a fresh environment with NO repo
# checkout, NO npm, NO .venv. Catches:
#   - wheel malformed / console script missing (`--version`)
#   - bundled-asset force-include broken (`/` returning text/html)
#   - api/main.py lifespan crashes on startup (auto-seed import,
#     alembic migrate, etc — manifests as /api/stations not 200)
#   - SPA fallback wiring broken (`/sources` not 200 text/html)
#
# Uses port 8765 (high, unlikely-collision); fails fast if it can't
# bind. Trap-cleans the tmp venv + kills the bg uvicorn even on
# mid-flight failure — c4-review 🟡-3 fix (matches the c4 §14.36
# refresh-rebirth-radio trap pattern).
#
# Interpreter: reuses `$(PY)`'s sys.executable so the venv is built
# with the same Python the dev workflow uses (c4-review 🟢-1 — avoids
# `python3` resolving to a sub-3.11 binary on multi-Python boxes).
wheel-smoke: wheel
	@echo "==> Smoke-installing wheel into a temp venv..."
	@PYTHON_BIN=$$($(PY) -c 'import sys; print(sys.executable)'); \
	TMPVENV=$$(mktemp -d); \
	SMOKE_PID=""; \
	trap 'if [ -n "$$SMOKE_PID" ]; then kill $$SMOKE_PID 2>/dev/null || true; fi; rm -rf $$TMPVENV' EXIT INT TERM; \
	set -e; \
	$$PYTHON_BIN -m venv $$TMPVENV/venv; \
	$$TMPVENV/venv/bin/pip install --quiet dist/station_studio-*.whl; \
	echo "--- station-studio --version ---"; \
	$$TMPVENV/venv/bin/station-studio --version; \
	echo "--- launching serve on :8765 ---"; \
	STATION_STUDIO_HOME=$$TMPVENV/home \
		$$TMPVENV/venv/bin/station-studio serve --host 127.0.0.1 --port 8765 \
		>$$TMPVENV/serve.log 2>&1 & \
	SMOKE_PID=$$!; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do \
		if curl -sf http://127.0.0.1:8765/api/stations >/dev/null 2>&1; then echo "  ready after $${i}s"; break; fi; \
		sleep 1; \
	done; \
	echo "--- curl / (SPA shell) ---"; \
	curl -sS -o /dev/null -w 'HTTP %{http_code} %{content_type}\n' http://127.0.0.1:8765/ | tee /dev/stderr | grep -q '^HTTP 200 text/html'; \
	echo "--- curl /api/stations ---"; \
	curl -sS -o /dev/null -w 'HTTP %{http_code} %{content_type}\n' http://127.0.0.1:8765/api/stations | tee /dev/stderr | grep -q '^HTTP 200 application/json'; \
	echo "--- /api/stations: lists all four bundled stations ---"; \
	BODY=$$(curl -sS http://127.0.0.1:8765/api/stations); \
	for slug in starter stateful-starter rebirth-classics rebirth-radio; do \
		echo "$$BODY" | grep -q "\"$$slug\"" || { echo "ERROR: $$slug missing from /api/stations response" >&2; exit 1; }; \
	done; \
	echo "  ✓ all four bundled stations present"; \
	echo "--- curl /sources (SPA fallback) ---"; \
	curl -sS -o /dev/null -w 'HTTP %{http_code} %{content_type}\n' http://127.0.0.1:8765/sources | tee /dev/stderr | grep -q '^HTTP 200 text/html'; \
	echo "--- curl /api/sources (all 4 auto-imported pools) ---"; \
	SOURCES=$$(curl -sS http://127.0.0.1:8765/api/sources); \
	for pool in STARTER_CONTENT STARTER_JINGLES STATEFUL_CONTENT REFERENCE_ARCHIVE; do \
		echo "$$SOURCES" | grep -q "$$pool" || { echo "FAIL: $$pool missing from /api/sources — bootstrap auto-import is broken" >&2; exit 1; }; \
	done; \
	echo "$$SOURCES" | grep -q '"role":"reference"' || { echo "FAIL: no role=reference pool in /api/sources — §16.14.t3 REFERENCE_ARCHIVE didn't import with the right role" >&2; exit 1; }; \
	echo "  ✓ all four pools present; reference role tag verified"; \
	echo "==> Smoke OK."

# §16.13 c5 — build the docker image.
#
# Multi-stage Dockerfile (web build → wheel build → runtime). The
# build context is filtered by .dockerignore so the daemon doesn't
# receive .venv / node_modules / *.db etc. Tag is `station-studio:dev`
# (compose's docker-compose.yml `image:` field matches this) so `make
# image` produces what `docker compose up` would use.
image:
	@echo "==> Building docker image (station-studio:dev)..."
	docker build --tag station-studio:dev .
	@echo "==> Built: station-studio:dev"

# Smoke-test the image: bring it up, wait for /api/stations to be
# 200, curl the same three URLs `make wheel-smoke` does (SPA shell +
# JSON + SPA fallback). Same release-blocker logic as the wheel
# smoke, applied to the containerised path.
#
# Uses port 18001 host-side to avoid colliding with `make dev`'s 8001
# if both happen to be running. trap cleans the container on any exit
# (success, mid-flight failure, ctrl-C).
image-smoke: image
	@echo "==> Running container + curl smoke (host :18001 → container :8001)..."
	@CID=$$(docker run -d -p 18001:8001 station-studio:dev); \
	trap "docker stop $$CID >/dev/null 2>&1 || true; docker rm $$CID >/dev/null 2>&1 || true" EXIT INT TERM; \
	set -e; \
	echo "--- waiting for /api/stations (up to 30s)..."; \
	READY=0; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do \
		if curl -sf http://127.0.0.1:18001/api/stations >/dev/null 2>&1; then \
			echo "  ready after $${i}s"; READY=1; break; \
		fi; \
		sleep 1; \
	done; \
	if [ "$$READY" = "0" ]; then \
		echo "ERROR: /api/stations never returned 200 within 30s — container is broken." >&2; \
		echo "--- last 50 lines of container logs (cid $$CID) ---" >&2; \
		docker logs --tail 50 $$CID >&2 2>&1 || true; \
		exit 1; \
	fi; \
	echo "--- curl / (SPA shell) ---"; \
	curl -sS -o /dev/null -w 'HTTP %{http_code} %{content_type}\n' http://127.0.0.1:18001/ | tee /dev/stderr | grep -q '^HTTP 200 text/html'; \
	echo "--- curl /api/stations ---"; \
	curl -sS -o /dev/null -w 'HTTP %{http_code} %{content_type}\n' http://127.0.0.1:18001/api/stations | tee /dev/stderr | grep -q '^HTTP 200 application/json'; \
	echo "--- /api/stations: lists all four bundled stations ---"; \
	BODY=$$(curl -sS http://127.0.0.1:18001/api/stations); \
	for slug in starter stateful-starter rebirth-classics rebirth-radio; do \
		echo "$$BODY" | grep -q "\"$$slug\"" || { echo "FAIL: $$slug missing from /api/stations response in image" >&2; exit 1; }; \
	done; \
	echo "  ✓ all four bundled stations present"; \
	echo "--- curl /sources (SPA fallback) ---"; \
	curl -sS -o /dev/null -w 'HTTP %{http_code} %{content_type}\n' http://127.0.0.1:18001/sources | tee /dev/stderr | grep -q '^HTTP 200 text/html'; \
	echo "--- curl /api/sources (starter auto-import) ---"; \
	SOURCES=$$(curl -sS http://127.0.0.1:18001/api/sources); \
	for pool in STARTER_CONTENT STARTER_JINGLES STATEFUL_CONTENT REFERENCE_ARCHIVE; do \
		echo "$$SOURCES" | grep -q "$$pool" || { echo "FAIL: $$pool missing from /api/sources — bootstrap auto-import is broken in the image" >&2; exit 1; }; \
	done; \
	echo "$$SOURCES" | grep -q '"role":"reference"' || { echo "FAIL: no role=reference pool in /api/sources — §16.14.t3 REFERENCE_ARCHIVE didn't import with the right role in the image" >&2; exit 1; }; \
	echo "  ✓ all four pools auto-imported (reference role tag verified)"; \
	echo "==> Image smoke OK."

# Alembic schema migrations (DESIGN.md §14.17 / §17 #8).
#
# `make migrate` is the everyday command: applies all unapplied migrations
# from migrations/versions/ to the DB at $$STATION_STUDIO_DB_URL (or the
# default sqlite:///station_studio.db if unset).
#
# `make alembic-stamp` is the one-time op for an existing dev DB that
# already has the schema (created via Base.metadata.create_all). Marks
# the DB as caught up to head without trying to re-apply the baseline.
# Run once after pulling §14.17 onto a long-lived dev DB.
#
# `make alembic-revision msg="..."` creates a new revision file via
# autogenerate. Usage:
#   make alembic-revision msg="add excluded_candidates to playlist_trace"
#
# `make alembic-current` shows which revision the current DB is at.
migrate:
	$(ALEMBIC) upgrade head

alembic-stamp:
	$(ALEMBIC) stamp head

alembic-revision:
	@if [ -z "$(msg)" ]; then \
		echo "Usage: make alembic-revision msg=\"description of change\""; \
		exit 1; \
	fi
	# §14.33 review fix: guard against the silent-no-op trap. If
	# nothing has changed since the prior migration, alembic
	# autogenerate writes an empty `upgrade() pass / downgrade() pass`
	# migration with no warning, and the developer can land an empty
	# commit before noticing. `alembic check` exits non-zero when the
	# schema matches the registered head (i.e. no pending changes);
	# we invert that — a zero exit here is the bad case.
	@if $(ALEMBIC) check >/dev/null 2>&1; then \
		echo "No schema changes detected — alembic-revision would emit an empty migration."; \
		echo "Did you forget to edit engine/models.py first?"; \
		exit 1; \
	fi
	$(ALEMBIC) revision --autogenerate -m "$(msg)"

alembic-current:
	$(ALEMBIC) current

# Compare `alembic upgrade head` schema against `Base.metadata.create_all`.
# Catches the "added a model class but forgot alembic-revision" regression
# class (DESIGN.md §14.17 implementation note + §17 review S2). Both are
# the same source of truth at HEAD; this guard fires if a contributor
# updates engine/models.py without also generating a migration.
#
# The comparison runs against two fresh empty SQLite files at $$TMPDIR,
# enumerates objects via sqlite_master excluding alembic_version (the
# expected-to-differ tracking table). Diff is name + type + flattened
# CREATE-SQL — catches missing tables, columns, indexes, FK changes.
migration-parity:
	$(PY) scripts/check_migration_parity.py

# §16.13.t4 — `requirements*.txt` ↔ `pyproject.toml` dep parity.
# Catches a security bump that updates one surface but not the other.
# Source: see scripts/check_deps_parity.py docstring for design.
check-deps-parity:
	$(PY) scripts/check_deps_parity.py

# §16.14 c1 — regenerate the starter station's synthetic iTunes pools.
# Deterministic: re-running against unchanged inputs writes
# byte-identical files. Fix a typo or change the track count in
# `scripts/generate_starter_pools.py`, then run this target —
# `git diff stations/starter-*.itunes.xml` shows the result.
refresh-starter-pools:
	$(PY) -m scripts.generate_starter_pools

# §16.14.t1 c1 — regenerate the stateful-starter station's content
# pool. 336 tracks across 14 synthetic artists (24 tracks each),
# Genre=Pop. The track count matches the persistent cursor's
# cycle math exactly (24 advance/week × 14 weeks = 336). Edit
# scripts/generate_stateful_starter_pools.py if you want a
# different shape, then run this target.
refresh-stateful-starter-pools:
	$(PY) -m scripts.generate_stateful_starter_pools

# §14.31 backfill: populate PlaylistEntry.slot_type for playlists
# generated before the column existed. Idempotent — re-running after
# the first pass is a no-op. Use --dry-run flag (`make
# backfill-slot-types ARGS=--dry-run`) to preview without writing.
backfill-slot-types:
	$(PY) -m scripts.backfill_slot_types $(ARGS)

# §16.16 — disk-export prune helper. Walks the configured output dir
# and reports orphaned `.xml` files (no matching Playlist /
# PlaylistVersion row). Default is DRY-RUN — `prune-exports` reports
# what would be deleted. `prune-exports-confirm` actually deletes.
#
# Defaults assume the dev server is running locally on port 8001;
# override with `HOST=...` if needed (e.g. inside a docker compose
# network).
HOST ?= http://localhost:8001

# `-fsS` so curl exits non-zero on HTTP errors AND connection refusals
# (without it, a down server returns empty stdout and the json.tool
# pipe emits a misleading "Expecting value" parse error).
prune-exports:
	@curl -fsS -X POST '$(HOST)/api/playlists/export/prune' \
		| $(PY) -m json.tool

prune-exports-confirm:
	@curl -fsS -X POST '$(HOST)/api/playlists/export/prune?confirm=true' \
		| $(PY) -m json.tool

# Backup / restore for the SQLite DB (DESIGN.md §14.18 / §17 #9).
#
# Why bother on a single-user tool: one accidental DELETE
# /api/stations/cls and 12+ weeks of generated playlists vanish. Trace
# data alone (~150 MB after a season) is regeneratable but slow. Cheap
# insurance.
#
# All three targets operate on $(DB_FILE) — `station_studio.db` by
# default. To target a different DB file (e.g. a Playwright per-PID DB
# for ad-hoc inspection) pass DB_FILE=...:
#     make backup DB_FILE=playwright-test.12345.db
#
# DB_FILE does NOT auto-track $$STATION_STUDIO_DB_URL — they're
# independent vars. Alembic targets read STATION_STUDIO_DB_URL via
# migrations/env.py; backup/restore takes a raw filesystem path. If
# you've overridden STATION_STUDIO_DB_URL for a non-default DB,
# pass DB_FILE explicitly here too. The reasoning is single-user-tool
# pragmatism: the env-var carry-through would need a SQLite-URL parser
# (sed shim) for marginal value.
#
# `make backup` uses sqlite3's `.backup` command, which is WAL-aware —
# safe to run while uvicorn is using the DB. Plain `cp` would risk
# catching a half-flushed WAL state.
#
# `make restore date=YYYY-MM-DD-HHMMSS` overwrites the live DB. STOP
# uvicorn first — concurrent writes during restore corrupt. The target
# makes a pre-restore safety copy and clears stale -wal/-shm files
# before the cp.

backup:
	@mkdir -p backups
	@TS=$$(date +%Y-%m-%d-%H%M%S); \
	  echo "Backing up $(DB_FILE) → backups/$$TS.db"; \
	  sqlite3 $(DB_FILE) ".backup backups/$$TS.db" && \
	  echo "Done — $$(du -h backups/$$TS.db | cut -f1)"

backups:
	@if ls backups/*.db 1>/dev/null 2>&1; then \
	  ls -lht backups/*.db | head -20; \
	else \
	  echo "No backups yet — run 'make backup'."; \
	fi

restore:
	@if [ -z "$(date)" ]; then \
	  echo "Usage: make restore date=YYYY-MM-DD-HHMMSS"; \
	  echo ""; \
	  echo "Available backups:"; \
	  ls backups/*.db 2>/dev/null | sed 's|backups/|  |;s|\.db$$||' | head -10; \
	  exit 1; \
	fi
	@if [ ! -f "backups/$(date).db" ]; then \
	  echo "No backup found at backups/$(date).db"; \
	  echo "Run 'make backups' for the list."; \
	  exit 1; \
	fi
	@TS=$$(date +%Y-%m-%d-%H%M%S); \
	  echo "Pre-restore safety copy → backups/pre-restore-$$TS.db"; \
	  sqlite3 $(DB_FILE) ".backup backups/pre-restore-$$TS.db"; \
	  echo "Clearing any stale -wal / -shm files…"; \
	  rm -f $(DB_FILE)-wal $(DB_FILE)-shm; \
	  echo "Restoring backups/$(date).db → $(DB_FILE)"; \
	  cp backups/$(date).db $(DB_FILE); \
	  echo "Done. If you stopped run.py for the restore, you can restart it now."

# §14.33 review fix: `make dev` orchestrates the backend + frontend dev
# servers in parallel so the solo developer doesn't have to juggle two
# shells. Each background process gets a clear label; Ctrl-C kills both
# (the `trap` ensures the cleanup runs on any signal).
#
# Backend: uvicorn on :8001 with reload. Frontend: vite on :5173 with
# its existing proxy pointed at :8001 (web/vite.config.js).
#
# `dev-backend` / `dev-frontend` are also exposed as standalone
# targets so the user can run one without the other (e.g. testing a
# backend change against a previously-built frontend).
# `dev-migrate-check` — warn loudly when an existing dev DB is behind
# the latest alembic head. The lifespan's `Base.metadata.create_all`
# adds missing TABLES but NOT missing COLUMNS on existing tables (the
# §16.2.5.t7 verification surfaced this: pulling .t7 + running
# `make dev` against the existing dev DB 500'd until `make migrate`
# ran). Non-blocking — an operator may be intentionally on a stale DB
# for a repro; the warning just makes the "ah, that's why" obvious.
# Silent for: fresh DBs (file absent — create_all bootstraps to latest
# schema) AND DBs already at head.
dev-migrate-check:
	@if [ -f station_studio.db ]; then \
	  CUR=$$($(ALEMBIC) current 2>/dev/null | awk '/\(head\)/{ print "head"; exit } /^[a-f0-9]+/{ print $$1; exit }'); \
	  HEAD=$$($(ALEMBIC) heads 2>/dev/null | awk '/^[a-f0-9]+/{ print $$1; exit }'); \
	  if [ -n "$$CUR" ] && [ -n "$$HEAD" ] && [ "$$CUR" != "head" ] && [ "$$CUR" != "$$HEAD" ]; then \
	    echo ""; \
	    echo "  ⚠️  dev DB is behind the latest migration:"; \
	    echo "       current = $$CUR"; \
	    echo "       head    = $$HEAD"; \
	    echo "     run \`make migrate\` first to avoid 500s on the affected columns."; \
	    echo ""; \
	  fi; \
	fi

dev: dev-migrate-check
	@echo "Starting backend (:8001) and frontend (:5173)…"
	@echo "Ctrl-C to stop both."
	@trap 'kill 0' EXIT INT TERM; \
	  ($(PY) run.py 2>&1 | sed -u 's/^/[backend] /') & \
	  (cd web && npm run dev 2>&1 | sed -u 's/^/[frontend] /') & \
	  wait

dev-backend: dev-migrate-check
	$(PY) run.py

dev-frontend:
	cd web && npm run dev

# Import a weekly-format template JSON as a station-studio station
# (§14.34 / §17 Phase B sub-slice #16). Reads JSON produced by
# `scripts/dump_weekly_template.py` (run inside a weekly/ checkout)
# and upserts the translated template into the Station table.
#
# Pre-import DB backup runs by default — destructive ops get a safety
# net per §14.18. Pass `ARGS=--no-backup` to suppress.
#
# Optional `name="Display Name"` overrides the default slug-title-cased
# display name.
#
# Usage:
#   make import-station slug=rebirth-radio json=weekly-template.json
#   make import-station slug=rebirth-radio json=weekly-template.json \
#       name="Rebirth Radio" ARGS=--no-backup
import-station:
	@if [ -z "$(slug)" ] || [ -z "$(json)" ]; then \
		echo "Usage: make import-station slug=<slug> json=<path> [name=\"Display Name\"] [ARGS=--no-backup]"; \
		echo "Imports a weekly-format template JSON as a station-studio station (§14.34)."; \
		exit 1; \
	fi
	@if ! echo "$(ARGS)" | grep -q -- "--no-backup"; then \
		echo "Pre-import safety backup → backups/pre-import-$$(date +%Y-%m-%d-%H%M%S).db"; \
		$(MAKE) backup; \
	fi
	$(PY) -m scripts.import_station "$(slug)" "$(json)" $(if $(name),--name "$(name)",) $(ARGS)

# Import weekly's 6 iTunes XML source pools via a manifest JSON
# (§14.35 / §17 Phase B sub-slice #17). The manifest declares each
# pool's path + role (content for samplable pools, reference for
# MASTER); relative paths in the manifest resolve against its parent
# directory.
#
# Pre-import DB backup runs by default — pool import is a heavy write
# (~10K rows across 6 pools); ARGS=--no-backup suppresses.
#
# Usage:
#   make import-weekly-pools manifest=path/to/manifest.json
#   make import-weekly-pools manifest=path/to/manifest.json ARGS=--no-backup
import-weekly-pools:
	@if [ -z "$(manifest)" ]; then \
		echo "Usage: make import-weekly-pools manifest=<path> [ARGS=--no-backup]"; \
		echo "Imports weekly's 6 source pools via a manifest JSON (§14.35)."; \
		exit 1; \
	fi
	@if ! echo "$(ARGS)" | grep -q -- "--no-backup"; then \
		echo "Pre-import safety backup → backups/pre-import-$$(date +%Y-%m-%d-%H%M%S).db"; \
		$(MAKE) backup; \
	fi
	$(PY) -m scripts.import_weekly_pools "$(manifest)" $(ARGS)

# Refresh stations/rebirth-radio.json from a live weekly checkout
# (§14.36). The committed JSON is a snapshot of weekly's
# build_template() + INITIAL_CURSORS at a point in time; this target
# regenerates it. Requires WEEKLY_DIR pointing at a weekly checkout
# with its venv active (the dump runs INSIDE weekly so it can import
# app.services.template).
#
# Usage:
#   make refresh-rebirth-radio WEEKLY_DIR=/path/to/weekly
#
# What happens:
#   1. Dump weekly's build_template() to /tmp/weekly-template.json
#      via scripts/dump_weekly_template.py running under weekly's venv
#   2. Translate weekly→station-studio format via the existing
#      import_station.py --output mode (no DB writes)
#   3. Overwrite stations/rebirth-radio.json with the result
#
# Drift detection isn't automated — re-run this target when weekly's
# template changes. Sub-slice #19 (byte-equality harness) is what
# actually catches drift via comparing generation output.
refresh-rebirth-radio:
	@if [ -z "$(WEEKLY_DIR)" ]; then \
		echo "Usage: make refresh-rebirth-radio WEEKLY_DIR=/path/to/weekly"; \
		echo "Refreshes stations/rebirth-radio.json from a live weekly checkout (§14.36)."; \
		exit 1; \
	fi
	@if [ ! -d "$(WEEKLY_DIR)/app/services" ]; then \
		echo "error: $(WEEKLY_DIR) doesn't look like a weekly checkout (missing app/services/)."; \
		exit 1; \
	fi
	@if [ ! -x "$(WEEKLY_DIR)/.venv/bin/python" ]; then \
		echo "error: $(WEEKLY_DIR)/.venv/bin/python not executable. Set up weekly's venv first."; \
		exit 1; \
	fi
	@TMP=$$(mktemp -t weekly-template.XXXXXX.json); \
	  trap "rm -f $$TMP" EXIT; \
	  echo "Dumping weekly template → $$TMP …"; \
	  PYTHONPATH=$(WEEKLY_DIR) $(WEEKLY_DIR)/.venv/bin/python scripts/dump_weekly_template.py --out "$$TMP" && \
	  echo "Translating → stations/rebirth-radio.json …" && \
	  $(PY) -m scripts.import_station rebirth-radio "$$TMP" --output stations/rebirth-radio.json && \
	  echo 'Done. Diff against the previous version with: git diff stations/rebirth-radio.json'

# Refresh tests/fixtures/expected/port_byte_eq.json from a live weekly
# checkout (§14.37 c2). The committed fixture captures weekly's
# generator output for (seed_base=42, weeks 2-9); this target
# regenerates it. Requires both WEEKLY_DIR (the checkout root) and
# WEEKLY_FROM_USER_DIR (where the XMLs live; usually $WEEKLY_DIR/from_user).
#
# Usage:
#   make refresh-port-fixture WEEKLY_DIR=/path/to/weekly \
#                             WEEKLY_FROM_USER_DIR=/path/to/weekly/from_user
refresh-port-fixture:
	@if [ -z "$(WEEKLY_DIR)" ] || [ -z "$(WEEKLY_FROM_USER_DIR)" ]; then \
		echo "Usage: make refresh-port-fixture WEEKLY_DIR=/path/to/weekly WEEKLY_FROM_USER_DIR=/path/to/weekly/from_user"; \
		echo "Refreshes tests/fixtures/expected/port_byte_eq.json from a live weekly checkout (§14.37 c2)."; \
		exit 1; \
	fi
	@if [ ! -d "$(WEEKLY_DIR)/app/services" ]; then \
		echo "error: $(WEEKLY_DIR) doesn't look like a weekly checkout (missing app/services/)."; \
		exit 1; \
	fi
	@if [ ! -x "$(WEEKLY_DIR)/.venv/bin/python" ]; then \
		echo "error: $(WEEKLY_DIR)/.venv/bin/python not executable. Set up weekly's venv first."; \
		exit 1; \
	fi
	@if [ ! -d "$(WEEKLY_FROM_USER_DIR)" ]; then \
		echo "error: $(WEEKLY_FROM_USER_DIR) is not a directory."; \
		exit 1; \
	fi
	@echo "Capturing weekly playlists → tests/fixtures/expected/port_byte_eq.json …"; \
	  PYTHONPATH=$(WEEKLY_DIR) $(WEEKLY_DIR)/.venv/bin/python scripts/dump_weekly_playlist.py \
	    --from-user "$(WEEKLY_FROM_USER_DIR)" \
	    --out tests/fixtures/expected/port_byte_eq.json && \
	  echo 'Done. Diff against the previous version with: git diff tests/fixtures/expected/port_byte_eq.json'

# Refresh BOTH the rebirth-radio.json artifact AND the port byte-
# equality fixture (§14.37 sweep-up). Useful when weekly's template
# OR the translator changes — both rebirth-radio.json and
# port_byte_eq.json may need to track. Independent of station-studio
# generator changes (those don't change weekly's output).
#
# The two targets are run in this order: template first (so the
# downstream port test runs against the up-to-date artifact), then
# fixture. Either step can fail independently; the user diagnoses
# from there.
#
# Usage:
#   make refresh-port-everything WEEKLY_DIR=/path/to/weekly \
#                                WEEKLY_FROM_USER_DIR=/path/to/weekly/from_user
refresh-port-everything:
	@if [ -z "$(WEEKLY_DIR)" ] || [ -z "$(WEEKLY_FROM_USER_DIR)" ]; then \
		echo "Usage: make refresh-port-everything WEEKLY_DIR=… WEEKLY_FROM_USER_DIR=…"; \
		echo "Runs refresh-rebirth-radio + refresh-port-fixture in order."; \
		exit 1; \
	fi
	$(MAKE) refresh-rebirth-radio WEEKLY_DIR="$(WEEKLY_DIR)"
	$(MAKE) refresh-port-fixture WEEKLY_DIR="$(WEEKLY_DIR)" WEEKLY_FROM_USER_DIR="$(WEEKLY_FROM_USER_DIR)"
	@echo "Both refreshed. Run the port byte-equality test:"
	@echo "  WEEKLY_FROM_USER_DIR=$(WEEKLY_FROM_USER_DIR) .venv/bin/python -m pytest tests/test_port_byte_equality.py"

# Refresh the Classics byte-equality fixture (§14.40). Unlike
# `refresh-port-fixture` (which dumps from weekly), this is
# self-contained: station-studio's own generator produces the
# expected output ("freeze-against-self" pattern). No weekly venv
# needed.
#
# Workflow:
#   1. Regenerate the synthetic Classics pool XMLs + manifest (so the
#      fixture is fully reproducible from this command alone — no
#      separate "regenerate pools" step needed).
#   2. Run the test in bootstrap mode (BOOTSTRAP_BYTE_EQ_FIXTURES=1)
#      to capture station-studio's current output as the new committed
#      fixture.
#
# Usage:
#   make refresh-classics-fixture
refresh-classics-fixture:
	@echo "Regenerating synthetic Classics pool XMLs + manifest …"
	$(PY) scripts/generate_classics_fixtures.py
	@echo "Capturing station-studio's Classics output as the new fixture …"
	BOOTSTRAP_BYTE_EQ_FIXTURES=1 $(PY) -m pytest \
	    tests/test_classics_byte_equality.py -v
	@echo "Done. Diff with: git diff tests/fixtures/expected/classics_byte_eq.json"

# Generate N weeks of Rebirth Radio iTunes XML playlists for USER-FACING
# validation (not byte-equality). Drops real .xml files that an operator
# can import into Apple Music/iTunes + play to confirm the port sounds
# right.
#
# Two modes — both run by default:
#   * fresh: weeks 1..N, no prior (all prev_ref slots fall back)
#   * prior: weeks 2..N+1, WEEKLY 001 seeded as prior (realistic cutover)
#
# Outputs to output/user-test/{fresh,prior}/. Output dir is gitignored
# (not committed; produced per-machine).
#
# Usage:
#   make generate-user-playlists \
#       WEEKLY_FROM_USER_DIR=/path/to/weekly/from_user
#
# With overrides:
#   make generate-user-playlists \
#       WEEKLY_FROM_USER_DIR=/path/to/weekly/from_user \
#       WEEKS=10 \
#       NAME_PREFIX="WEEKLY PLAYLIST" \
#       MODE=prior
generate-user-playlists:
	@if [ -z "$(WEEKLY_FROM_USER_DIR)" ]; then \
		echo "Usage: make generate-user-playlists WEEKLY_FROM_USER_DIR=/path/to/from_user"; \
		echo "Optional: WEEKS=N (default 25), NAME_PREFIX=\"…\" (default 'Rebirth Radio'),"; \
		echo "          MODE=fresh|prior|both (default both)"; \
		exit 1; \
	fi
	$(PY) -m scripts.generate_user_playlists \
	    --xmls "$(WEEKLY_FROM_USER_DIR)" \
	    $(if $(WEEKS),--weeks $(WEEKS),) \
	    $(if $(NAME_PREFIX),--name-prefix "$(NAME_PREFIX)",) \
	    $(if $(MODE),--mode $(MODE),)

# §16.2.1: auto-derive an editor sidecar for a flat_slots station
# template. Heuristic walker (scripts/derive_sidecar.py) detects
# sections + macros from slot-vocabulary patterns; output validates
# against the engine.editor_metadata drift rules.
#
# Usage:
#   make derive-sidecar STATION=stations/rebirth-radio.json
#
# Writes alongside the station file (STATION basename + .editor.json).
# row_template stations should be hand-authored — derive_sidecar
# rejects them with a clear error.
derive-sidecar:
	@if [ -z "$(STATION)" ]; then \
		echo "Usage: make derive-sidecar STATION=stations/X.json"; \
		exit 1; \
	fi
	@OUT=$$(echo "$(STATION)" | sed 's/\.json$$/.editor.json/'); \
	  $(PY) -m scripts.derive_sidecar --station "$(STATION)" --out "$$OUT"

# §16.15 c1 — docs site (MkDocs-Material). Source lives in `docs/`;
# build output goes to `site/` (gitignored). Published to GH Pages
# from main via .github/workflows/docs.yml.
#
# Local workflow:
#   make docs-serve   # auto-reload at http://127.0.0.1:8000
#   make docs-build   # one-shot build into site/
#
# `strict: true` in mkdocs.yml fails the build on broken internal
# links / missing nav entries so drift between code and docs
# surfaces locally + in CI before the published site goes stale.
docs-serve:
	$(PY) -m mkdocs serve

docs-build:
	$(PY) -m mkdocs build --strict
