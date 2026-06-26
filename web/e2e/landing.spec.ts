import { test, expect } from '@playwright/test';
import { createStation } from './fixtures';
import { assertNoA11yViolations } from './a11y';

test.describe('landing page', () => {
  // §16.5 c1-review R3 — axe smoke check runs FIRST in this file
  // so the audit sees the landing page in whatever state earlier
  // alphabetic spec files left it (DB usually already has data
  // from `generate.spec.ts` etc), NOT after this file's own
  // `createStation('landing-a')` adds another row on top. The
  // important property is "audit hits the populated-list render
  // path" — exact row count isn't load-bearing. Keeping it first
  // also avoids the failure mode where a future intra-file test
  // mutates state in a way the audit doesn't expect.
  test('§16.5 c1 — landing page passes axe-core WCAG audit', async ({ page }) => {
    await page.goto('/');
    await expect(
      page.getByRole('heading', { name: 'Station Studio' }),
    ).toBeVisible();
    await assertNoA11yViolations(page);
  });

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
