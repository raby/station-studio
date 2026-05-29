import { test, expect } from '@playwright/test';
import { API, createStation, importTinyPool, generate, minimalTemplate } from './fixtures';

/**
 * §16.12 c3 — Playwright for the stations-list polish.
 *
 * vitest pins the pure helpers + component render; this proves the
 * search / filter / sort + last-generated line work end-to-end
 * against the real GET /api/stations aggregates.
 *
 * Shared-DB caveat: the Playwright backend is single-process, so
 * other specs seed stations too. Every assertion here scopes to
 * THIS spec's uniquely-slugged stations (sl12-*) via row selectors —
 * never total counts.
 */
test.describe('§16.12 stations list polish', () => {
  test('search narrows to the matching station', async ({ page, request }) => {
    await createStation(request, 'sl12-jazz', 'SL12 Jazz');
    await createStation(request, 'sl12-rock', 'SL12 Rock');

    await page.goto('/');
    await page.getByTestId('station-search').fill('sl12-jazz');

    await expect(page.locator('[data-slug="sl12-jazz"]')).toBeVisible();
    await expect(page.locator('[data-slug="sl12-rock"]')).toHaveCount(0);
  });

  test('cadence filter chip shows only that cadence', async ({ page, request }) => {
    // Weekly via the shared helper; monthly via an inline custom template.
    await createStation(request, 'sl12-weekly', 'SL12 Weekly');
    const monthlyTemplate = { ...minimalTemplate('sl12-monthly'), cadence: 'monthly' };
    const r = await request.post(`${API}/api/stations`, {
      data: { slug: 'sl12-monthly', name: 'SL12 Monthly', template: monthlyTemplate },
    });
    expect(r.status()).toBe(201);

    await page.goto('/');
    // Scope the search to our pair first so other specs' weekly
    // stations don't crowd the assertion.
    await page.getByTestId('station-search').fill('sl12-');
    await page.getByTestId('cadence-monthly').click();

    await expect(page.locator('[data-slug="sl12-monthly"]')).toBeVisible();
    await expect(page.locator('[data-slug="sl12-weekly"]')).toHaveCount(0);
  });

  test('generated filter + last-generated line', async ({ page, request }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'sl12-gen', 'SL12 Gen');
    await createStation(request, 'sl12-empty', 'SL12 Empty');
    await generate(request, 'sl12-gen', 1);

    await page.goto('/');
    await page.getByTestId('station-search').fill('sl12-');

    // Both visible under "all".
    await expect(page.locator('[data-slug="sl12-gen"]')).toBeVisible();
    await expect(page.locator('[data-slug="sl12-empty"]')).toBeVisible();

    // The generated row shows a last-generated line; the empty one shows
    // the never-generated muted line.
    await expect(
      page.locator('[data-slug="sl12-gen"]').getByTestId('last-generated'),
    ).toContainText('WEEKLY 001');
    await expect(
      page.locator('[data-slug="sl12-empty"]').getByTestId('never-generated'),
    ).toBeVisible();

    // "has generations" filter hides the empty one.
    await page.getByTestId('gen-has').click();
    await expect(page.locator('[data-slug="sl12-gen"]')).toBeVisible();
    await expect(page.locator('[data-slug="sl12-empty"]')).toHaveCount(0);

    // "empty" flips it.
    await page.getByTestId('gen-empty').click();
    await expect(page.locator('[data-slug="sl12-empty"]')).toBeVisible();
    await expect(page.locator('[data-slug="sl12-gen"]')).toHaveCount(0);
  });

  test('sort by name orders this spec\'s rows alphabetically', async ({
    page,
    request,
  }) => {
    await createStation(request, 'sl12-sort-c', 'SL12 Sort Charlie');
    await createStation(request, 'sl12-sort-a', 'SL12 Sort Alpha');
    await createStation(request, 'sl12-sort-b', 'SL12 Sort Bravo');

    await page.goto('/');
    await page.getByTestId('station-search').fill('SL12 Sort');
    await page.getByTestId('sort-name').click();

    const slugs = await page
      .locator('[data-testid="station-row"]')
      .evaluateAll((rows) => rows.map((r) => r.getAttribute('data-slug')));
    expect(slugs).toEqual(['sl12-sort-a', 'sl12-sort-b', 'sl12-sort-c']);
  });

  test('no-matches message when the search excludes everything', async ({
    page,
    request,
  }) => {
    await createStation(request, 'sl12-nomatch', 'SL12 NoMatch');
    await page.goto('/');
    await page.getByTestId('station-search').fill('zzz-definitely-not-a-station-xyz');
    await expect(page.getByTestId('no-matches')).toBeVisible();
  });
});
