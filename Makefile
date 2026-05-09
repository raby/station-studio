# station-studio — convenience entry points for the test matrix.
#
# Three layers, each independently runnable:
#   make test-backend       pytest + coverage gate (Python)
#   make test-frontend-unit vitest (api.ts + component tests)
#   make test-e2e           playwright (UI ↔ API roundtrip)
#
# `make test` runs all three in order; CI does the same.

.PHONY: test test-backend test-frontend-unit test-e2e install-e2e-browsers clean-test \
        migrate alembic-stamp alembic-revision alembic-current

PY := .venv/bin/python
ALEMBIC := .venv/bin/alembic

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
	$(ALEMBIC) revision --autogenerate -m "$(msg)"

alembic-current:
	$(ALEMBIC) current
