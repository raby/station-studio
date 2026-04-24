import { test, expect } from '@playwright/test';
import { createStation } from './fixtures';

test.describe('landing page', () => {
  test('shows the + new station button and an empty-state hint', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Station Studio' })).toBeVisible();
    await expect(page.getByRole('link', { name: /\+ new station/ })).toBeVisible();
    await expect(
      page.getByText(/No stations yet|Bootstrap Rebirth Classics/),
    ).toBeVisible();
  });

  test('lists stations after one is created via the API', async ({ page, request }) => {
    await createStation(request, 'landing-a', 'Landing A');
    await page.goto('/');
    const link = page.getByRole('link', { name: 'Landing A' });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', '/stations/landing-a');
  });
});
