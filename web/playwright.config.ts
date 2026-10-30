import { defineConfig, devices } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '..');

// One scratch folder per run, in the OS temp dir rather than the repo
// root (runs used to leave a playwright-test.<pid>.db* trio and a
// playwright-stations.<pid>/ folder there every time). Playwright
// evaluates this config in its runner AND in each worker process; the
// first evaluation picks the path and puts it in the environment, and
// workers inherit it, so every process (and global-teardown.ts, which
// deletes the folder) agrees on one path. The PID still keeps two
// concurrent `make test-e2e` runs apart. Nothing is created here: the
// backend webServer command below makes the folder on boot.
const E2E_DIR = (process.env.STATION_STUDIO_E2E_DIR ??= path.join(
  os.tmpdir(),
  `station-studio-e2e.${process.pid}`,
));

// Per-run SQLite file; the backend command below wipes it on boot.
const TEST_DB_PATH = path.join(E2E_DIR, 'test.db');

// §16.2.3 c3 — throwaway stations dir for the e2e backend. The editor
// EXPORT endpoint writes `stations/{slug}.json`; without this override
// the e2e would litter the repo's committed `stations/`. A real server
// process has no dependency-override, so the isolation must come via
// the env var `get_stations_dir` reads (api/deps.py). Editor GETs for
// e2e-created stations have no committed sidecar → synthesize-trivial,
// so an empty dir is fine.
const TEST_STATIONS_DIR = path.join(E2E_DIR, 'stations');

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // the backend is a single-process shared store
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  // Deletes E2E_DIR after the run. The DB wipe is done by the backend
  // webServer command itself (see below), since Playwright starts
  // webServers before globalSetup runs.
  globalTeardown: './e2e/global-teardown.ts',
  webServer: [
    {
      name: 'backend',
      // Wipe the test DB *before* uvicorn imports api.deps (which runs
      // create_all at module-import). Doing this inside globalSetup races:
      // Playwright boots webServers before globalSetup runs, so a stale DB
      // file gets created/opened first and then deleted out from under the
      // connection pool, leaving queries to hit an empty file.
      command: `rm -rf "${E2E_DIR}" && mkdir -p "${E2E_DIR}" && exec ${REPO_ROOT}/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8001`,
      cwd: REPO_ROOT,
      url: 'http://127.0.0.1:8001/',
      reuseExistingServer: false,
      stdout: 'pipe',
      stderr: 'pipe',
      timeout: 30_000,
      env: {
        STATION_STUDIO_DB_URL: `sqlite:///${TEST_DB_PATH}`,
        STATION_STUDIO_STATIONS_DIR: TEST_STATIONS_DIR,
      },
    },
    {
      name: 'frontend',
      // §16.25.t5: use `vite preview` (production build) instead of
      // `vite dev`. Dev-mode HMR breaks Svelte event-handler
      // attachment specifically on PoolImportPanel (proved side-by-
      // side: same `on:submit|preventDefault` directive worked on
      // DetailPanel in the same Playwright run but failed on
      // PoolImportPanel; switching to preview made both work).
      // Always rebuilds first so source edits don't go stale between
      // runs — costs a few seconds on each `npm run test:e2e` invocation
      // but ensures correctness.
      command: 'npm run build && npm run preview -- --port 5173',
      cwd: __dirname,
      url: 'http://localhost:5173/',
      reuseExistingServer: false,
      stdout: 'pipe',
      stderr: 'pipe',
      // Build can take 5-10s; bump from 30s → 60s so slow CI doesn't trip.
      timeout: 60_000,
    },
  ],
});
