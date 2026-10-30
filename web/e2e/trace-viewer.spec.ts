import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, generate, minimalTemplate, API } from './fixtures';
import { assertNoA11yViolations } from './a11y';

test.describe('trace viewer', () => {
  test('§16.5 c2 — trace viewer passes axe-core WCAG audit (expanded + collapsed)', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'trv-a11y', 'Trace View A11y');
    const { playlist_id } = await generate(request, 'trv-a11y', 1);
    await page.goto(`/playlists/${playlist_id}`);
    await expect(
      page.getByRole('heading', { name: 'Your working' }),
    ).toBeVisible();
    // First pass: every row expanded — exercises aria-expanded=true,
    // aria-controls pointing at a rendered <table id>, filter inputs
    // with aria-labels.
    await assertNoA11yViolations(page);

    // §16.5 c2-review R7 — second pass after toggling a row collapsed.
    // The aria-controls IDREF now points at an unmounted element
    // (ARIA-spec-allowed dangling reference); axe shouldn't flag it,
    // but the assertion locks the contract so a future regression
    // (e.g. someone changing the IDREF format) fails CI.
    await page.locator('section.row > header[role="button"]').first().click();
    await assertNoA11yViolations(page);
  });

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

  test('§16.9 multi-week prev/next: link navigates to sibling trace', async ({
    page,
    request,
  }) => {
    // Round-trip pin: Vitest covers the prev/next derivation against
    // mocked $lib/api; this spec covers the integration — the
    // getStationPlaylists fetch lands real data, the link's href
    // resolves correctly, and SvelteKit's client-side navigation
    // loads the sibling trace without a full page reload.
    await importTinyPool(request, 'A');
    await createStation(request, 'trv-nav', 'Nav Station');
    const wk1 = await generate(request, 'trv-nav', 1);
    const wk2 = await generate(request, 'trv-nav', 2);

    // Land on week 1's trace. Prev should be disabled; next should
    // point at week 2's playlist id.
    await page.goto(`/playlists/${wk1.playlist_id}`);
    await expect(page.getByTestId('week-nav-prev-disabled')).toBeVisible();
    const next = page.getByTestId('week-nav-next');
    await expect(next).toHaveAttribute(
      'href',
      `/playlists/${wk2.playlist_id}`,
    );

    // Click next → end up on week 2; prev now points back to week 1,
    // next is the disabled placeholder.
    await next.click();
    await expect(page).toHaveURL(`/playlists/${wk2.playlist_id}`);
    await expect(page.getByTestId('week-nav-prev')).toHaveAttribute(
      'href',
      `/playlists/${wk1.playlist_id}`,
    );
    await expect(page.getByTestId('week-nav-next-disabled')).toBeVisible();
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

  test('Songs view: each song with its rule and why, and it passes axe', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'trv-songs', 'Trace View Songs');
    const { playlist_id } = await generate(request, 'trv-songs', 1);
    await page.goto(`/playlists/${playlist_id}`);

    await page.getByTestId('week-view-songs').click();
    const rows = page.getByTestId('song-row');
    await expect(rows.first()).toBeVisible();
    await expect(page.getByTestId('song-rule').first()).toContainText('from A');
    await expect(page.getByTestId('song-why').first()).not.toBeEmpty();
    await assertNoA11yViolations(page);

    // Remembered on the next visit.
    await page.reload();
    await expect(page.getByTestId('song-list')).toBeVisible();
  });

  test('Songs view: change a song\'s rule, preview, save, regenerate the week', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    const t = minimalTemplate('trv-edit');
    t.layout.unit_labels = ['Mon', 'Tue'];
    t.layout.row_start_offsets = [0, 0];
    t.layout.row_template = [
      { source: 'A', strategy: 'pick', size: 1 },
      { source: 'A', strategy: 'window', size: 2 },
    ] as typeof t.layout.row_template;
    await createStation(request, 'trv-edit', 'Trace View Edit', 'A', t);
    const { playlist_id } = await generate(request, 'trv-edit', 1);
    const week2 = await generate(request, 'trv-edit', 2);
    await page.goto(`/playlists/${playlist_id}`);
    await page.getByTestId('week-view-songs').click();

    // Song 2 comes from the window rule (block 2 of each row).
    const rows = page.getByTestId('song-row');
    await expect(page.getByTestId('song-rule').nth(1)).toContainText('2 in a row from A');
    await rows.nth(1).click();
    await expect(page.getByTestId('detail-panel')).toBeVisible();
    // Both rows' window songs are the same rule.
    await expect(page.locator('[data-testid="song-row"].related')).toHaveCount(4);

    await page.getByTestId('retype-toggle').click();
    await page.getByTestId('retype-strategy').selectOption('pick');
    await page.getByTestId('retype-apply').click();
    await expect(page.getByTestId('song-edit-count')).toContainText('1 change');
    // A real preview of the edited rules replaces the list.
    await expect(page.getByTestId('song-rule').nth(1)).toContainText('Picked from A');
    // The bar lists the change in plain words and says how many songs move.
    await expect(page.getByTestId('song-edit-item')).toContainText('rule 2: Picked from A');
    await expect(page.getByTestId('song-edit-moved')).toContainText('in week 1 change');
    await assertNoA11yViolations(page);

    await page.getByTestId('song-edit-save').click();
    await expect(page.getByTestId('song-edit-saved')).toBeVisible();
    // Saved, not yet regenerated: the week's window songs say their rule
    // has changed since.
    await expect(page.getByTestId('song-rule').nth(1)).toContainText('rule changed since');
    // Week 2 was made with the old rule too: regenerate both, in order.
    await expect(page.getByTestId('song-edit-saved')).toContainText('the week after it (week 2)');
    await page.getByTestId('song-edit-regenerate-later').click();
    await expect(page.getByTestId('song-edit-bar')).toHaveCount(0);
    // The regenerated weeks were made with the saved rule.
    await expect(page.getByTestId('song-rule').nth(1)).toContainText('Picked from A');
    await expect(page.getByTestId('song-why').nth(1)).toContainText('pick from');
    await page.goto(`/playlists/${week2.playlist_id}`);
    await expect(page.getByTestId('song-rule').nth(1)).toContainText('Picked from A');
    await expect(page.getByTestId('song-rule').nth(1)).not.toContainText('rule changed since');
  });

  test('Songs view: change a rule and the identical ones with it', async ({ page, request }) => {
    await importTinyPool(request, 'A');
    const t = minimalTemplate('trv-twins');
    t.layout.unit_labels = ['Mon', 'Tue'];
    t.layout.row_start_offsets = [0, 0];
    t.layout.row_template = [
      { source: 'A', strategy: 'weighted', size: 1 },
      { source: 'A', strategy: 'pick', size: 1 },
      { source: 'A', strategy: 'weighted', size: 1 },
    ] as typeof t.layout.row_template;
    await createStation(request, 'trv-twins', 'Trace View Twins', 'A', t);
    const { playlist_id } = await generate(request, 'trv-twins', 1);
    await page.goto(`/playlists/${playlist_id}`);
    await page.getByTestId('week-view-songs').click();

    await page.getByTestId('song-row').first().click();
    await expect(page.getByTestId('also-identical')).toContainText('1 other rule just like this one');
    await page.getByTestId('also-identical-input').check();
    await page.getByTestId('input-window').fill('3');
    await page.getByTestId('detail-save').click();
    await expect(page.getByTestId('song-edit-count')).toContainText('2 changes');
    await expect(page.getByTestId('song-edit-item')).toHaveCount(2);
    await page.getByTestId('detail-save').hover(); // the button stays readable on hover
    await assertNoA11yViolations(page);

    await page.getByTestId('song-edit-save').click();
    await expect(page.getByTestId('song-edit-saved')).toBeVisible();
    const saved = await (await request.get(`${API}/api/stations/trv-twins/sidecar`)).json();
    const row = saved.template.layout.row_template;
    expect([row[0].window, row[1].window, row[2].window]).toEqual([3, undefined, 3]);
  });
});
