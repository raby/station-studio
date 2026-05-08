import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, generate, API } from './fixtures';

test.describe('trace viewer', () => {
  test('renders slot rows and a Download iTunes XML link', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'trv', 'Trace View Station');
    const { playlist_id } = await generate(request, 'trv', 1);

    await page.goto(`/playlists/${playlist_id}`);

    await expect(
      page.getByRole('heading', { name: /Trace View Station/ }),
    ).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Your working' })).toBeVisible();

    // At least one slot row must appear, with source chip 'A'.
    const chips = page.locator('.chip');
    await expect(chips.first()).toBeVisible();
    await expect(chips.first()).toHaveText('A');

    // Download link targets the export endpoint.
    const dl = page.getByRole('link', { name: 'Download iTunes XML' });
    await expect(dl).toHaveAttribute(
      'href',
      `/api/playlists/${playlist_id}/export`,
    );
  });

  test('download link returns an iTunes-shaped plist', async ({ request }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'trv-dl', 'Download Station');
    const { playlist_id } = await generate(request, 'trv-dl', 1);

    const r = await request.get(`${API}/api/playlists/${playlist_id}/export`);
    expect(r.status()).toBe(200);
    const body = await r.text();
    expect(body).toContain('<?xml');
    expect(body).toContain('//Apple Computer//DTD PLIST 1.0//EN');
    expect(body).toContain('Download Station 001'); // playlist Name derives from station.name + week
  });

  test('Export to disk: click → POST → success status renders the path + size', async ({
    page,
    request,
  }) => {
    // §16.6 — disk-export button on the trace viewer. The endpoint already
    // existed (§14.2); this verifies the UI hookup writes the file and
    // surfaces the path.
    //
    // Side effect: this test writes <repo>/out/trv-disk/001.xml on every
    // run because the per-PID test DB has no Config row, so the backend
    // falls back to "out/" relative to its CWD (the repo root). The path
    // is gitignored (.gitignore: out/) and re-runs overwrite the same
    // file rather than accumulating. The repo-root `out/` is shared with
    // any real-data exports (e.g. bootstrap_classics's), so we deliberately
    // don't clean it from `make clean-test` — `git clean -fdx out/` if you
    // need a sweep.
    await importTinyPool(request, 'A');
    await createStation(request, 'trv-disk', 'Disk Station');
    const { playlist_id } = await generate(request, 'trv-disk', 1);

    await page.goto(`/playlists/${playlist_id}`);
    await page.getByTestId('export-to-disk').click();

    // Status line shows up with the absolute path and a "KB" size figure.
    // The exact path varies (CWD-relative vs configured); just match the
    // station + week bits which are stable.
    await expect(page.getByTestId('disk-export-status')).toContainText(
      /trv-disk\/001\.xml/,
    );
    await expect(page.getByTestId('disk-export-status')).toContainText(/KB/);
  });

  test('§16.8 search / filter: typing narrows visible rows; clear restores them', async ({
    page,
    request,
  }) => {
    // Round-trip pin: Vitest covers the predicate combinations via
    // mocked $lib/api; this spec covers the integration — real backend,
    // real reactive cycle, real DOM update — so the wiring can't break
    // in a way Vitest misses.
    await importTinyPool(request, 'A');
    await createStation(request, 'trv-filter', 'Filter Station');
    const { playlist_id } = await generate(request, 'trv-filter', 1);

    await page.goto(`/playlists/${playlist_id}`);
    await expect(page.getByTestId('filter-bar')).toBeVisible();

    // Initial state: counter shows "Showing N of N" with the same number
    // on each side (no filter active). The tiny-library fixture has 3
    // tracks total, so a 1-slot pick from pool A means N=1.
    const counter = page.getByTestId('filter-counter');
    await expect(counter).toHaveText(/showing\s+1\s+of\s+1/i);

    // Type a search that matches no tracks → empty state appears,
    // counter goes to 0.
    await page.getByTestId('filter-search').fill('zzz-no-such-track');
    await expect(page.getByTestId('filter-empty')).toBeVisible();
    await expect(counter).toHaveText(/showing\s+0\s+of\s+1/i);

    // Clear button restores the original view.
    await page.getByTestId('filter-clear').click();
    await expect(page.getByTestId('filter-empty')).not.toBeVisible();
    await expect(counter).toHaveText(/showing\s+1\s+of\s+1/i);
  });
});
