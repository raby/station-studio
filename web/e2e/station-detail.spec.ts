import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, generate } from './fixtures';

test.describe('station detail page', () => {
  test('shows "none generated yet" when the station is fresh', async ({
    page,
    request,
  }) => {
    await createStation(request, 'det-empty', 'Det Empty');
    await page.goto('/stations/det-empty');
    await expect(page.getByRole('heading', { name: 'Det Empty' })).toBeVisible();
    await expect(page.getByText('None generated yet.')).toBeVisible();
  });

  test('lists generated playlists in a table with week, slots, trace', async ({
    page,
    request,
  }) => {
    // A pool named "A" matching the template's source name.
    await importTinyPool(request, 'A');
    await createStation(request, 'det-list', 'Det List');
    await generate(request, 'det-list', 1);
    await generate(request, 'det-list', 2);

    await page.goto('/stations/det-list');
    await expect(page.getByRole('heading', { name: 'Det List' })).toBeVisible();

    // One row per week, ordered 001 then 002.
    const rows = page.locator('table tbody tr');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0).locator('td').first()).toHaveText('001');
    await expect(rows.nth(1).locator('td').first()).toHaveText('002');

    // view link routes to the trace viewer.
    await expect(rows.first().getByRole('link', { name: /view/ })).toHaveAttribute(
      'href',
      /\/playlists\/\d+/,
    );
  });
});
