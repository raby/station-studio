import { test, expect } from '@playwright/test';
import { createStation, importTinyPool } from './fixtures';

/**
 * §16.2.2 c5 — Playwright covering the full read-only visualise flow.
 *
 * vitest pins the components in isolation; only an e2e run proves
 * the API → load → expand → render → click pipeline holds end-to-
 * end through the real Vite proxy + FastAPI + SvelteKit nav.
 *
 * Test stations are created via `createStation` (minimal template,
 * 1 atomic block). The endpoint returns a synthesized-trivial
 * sidecar (no committed `.editor.json` for the test slug), so the
 * canvas renders one "All slots" section with one chip.
 */
test.describe('§16.2.2 visualise canvas', () => {
  test('discovery link from station detail lands on visualise route', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'viz-discovery', 'Viz Discovery');

    await page.goto('/stations/viz-discovery');
    await expect(page.getByTestId('visualise-station')).toBeVisible();
    await page.getByTestId('visualise-station').click();
    await expect(page).toHaveURL(/\/stations\/viz-discovery\/visualise$/);
    await expect(page.getByTestId('editor-canvas')).toBeVisible();
  });

  test('synthesized-trivial sidecar renders one "All slots" section', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'viz-trivial', 'Viz Trivial');

    await page.goto('/stations/viz-trivial/visualise');
    await expect(page.getByTestId('editor-canvas')).toBeVisible();
    // Single section, default name from synthesize_trivial_sidecar.
    const section = page.getByTestId('section');
    await expect(section).toHaveCount(1);
    await expect(page.getByTestId('section-name')).toHaveText('All slots');
    // One chip for the one atomic block in the minimal template.
    await expect(page.getByTestId('chip')).toHaveCount(1);
  });

  test('clicking a chip opens the DetailPanel with the engine spec', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'viz-detail', 'Viz Detail');

    await page.goto('/stations/viz-detail/visualise');
    await expect(page.getByTestId('chip')).toBeVisible();

    // Panel hidden by default.
    await expect(page.getByTestId('detail-panel')).toBeHidden();

    await page.getByTestId('chip').first().click();
    const panel = page.getByTestId('detail-panel');
    await expect(panel).toBeVisible();
    // Synthesized trivial section name + pick strategy from the
    // minimal template fixture.
    await expect(page.getByTestId('detail-section-name')).toHaveText('All slots');
    await expect(page.getByTestId('detail-strategy')).toHaveText('pick');
    await expect(page.getByTestId('detail-source')).toHaveText('A');
  });

  test('Escape closes the DetailPanel', async ({ page, request }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'viz-esc', 'Viz Esc');

    await page.goto('/stations/viz-esc/visualise');
    await page.getByTestId('chip').first().click();
    await expect(page.getByTestId('detail-panel')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('detail-panel')).toBeHidden();
  });

  test('ViewModeToggle: verbose label persists across reload', async ({
    page,
    request,
    context,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'viz-vm', 'Viz VM');

    await page.goto('/stations/viz-vm/visualise');
    // Default = compact. Chip shows the abbreviation 'Pk'.
    await expect(page.getByTestId('chip')).toHaveText('Pk');

    await page.getByTestId('vm-label-verbose').click();
    // Verbose label = "strategy (source)" = "pick (A)".
    await expect(page.getByTestId('chip')).toContainText('pick (A)');

    // Reload — localStorage preference should rehydrate.
    await page.reload();
    await expect(page.getByTestId('chip')).toContainText('pick (A)');
    await expect(page.getByTestId('vm-label-verbose')).toHaveAttribute(
      'aria-pressed', 'true',
    );

    // Cleanup for parallel-test isolation — localStorage is per-
    // origin and persists across tests in the same context.
    await context.clearCookies();
    await page.evaluate(() => window.localStorage.clear());
  });

  test('section collapse toggle works', async ({ page, request }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'viz-collapse', 'Viz Collapse');

    await page.goto('/stations/viz-collapse/visualise');
    // Section starts expanded (1 chip ≤ 500 threshold).
    await expect(page.getByTestId('section-body')).toBeVisible();
    await expect(page.getByTestId('chip')).toBeVisible();

    await page.getByTestId('section-toggle').click();
    await expect(page.getByTestId('section-body')).toBeHidden();
    await expect(page.getByTestId('chip')).toBeHidden();

    await page.getByTestId('section-toggle').click();
    await expect(page.getByTestId('section-body')).toBeVisible();
  });

  test('back link returns to station detail', async ({ page, request }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'viz-back', 'Viz Back');

    await page.goto('/stations/viz-back/visualise');
    await expect(page.getByRole('link', { name: '← viz-back' })).toBeVisible();
    await page.getByRole('link', { name: '← viz-back' }).click();
    await expect(page).toHaveURL(/\/stations\/viz-back$/);
  });
});
