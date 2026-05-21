# station-studio — convenience entry points for the test matrix.
#
# Three layers, each independently runnable:
#   make test-backend       pytest + coverage gate (Python)
#   make test-frontend-unit vitest (api.ts + component tests)
#   make test-e2e           playwright (UI ↔ API roundtrip)
#
# `make test` runs all three in order; CI does the same.

.PHONY: test test-backend test-frontend-unit test-e2e install-e2e-browsers clean-test \
        migrate alembic-stamp alembic-revision alembic-current migration-parity \
        backfill-slot-types \
        backup backups restore \
        dev dev-backend dev-frontend \
        import-station import-weekly-pools refresh-rebirth-radio

PY := .venv/bin/python
ALEMBIC := .venv/bin/alembic
DB_FILE := station_studio.db

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

# §14.31 backfill: populate PlaylistEntry.slot_type for playlists
# generated before the column existed. Idempotent — re-running after
# the first pass is a no-op. Use --dry-run flag (`make
# backfill-slot-types ARGS=--dry-run`) to preview without writing.
backfill-slot-types:
	$(PY) -m scripts.backfill_slot_types $(ARGS)

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
dev:
	@echo "Starting backend (:8001) and frontend (:5173)…"
	@echo "Ctrl-C to stop both."
	@trap 'kill 0' EXIT INT TERM; \
	  ($(PY) run.py 2>&1 | sed -u 's/^/[backend] /') & \
	  (cd web && npm run dev 2>&1 | sed -u 's/^/[frontend] /') & \
	  wait

dev-backend:
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
	  echo "Dumping weekly template → $$TMP …"; \
	  PYTHONPATH=$(WEEKLY_DIR) $(WEEKLY_DIR)/.venv/bin/python scripts/dump_weekly_template.py --out "$$TMP" && \
	  echo "Translating → stations/rebirth-radio.json …" && \
	  $(PY) -m scripts.import_station rebirth-radio "$$TMP" --output stations/rebirth-radio.json && \
	  rm -f "$$TMP" && \
	  echo 'Done. Diff against the previous version with: git diff stations/rebirth-radio.json'
