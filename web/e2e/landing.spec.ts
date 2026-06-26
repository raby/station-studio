import { test, expect } from '@playwright/test';
import { createStation } from './fixtures';
import { assertNoA11yViolations } from './a11y';

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

  test('§16.5 c1 — landing page passes axe-core WCAG audit', async ({ page }) => {
    // Smoke test for the §16.5 c1 a11y CI gate. The landing page is
    // the cheapest surface to wire first: stable copy, no data
    // fetches required, exercises the shared +layout chrome
    // (skip-link, focus ring, <main id="main-content">).
    // Subsequent slices (c2/c3/c4) add per-surface audits.
    await page.goto('/');
    await expect(
      page.getByRole('heading', { name: 'Station Studio' }),
    ).toBeVisible();
    await assertNoA11yViolations(page);
  });
});
