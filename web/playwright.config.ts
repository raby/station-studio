import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '..');

// Per-process SQLite file. Including the Playwright process PID keeps two
// concurrent `make test-e2e` runs on the same checkout from stomping each
// other's DB; the backend command below wipes its own file on boot.
const TEST_DB_PATH = path.join(REPO_ROOT, `playwright-test.${process.pid}.db`);

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
  // DB wipe is done by the backend webServer command itself (see below),
  // since Playwright starts webServers before globalSetup runs.
  webServer: [
    {
      name: 'backend',
      // Wipe the test DB *before* uvicorn imports api.deps (which runs
      // create_all at module-import). Doing this inside globalSetup races:
      // Playwright boots webServers before globalSetup runs, so a stale DB
      // file gets created/opened first and then deleted out from under the
      // connection pool, leaving queries to hit an empty file.
      command: `rm -f "${TEST_DB_PATH}" "${TEST_DB_PATH}-wal" "${TEST_DB_PATH}-shm" && exec ${REPO_ROOT}/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8001`,
      cwd: REPO_ROOT,
      url: 'http://127.0.0.1:8001/',
      reuseExistingServer: false,
      stdout: 'pipe',
      stderr: 'pipe',
      timeout: 30_000,
      env: {
        STATION_STUDIO_DB_URL: `sqlite:///${TEST_DB_PATH}`,
      },
    },
    {
      name: 'frontend',
      command: 'npm run dev -- --port 5173',
      cwd: __dirname,
      url: 'http://localhost:5173/',
      reuseExistingServer: false,
      stdout: 'pipe',
      stderr: 'pipe',
      timeout: 30_000,
    },
  ],
});
