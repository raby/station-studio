import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, generate } from './fixtures';
import { assertNoA11yViolations } from './a11y';

test.describe('station detail page', () => {
  test('shows the empty-state CTA when the station is fresh', async ({
    page,
    request,
  }) => {
    await createStation(request, 'det-empty', 'Det Empty');
    await page.goto('/stations/det-empty');
    await expect(page.getByRole('heading', { name: 'Det Empty' })).toBeVisible();
    // The §16.1 empty-state replaces the old hint-only copy with a primary
    // CTA. The hint sentence is still there, just rephrased.
    await expect(page.getByTestId('generate-empty-cta')).toBeVisible();
    await expect(page.getByText(/none generated yet/i)).toBeVisible();
  });

  test('§16.5 c4 — /stations/[slug] passes axe-core WCAG audit', async ({
    page,
    request,
  }) => {
    // Audit the populated-table path so the c4 visually-hidden
    // <th> labels are in scope. The empty-state path covers a
    // different DOM and could regress separately; cover the
    // playlists-table presence since that's where the §14.5
    // gap-list entries live.
    await importTinyPool(request, 'A');
    await createStation(request, 'det-a11y', 'Det A11y');
    await generate(request, 'det-a11y', 1);
    await page.goto('/stations/det-a11y');
    await expect(page.getByTestId('playlists-table')).toBeVisible();
    await assertNoA11yViolations(page);
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
    const rows = page.locator('[data-testid="playlists-table"] tbody tr');
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
