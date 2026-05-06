# Station Studio — Web

SvelteKit front-end. The wizard (and later, the block editor) reads and writes station template JSON.
All template validation is delegated to the backend; the UI is a thin shell over the same JSON the engine consumes.

## Running

```bash
cd web
npm install
npm run dev          # http://localhost:5173
```

The backend should be running on `http://localhost:8001` (see repo-root `run.py`). CORS is already configured for the Vite dev server.

## Tests

Two tiers — keep both green when shipping UI changes.

```bash
npm run test         # vitest: jsdom, $lib/api mocked
npm run test:e2e     # playwright: real backend on a per-PID SQLite, real Vite proxy
```

Vitest covers each page's render branches (loading / error / empty /
populated) without a backend. Playwright covers the cross-page flow:
landing → wizard → station detail → trace viewer → iTunes download.

A few non-obvious things to know before adding tests:

- **Vitest needs `resolve.conditions: ['browser']` at top-level** in
  `vite.config.js`, gated on `process.env.VITEST`. Otherwise Svelte 4's
  `onMount` is no-op under jsdom (we'd be loading the SSR build).
- **`vitest.setup.ts` already runs `await act(); cleanup()` per test**
  via `@testing-library/svelte/vitest`. Don't add a per-file
  `afterEach(cleanup)` on top — it races the act flush.
- **Loading-branch tests** can use a pre-resolved
  `mockResolvedValueOnce(...)` and assert synchronously before
  `await act()` — the `.then` microtask hasn't fired by the time
  `render()` returns. No deferred-promise dance needed.
- **Playwright DB filename includes the PID** so concurrent dev
  runs don't collide; `make clean-test` globs `playwright-test.*.db*`.
- **Hydration sentinel**, not `networkidle`. The wizard sets
  `data-wizard-ready` on `<html>` from `onMount`; specs `await
  expect(page.locator('html[data-wizard-ready]')).toBeAttached()`
  before filling form fields. SSR HTML lies about hydration state.

DESIGN.md §14.8 is the long-form rationale for all of the above.

## Surfaces

| Route                          | State        | Notes                                                     |
|--------------------------------|--------------|-----------------------------------------------------------|
| `/`                            | shipped      | Landing — list stations, link to wizard / detail          |
| `/stations/new`                | shipped      | Wizard — single-page form; produces template JSON         |
| `/stations/[slug]`             | shipped      | Station detail — header + playlists table                 |
| `/playlists/[id]`              | shipped      | Trace viewer — slot-by-slot reasoning, iTunes download    |
| Generate / regenerate UI       | shipped      | Empty-state CTA + next-week + advanced + per-row ↻ regen  |
| Delete / station-reset UI      | shipped      | Per-row ✕ delete + "Delete station →" nuke; cascades      |
| Disk-export UI button          | shipped      | Trace viewer "Export to disk" + inline path/size status    |
| Edit mode for stations         | shipped      | `/stations/[slug]/edit` — shared `<StationForm>` + JSON preview + diff + reset |
| Visual block editor            | v2 (§16.2)   | Drag-drop over the same JSON the wizard produces          |
| Trace versioning               | later (§16.4)| Schema change so regen stops being destructive            |

## Design note

The wizard is designed to produce the canonical template JSON directly. No UI-only state. If a user opens a JSON authored by someone else (or by a future block editor), the wizard can roundtrip it. See repo-root `DESIGN.md` §4.2 (canonical JSON) and §16 (next-up slices).
