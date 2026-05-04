import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, generate } from './fixtures';

/**
 * §16.3 Delete affordances — end-to-end against a real backend.
 *
 * Vitest covers the wiring (mock DELETE, confirm() yes/no, refetch on
 * success). Playwright covers the FastAPI cascade and SvelteKit
 * navigation: per-row delete shrinks the table; station nuke makes
 * the URL 404 and lands the user back on the stations list.
 */
test.describe('§16.3 delete affordances', () => {
  test('per-row delete: confirm → DELETE 204 → row vanishes', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'del-row', 'Del Row');
    await generate(request, 'del-row', 1);
    await generate(request, 'del-row', 2);

    await page.goto('/stations/del-row');
    await expect(page.locator('table tbody tr')).toHaveCount(2);

    page.once('dialog', (d) => d.accept());
    await page.getByTestId('delete-row-1').click();

    // After the refetch, the table is one row shorter and week 2 remains.
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.locator('table tbody tr td').first()).toHaveText('002');
  });

  test('per-row delete: cancel keeps the row in place', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'del-cancel', 'Del Cancel');
    await generate(request, 'del-cancel', 1);

    await page.goto('/stations/del-cancel');
    page.once('dialog', (d) => d.dismiss());
    await page.getByTestId('delete-row-1').click();

    // No DOM change: still one row, still week 1.
    await expect(page.locator('table tbody tr')).toHaveCount(1);
    await expect(page.locator('table tbody tr td').first()).toHaveText('001');
  });

  test('station nuke: confirm → DELETE 204 → land on landing without it', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'del-nuke', 'Del Nuke');
    await generate(request, 'del-nuke', 1);

    await page.goto('/stations/del-nuke');
    page.once('dialog', (d) => d.accept());
    await page.getByTestId('delete-station').click();

    // Lands on landing.
    await expect(page).toHaveURL(/\/$/);
    // The nuked station's row is gone.
    await expect(page.getByRole('link', { name: 'Del Nuke' })).toHaveCount(0);
  });

  test('station nuke: cancel stays on the detail page', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'del-nuke-cancel', 'Del Nuke Cancel');

    await page.goto('/stations/del-nuke-cancel');
    page.once('dialog', (d) => d.dismiss());
    await page.getByTestId('delete-station').click();

    // No navigation; still on the same URL.
    await expect(page).toHaveURL(/\/stations\/del-nuke-cancel$/);
    await expect(
      page.getByRole('heading', { name: 'Del Nuke Cancel' }),
    ).toBeVisible();
  });
});
