import { test, expect } from '@playwright/test';
import { API, createStation } from './fixtures';

/**
 * §16.10 c3 — Playwright for the per-station Rules editor.
 *
 * vitest pins the RulesPanel component in isolation (mocked api);
 * this proves the discovery link + route + real
 * GET/PUT/DELETE /api/stations/{slug}/rules round-trip.
 *
 * Shared-DB-safe: each test uses a uniquely-slugged station
 * (rules10-*).
 *
 * §16.14.t10 sweep SR1: row count bumped from 4 → 5 to include
 * the list rule `cross_pool_dedup_sources` (the t10 GET endpoint
 * surfaces both int + list rules; the panel renders the list row
 * as a read-only placeholder until §16.14.t11 ships the editor).
 */
test.describe('§16.10 rules editor', () => {
  test('discovery link from station detail lands on the rules route', async ({
    page,
    request,
  }) => {
    await createStation(request, 'rules10-nav', 'Rules10 Nav');
    await page.goto('/stations/rules10-nav');
    await page.getByTestId('rules-station').click();
    await expect(page).toHaveURL(/\/stations\/rules10-nav\/rules$/);
    await expect(page.getByTestId('rules-panel')).toBeVisible();
  });

  test('lists every engine rule (int + list) as defaults on a fresh station', async ({
    page,
    request,
  }) => {
    await createStation(request, 'rules10-list', 'Rules10 List');
    await page.goto('/stations/rules10-list/rules');
    const rows = page.getByTestId('rule-row');
    // §16.14.t10: 4 int rules + 1 list rule (cross_pool_dedup_sources).
    await expect(rows).toHaveCount(5);
    // All unset → every row shows the default badge, none overridden.
    await expect(page.getByTestId('default-badge')).toHaveCount(5);
    await expect(page.getByTestId('override-badge')).toHaveCount(0);
    // §16.14.t10: one row is the list-rule placeholder; the
    // others are int rows with editable inputs.
    await expect(page.getByTestId('rule-list-placeholder')).toHaveCount(1);
    await expect(page.getByTestId('rule-input')).toHaveCount(4);
  });

  test('save an override then reset it — full round-trip', async ({
    page,
    request,
  }) => {
    await createStation(request, 'rules10-edit', 'Rules10 Edit');
    await page.goto('/stations/rules10-edit/rules');

    // Edit the first rule's value + Save.
    const firstRow = page.getByTestId('rule-row').first();
    const input = firstRow.getByTestId('rule-input');
    await input.fill('7');
    await firstRow.getByTestId('rule-save').click();

    // Row flips to overridden.
    await expect(firstRow.getByTestId('override-badge')).toBeVisible();

    // Verify it actually persisted via the API.
    const key = await firstRow.getAttribute('data-key');
    const r = await request.get(`${API}/api/stations/rules10-edit/rules`);
    const body = await r.json();
    const saved = body.rules.find((x: { key: string }) => x.key === key);
    expect(saved.value).toBe(7);
    expect(saved.is_override).toBe(true);

    // Reset → back to default, badge flips, override cleared server-side.
    await firstRow.getByTestId('rule-reset').click();
    await expect(firstRow.getByTestId('default-badge')).toBeVisible();
    const r2 = await request.get(`${API}/api/stations/rules10-edit/rules`);
    const body2 = await r2.json();
    const cleared = body2.rules.find((x: { key: string }) => x.key === key);
    expect(cleared.is_override).toBe(false);
  });

  test('out-of-range value surfaces a backend error inline', async ({
    page,
    request,
  }) => {
    await createStation(request, 'rules10-badval', 'Rules10 BadVal');
    await page.goto('/stations/rules10-badval/rules');

    // artist_dedup_window caps at 1000. Type within the HTML max so the
    // browser doesn't clamp, but past it via JS fill, then Save. The
    // client-side validator catches >max first → inline row error,
    // no navigation, no persisted change.
    const row = page.locator('[data-key="artist_dedup_window"]');
    await row.getByTestId('rule-input').fill('99999');
    await row.getByTestId('rule-save').click();
    await expect(row.getByTestId('rule-row-error')).toContainText('1000');
  });

  test('back crumb returns to station detail', async ({ page, request }) => {
    await createStation(request, 'rules10-back', 'Rules10 Back');
    await page.goto('/stations/rules10-back/rules');
    await page.getByRole('link', { name: '← rules10-back' }).click();
    await expect(page).toHaveURL(/\/stations\/rules10-back$/);
  });
});
