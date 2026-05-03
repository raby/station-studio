import { test, expect } from '@playwright/test';
import { createStation } from './fixtures';

test.describe('landing page', () => {
  test('shows the heading and the + new station button', async ({ page }) => {
    // Note: this used to also assert the empty-state hint ("No stations
    // yet" / "Bootstrap Rebirth Classics"), but that copy is only visible
    // when the DB has 0 or 1 stations. As the suite grew (generate.spec.ts
    // is alphabetically earlier and seeds several stations), depending on
    // an empty DB became flaky. The empty-state hint is now exercised in
    // the Vitest layer (landing.test.ts) where the API response is mocked.
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Station Studio' })).toBeVisible();
    await expect(page.getByRole('link', { name: /\+ new station/ })).toBeVisible();
  });

  test('lists stations after one is created via the API', async ({ page, request }) => {
    await createStation(request, 'landing-a', 'Landing A');
    await page.goto('/');
    const link = page.getByRole('link', { name: 'Landing A' });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', '/stations/landing-a');
  });
});
