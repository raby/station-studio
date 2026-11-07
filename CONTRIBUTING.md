# Contributing to Station Studio

Thanks for your interest. Station Studio is open-source under
[MIT](LICENSE) — fork freely, send PRs, file issues.

This doc covers the conventions the project uses internally. They
exist because the engine is byte-equality-tested against two
production radio stations; loose conventions would erode that
guarantee. Following them is what keeps "any contributor can ship
without breaking real stations" true.

## How a slice happens

Work happens in small slices. Each slice is one coherent piece of
work, on its own branch, in its own pull request:

1. **Design first.** For anything non-trivial, say what the slice
   does before writing code: what it ships, what it doesn't, and the
   decisions taken (with the alternatives turned down). Open an issue,
   or put it at the top of the pull request.

2. **Build it with its tests.** Every behaviour change comes with a
   test at the right layer (see below). A slice that's too big for one
   review becomes two pull requests, each one working on its own.

3. **Review it in full before merging**: correctness, what's missing,
   what it could break. Anything found is fixed in the same pull
   request, and the fix is reviewed again.

4. **Write it down where it'll be read:**
    - the pull request says what changed, why, and what was checked;
    - `CHANGELOG.md` says it for people using the apps, in plain words;
    - [DESIGN.md](DESIGN.md) and the docs site's
      [Architecture](https://raby.github.io/station-studio/architecture/)
      pages hold the design. A slice that changes how a part works
      updates them in the same pull request.

Follow-ups found along the way ("we should also…") go in the pull
request under what it doesn't do, so they can be picked up as slices
of their own, never only in a review comment.

Until November 2026 each slice was written up in a long running
`DESIGN.md`; that's now the
[design log](docs/history/design-log.md), kept as it was. Section
numbers in code comments and older commits ("§14.25") point into it.

## Test discipline

| Layer | Tool | Gate |
|---|---|---|
| Backend (engine + API) | pytest | ≥90% line coverage (`make test-backend`) |
| Migrations | `make migration-parity` | upgrade-head must match `Base.metadata.create_all` |
| Frontend unit | vitest | per-file lines gate in `web/vite.config.js` |
| End-to-end | Playwright | must pass in CI |
| Byte-equality (Rebirth Radio) | `tests/test_port_byte_equality.py` | skip-when-XMLs-missing; **must stay green when XMLs present** |
| Byte-equality (Classics) | `tests/test_classics_byte_equality.py` | always runs (synthetic pools committed) |
| Install (wheel channel) | `make wheel-smoke` | **release-blocker** — installs the wheel into a temp venv, launches `station-studio serve`, curls `/`, `/api/stations`, `/sources` and asserts HTTP 200 + content-type. Catches bundled-asset force-include regressions + lifespan breakage that pytest can't see. |
| Install (docker channel) | `make image-smoke` | **release-blocker** — builds the docker image, runs a container, curls the same three URLs against it. Healthcheck reports `(healthy)` after lifespan completes. Run before tagging a release. |

Changes that affect the engine MUST not break byte-equality for
either example station. If you're intentionally changing engine
output (new strategy, bug fix), regenerate the fixtures via
`BOOTSTRAP_BYTE_EQ_FIXTURES=1` AND `make refresh-port-fixture`,
inspect the diff, and say why output changed in the pull request and
`CHANGELOG.md`.

## Dependencies

Each app's `pyproject.toml` says what it needs, as ranges (that's what
a `pip install` from PyPI gets). `uv.lock`, at the repository root, pins
the exact version and hashes of every Python package all three apps are
built, tested and shipped with: CI, the Mac and Windows apps and the
Docker image all install from it, so a release is built from the
versions its pull request was tested with.

- **Adding or changing a dependency:** edit the `pyproject.toml`, run
  `make lock`, and commit `uv.lock` with it. CI fails (`uv lock
  --check`) if the two don't match.
- **Tools** go in a dependency group, not `dependencies`: `dev`
  (tests, `build`), `docs` (the docs site) or `installer` (PyInstaller
  and what the apps' windows need). Groups aren't in the published
  package.
- **Upgrading:** `make lock-upgrade` moves every package to the newest
  version its range allows, as long as it's been out a week
  (`exclude-newer` in `pyproject.toml`: a bad or hijacked release is
  usually pulled within days). Review `uv.lock`'s diff like code: it's
  what the next release ships. One package: `uv lock --upgrade-package
  fastapi`.
- **Monthly:** a scheduled task runs `make lock-upgrade` on the 1st of
  each month and opens one pull request (`deps/upgrade-YYYY-MM`) with
  what moved, anything notable in the release notes, and the checks.
  It never merges; review and merge it like any other. Before a
  Station Studio release, run one by hand if the last was a few weeks
  ago.
- **uv's version** is in each workflow (`astral-sh/setup-uv`) and the
  Dockerfile; bump them together. `pyproject.toml` asks for at least
  that minor version locally.

## Code style

- **Python**: stdlib first; SQLAlchemy 2.0 patterns; type hints on
  module-level signatures (relaxed on test helpers); no `mypy`
  strict yet.
- **Frontend**: SvelteKit; TypeScript; Vitest for unit + Playwright
  for e2e. `svelte-check --threshold error` in CI.
- **Docstrings**: long-form, narrative. Not just "what" — *why*.
  See existing engine modules for the shape. The goal is that
  someone reading the code six months later understands the
  decisions, not just the mechanics.

## Proposing a new station shape

**The fast path** (hobbyist building their first station): fork
`stations/rebirth-classics.json`, edit the slot vocabulary to match
what you want, drop it back in `stations/`, restart the app. The
auto-seed lifespan handler picks it up on next boot. Your station
shows up in the sidebar.

That's it for the minimum case. The fast path is the path. You DO
NOT need to write a byte-equality test, port through the weekly
translator, or know what `mix_exclusion_weeks` means. Most stations
won't.

### When you DO want more

Three signals you're past the fast path:

1. **You want to port from an existing weekly-format generator.**
   Use `scripts/dump_weekly_template.py` + `make import-station`
   to bring its template in. The design log's §14.34 has the
   translation table.

2. **You want regression-locked output across engine changes.**
   Add a "freeze-against-self" byte-equality test under
   `tests/test_<slug>_byte_equality.py`. The Classics test is
   the model. Only worth doing if you're maintaining the station
   long-term + care about engine changes not silently changing
   your output.

3. **You hit a primitive the engine doesn't have.** Propose it
   as a slice — engine strategy modules live under
   `engine/strategies/`; pattern: pure function, no DB, no
   randomness-source baked in. The existing eight are the examples;
   the design log's §14.24–§14.28 walk through how five of them were
   designed.

The [template grammar](https://raby.github.io/station-studio/reference/template-grammar/)
is the reference for both forms (`flat_slots` and `row_template`).

## Commit message style

```
<What changed, in a line> (slice N, PR M)

<paragraph(s): what it does and why>

Co-Authored-By: ...
```

The first line says what changed for someone reading the log
("Week page: change a song just this week"), not which file. Fixes
from a review are their own commit, starting `Review:` and naming
what they fix. See `git log` for examples. (Older commits start with
the design-log section they belonged to: `§14.NN …`.)

## Releases

Each app has its own version and release:

| App | Version in | Released by |
|---|---|---|
| Station Studio (PyPI) | `pyproject.toml` | pushing a `v<version>` tag (below) |
| Station Studio (Mac and Windows apps) | `pyproject.toml`, `station_studio/__init__.py` | Actions → **Publish Station Studio app** → Run workflow on main: tests, a draft `studio-v<version>` release, the downloads, then the release published |
| Station Player | `player/pyproject.toml` | Actions → **Publish Station Player** (`player-v<version>`, and PyPI) |
| Station Server | `server/pyproject.toml` | Actions → **Publish Station Server** (`server-v<version>`) |

Each workflow's header comment says exactly what it checks and
builds. If a download job fails, use **Re-run failed jobs** on the
run: the draft is kept, and published once they pass.

## Releasing to PyPI

Station Studio's PyPI release is `.github/workflows/publish.yml`.

**One-time setup.** PyPI's **pending publisher** feature lets you
authorize a workflow to claim a name BEFORE the project exists on
PyPI — no `twine upload`, no temp API token. Three steps:

1. **PyPI** → Account → Publishing → "Add a pending publisher":
   - PyPI project name: `station-studio`
   - owner: `raby`, repo: `station-studio`
   - workflow: `publish.yml`, environment: `pypi`
2. **TestPyPI** (test.pypi.org) → same, environment: `testpypi`.
3. **GitHub repo** → Settings → Environments → create `pypi` and
   `testpypi` (no protection rules required for hobbyist use; add
   reviewers if you want a manual approval gate before real-PyPI
   publish).

The first tag push triggers the workflow, PyPI sees the trusted
publisher match the pending claim, and the project is created
in-place. No tokens stored anywhere.

**Cutting a release:**

```bash
# 1. Local release-rehearsal — catches the bugs the workflow can't.
make wheel-smoke                      # wheel installs + serves OK
make image-smoke                      # docker image installs + serves OK
.venv/bin/python -m pytest -q         # full backend suite

# 2. Bump version (single source of truth: pyproject.toml).
$EDITOR pyproject.toml                # version = "0.2.0"
git commit -am "Release v0.2.0"

# 3. Tag + push — the workflow does the rest.
git tag v0.2.0
git push origin main v0.2.0
```

The publish.yml workflow asserts `tag-version == pyproject-version`
before building (catches "tagged but forgot to bump pyproject"),
pushes to test.pypi, pip-installs from test.pypi + curls the same
three URLs `make wheel-smoke` checks locally, then on green
proceeds to real PyPI.

**Manual rehearsal** (test.pypi only, never real PyPI):

```bash
gh workflow run publish.yml
```

`workflow_dispatch` is implicitly test-only — the `publish-pypi`
job gates on `push` events, so a manual run can never reach real
PyPI regardless of what flags you pass. Tag-driven push is the
only path to a real release, by design.

## When in doubt

Read [DESIGN.md](DESIGN.md) for how the apps fit together, then the
[docs site](https://raby.github.io/station-studio/). For why a
particular decision was taken, search the
[design log](docs/history/design-log.md). If your question isn't answered
there, open a discussion or issue before writing code.
