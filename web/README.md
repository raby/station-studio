# Station Studio — Web

SvelteKit front-end. The wizard (and later, the block editor) reads and writes station template JSON.
All template validation is delegated to the backend; the UI is a thin shell over the same JSON the engine consumes.

## Initialising the SvelteKit app

The repo ships with a lightweight stub (`package.json`, `src/`, `svelte.config.js`) so you can start developing immediately without a full `npm create svelte@latest` wizard. Install deps and run:

```bash
cd web
npm install
npm run dev          # http://localhost:5173
```

The backend should be running on `http://localhost:8001` (see repo-root `run.py`). CORS is already configured for the Vite dev server.

## Planned surfaces

1. **`/stations`** — list stations, link into wizard or viewer
2. **`/stations/new`** — wizard: name → sources → cursor → layout → review → save
3. **`/stations/[slug]`** — view template with colour-coded source lanes; live slot-grid preview
4. **`/stations/[slug]/generate`** — trigger generation, show trace with collapsible per-slot detail
5. **`/stations/[slug]/edit`** — eventually, a visual block editor over the same JSON

## Design note

The wizard is designed to produce the canonical template JSON directly. No UI-only state. If a user opens a JSON authored by someone else (or by a future block editor), the wizard can roundtrip it. See repo-root `DESIGN.md` §4.2.
