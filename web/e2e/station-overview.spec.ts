import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, generate } from './fixtures';

// §16.19 — the rich station-detail overview (template summary / source
// pools / generation rollup). The sections are additive and fetched
// non-fatally, so these assertions scope to unique slugs (shared-DB
// single-process backend caveat).

test.describe('station overview (§16.19)', () => {
  test('surfaces a declared-but-not-imported pool as a setup gap', async ({
    page,
    request,
  }) => {
    // Source "ov-gap" is declared in the template but never imported.
    await createStation(request, 'ov-gap', 'Overview Gap', 'ov-gap');
    await page.goto('/stations/ov-gap');

    const pools = page.getByTestId('pools-summary');
    await expect(pools).toBeVisible();
    await expect(pools).toContainText('ov-gap');
    // The un-imported source is flagged so the operator knows generate
    // would 400.
    await expect(page.getByTestId('pool-missing')).toHaveText(/not imported/i);
  });

  test('shows template summary + imported pool counts + generation rollup', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'ov-pool');
    await createStation(request, 'ov-full', 'Overview Full', 'ov-pool');
    await generate(request, 'ov-full', 1);

    await page.goto('/stations/ov-full');

    // Template card — one pick slot, row_template layout.
    const ts = page.getByTestId('template-summary');
    await expect(ts).toBeVisible();
    await expect(ts).toContainText('row_template');
    await expect(ts).toContainText(/pick ×1/);

    // Pools card — the imported pool shows its (3-track) count, no gap.
    const pools = page.getByTestId('pools-summary');
    await expect(pools).toContainText('ov-pool');
    await expect(pools).toContainText('3 tracks');
    await expect(page.getByTestId('pool-missing')).toHaveCount(0);

    // Generations card — one week generated.
    const gens = page.getByTestId('generations-summary');
    await expect(gens).toBeVisible();
    await expect(gens).toContainText('WEEKLY 001');

    // The visualise deep-link is present.
    await expect(page.getByTestId('overview-visualise-link')).toHaveAttribute(
      'href',
      /\/stations\/ov-full\/visualise/,
    );
  });
});
