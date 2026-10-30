import fs from 'node:fs';

/**
 * Deletes the run's scratch folder (test DB + stations dir) that
 * playwright.config.ts put in the OS temp dir. Runs before Playwright
 * stops the webServers; the backend still has the SQLite file open,
 * which macOS and Linux allow. A run killed before teardown leaves the
 * folder in the temp dir, which the OS clears, never in the repo.
 */
export default function globalTeardown(): void {
  const dir = process.env.STATION_STUDIO_E2E_DIR;
  // Unset only if the config never ran; nothing to delete then.
  if (!dir) return;
  fs.rmSync(dir, { recursive: true, force: true });
}
