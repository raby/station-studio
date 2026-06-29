# Test pyramid

How the test layers stack + when to add what.

## The 4 layers

| Layer | Tool | What it tests | When it runs |
|---|---|---|---|
| **Backend unit + integration** | pytest | engine.* (strategies, generator, persistence, template) + api.* (routers, schema validation, TestClient round-trips) | `make test-backend` + CI `backend` job |
| **Backend byte-equality** | pytest | full `generate()` against committed PID-sequence JSON snapshots under `tests/fixtures/expected/` — pins reproducibility. iTunes XML inputs live in `tests/fixtures/itunes_xml_byte_eq/`; the harness lives in `tests/byte_equality.py` + the four `tests/test_byte_equality_*.py` test modules | same job, gated together |
| **Frontend unit** | vitest + @testing-library/svelte | component behaviour with mocked `$lib/api`, Svelte event dispatch, state machines | `make test-frontend-unit` + CI `frontend-unit` |
| **End-to-end** | Playwright + @axe-core/playwright | full stack — real FastAPI backend, real SvelteKit frontend, real SQLite, real browser | `make test-e2e` + CI `e2e` |

## What goes where

**pytest:**

- A new engine strategy lands. Test its candidate-selection logic
  + the §14.32 slot_type column it populates. Pin the byte-equality
  output against a `tests/fixtures/expected/<name>.json` PID-
  sequence snapshot.
- A new API endpoint. TestClient call + assertion on the JSON
  response. Verify all 4xx error paths (404 on missing entity,
  409 on conflict, 422 on Pydantic rejection, etc).

**vitest:**

- A new Svelte component. Mount in jsdom, verify event dispatch +
  prop reactivity. Mock `$lib/api` exports if it fetches.
- A new page-level state machine (e.g. trace-viewer diff classifier).
  Pin every state transition with a parametrised case.

**Playwright:**

- A new user journey across multiple routes (e.g. create-station
  wizard → station detail → generate → trace viewer). Run with
  `--headed` locally for debugging.
- An a11y regression worry. Add `assertNoA11yViolations(page)` —
  it runs axe-core 2.0+2.1 A/AA against the rendered DOM and
  fails on `serious` or `critical`. Smokes already wired at
  `/`, `/stations/new`, `/stations/[slug]`, and
  `/playlists/[id]` (the landing and station-detail pages
  also run at a 375px narrow viewport).

## CI gates

`.github/workflows/ci.yml` runs **5 jobs in parallel**:
`backend`, `frontend-unit`, `e2e`, `install-smoke-wheel`,
`install-smoke-image`. Each must pass for the merge to land.

Two important **steps inside `backend`** (not separate jobs)
guard cross-cutting drift:

- `make migration-parity` — verifies `alembic upgrade head`
  produces the same schema as `Base.metadata.create_all()`
  (catches "added a model but forgot a migration").
- `make check-deps-parity` — verifies `requirements.txt`,
  `requirements-dev.txt`, and `pyproject.toml` list the same
  packages with the same constraints.

The `install-smoke-wheel` job runs `make wheel-smoke` —
builds the wheel, installs into a temp venv, curls / and
/api/stations for HTTP-200 + content-type. `install-smoke-image`
(push-to-main only) does the same for the docker image.

The `.github/workflows/docs.yml` workflow is **separate** and
builds + deploys the §16.15 docs site to GH Pages in strict
mode on every push to main.

## Read more

`tests/test_byte_equality.py` for the reproducibility harness.
DESIGN.md §14.8 + §17 for the historical evolution of the test
pyramid (Phase B byte-equality, Phase C UI surface).

[DESIGN.md →](https://github.com/raby/station-studio/blob/main/DESIGN.md)
