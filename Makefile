# station-studio — convenience entry points for the test matrix.
#
# Three layers, each independently runnable:
#   make test-backend       pytest + coverage gate (Python)
#   make test-frontend-unit vitest (api.ts + component tests)
#   make test-e2e           playwright (UI ↔ API roundtrip)
#
# `make test` runs all three in order; CI does the same.

.PHONY: test test-backend test-frontend-unit test-e2e install-e2e-browsers clean-test

PY := .venv/bin/python

test: test-backend test-frontend-unit test-e2e

# Backend: pytest + fail-under so coverage can't silently drop.
test-backend:
	$(PY) -m coverage run -m pytest
	$(PY) -m coverage report --fail-under=95

test-frontend-unit:
	cd web && npm run test:unit

test-e2e:
	cd web && npm run test:e2e

# One-shot: fetches the chromium binary Playwright needs.
install-e2e-browsers:
	cd web && npx playwright install chromium

clean-test:
	rm -f .coverage playwright-test.*.db playwright-test.*.db-wal playwright-test.*.db-shm
	rm -rf web/test-results web/playwright-report
