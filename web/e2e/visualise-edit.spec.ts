import { test, expect } from '@playwright/test';
import {
  API,
  createStation,
  importTinyPool,
  minimalFlatTemplate,
} from './fixtures';
import type { APIRequestContext } from '@playwright/test';

/**
 * §16.2.5 c2 — end-to-end click-through covering the §16.2.3 →
 * §16.2.4 → §16.2.5 surface against a real backend.
 *
 * One smoke test that drives the happy paths the slice promises:
 *   §16.2.3 — click chip → edit parameter field → Save → persists
 *   §16.2.4 — open retype panel → change source → Retype → applies
 *   §16.2.5 — Add Section → Move chip via dropdown → resize slot →
 *             two-click Delete-slot → all changes round-trip
 *
 * vitest already exercises each component in isolation; this e2e
 * proves the API → fetch → render → mutate → refetch pipeline holds
 * through the real Vite proxy + FastAPI + Svelte boundary.
 *
 * Uses a flat_slots template (NOT the row_template one
 * `minimalTemplate` returns) because structural edits to
 * row_template stations are §16.2.5 D8 punts — server rejects
 * with 400.
 */

// Slice-review sweep: the inline 30-line `createFlatStation` literal
// got hoisted into `web/e2e/fixtures.ts` as `minimalFlatTemplate` +
// reused via the existing `createStation` helper. The local wrapper
// stays only to keep the call-site signature compact.
async function createFlatStation(
  request: APIRequestContext,
  slug: string,
  sourceName = 'A',
): Promise<void> {
  await createStation(
    request, slug, slug, sourceName, minimalFlatTemplate(slug, sourceName),
  );
}


test.describe('§16.2.5 e2e — section + structural happy paths', () => {
  test('add section, move chip via dropdown, resize, two-click delete', async ({
    page, request,
  }) => {
    await importTinyPool(request, 'A');
    await createFlatStation(request, 'e2e-525');

    await page.goto('/stations/e2e-525/visualise');
    await expect(page.getByTestId('editor-canvas')).toBeVisible();

    // Synthesized trivial sidecar → 1 section, 3 chips.
    await expect(page.getByTestId('section-header')).toHaveCount(1);
    await expect(page.getByTestId('chip')).toHaveCount(3);

    // §16.2.5 — Add Section.
    await page.getByTestId('add-section').click();
    const idInput = page.getByTestId('add-section-input');
    await idInput.fill('mornings');
    await page.getByTestId('add-section-commit').click();
    // Wait for the new section to appear in the canvas.
    await expect(page.getByTestId('section-header')).toHaveCount(2);
    await expect(
      page.locator('[data-testid="section-header"][data-section-id="mornings"]')
    ).toBeVisible();

    // §16.2.5 — move chip via DetailPanel dropdown. Click chip 0 →
    // panel opens → pick 'mornings' from the Move dropdown → click
    // Move → chip relocates.
    await page.getByTestId('chip').first().click();
    await expect(page.getByTestId('detail-panel')).toBeVisible();
    await page.getByTestId('move-target').selectOption('mornings');
    await page.getByTestId('move-apply').click();
    // The canvas refetches; chip 0's section is now 'mornings'.
    // (The source section 'all-slots' loses one chip → 2 left.)
    await expect(page.getByTestId('chip')).toHaveCount(3);
    // mornings now has at least one chip.
    const morningsBody = page.locator(
      'section[data-section-id="mornings"] [data-testid="chip"]',
    );
    await expect(morningsBody).toHaveCount(1);

    // §16.2.5 — resize a slot. Click another chip (one still in
    // 'all-slots'), apply a size change.
    await page.locator(
      'section[data-section-id="all-slots"] [data-testid="chip"]',
    ).first().click();
    await expect(page.getByTestId('detail-panel')).toBeVisible();
    // The size input is seeded from the selected slot's current size.
    const sizeInput = page.getByTestId('structural-size-input');
    await sizeInput.fill('7');
    await page.getByTestId('structural-size-apply').click();
    // c2 review #3 fix #3: positive assertion that the PATCH landed.
    // Previously we waited for the panel to hide with a swallowed
    // catch; the panel actually stays open after a structural edit
    // (it just refetches), so the catch was masking a never-true
    // assertion. Hit the GET endpoint directly + assert the new
    // size is in the persisted template. `expect.poll` retries
    // until the refetch settles or the timeout fires.
    // §16.26 — was `/template`; that endpoint doesn't exist on the
    // API surface (StationOut at GET /api/stations/{slug} carries
    // the full template blob). The previous URL returned 404 so the
    // poll resolved to null on every attempt and the matcher errored
    // before the matcher's `toContain` semantics could even apply.
    await expect.poll(
      async () => {
        const r = await request.get(`${API}/api/stations/e2e-525`);
        if (r.status() !== 200) return null;
        const body = await r.json();
        const slots = body?.template?.layout?.flat_slots ?? [];
        return slots.map((s: { size?: number }) => s.size ?? null);
      },
      { timeout: 5_000 },
    ).toContain(7);
    // The canvas re-renders. Click the same chip and confirm size=7.
    await page.locator(
      'section[data-section-id="all-slots"] [data-testid="chip"]',
    ).first().click();
    await expect(sizeInput).toHaveValue('7');

    // §16.2.5 — two-click delete: first click arms the button +
    // changes its label; second click commits.
    const deleteBtn = page.getByTestId('structural-delete');
    await expect(deleteBtn).toHaveText('Remove slot');
    await deleteBtn.click();
    await expect(deleteBtn).toHaveText(/click again/i);
    await deleteBtn.click();
    // One fewer chip on the canvas (2 left from the original 3).
    await expect(page.getByTestId('chip')).toHaveCount(2);
  });

  test('rename section via SectionHeader pencil', async ({
    page, request,
  }) => {
    await importTinyPool(request, 'A');
    await createFlatStation(request, 'e2e-rename');

    await page.goto('/stations/e2e-rename/visualise');
    await expect(page.getByTestId('section-header')).toHaveCount(1);

    // The trivial fallback section is `all-slots` (id) / 'All slots'
    // (name). Rename via the ✎ button.
    await page.getByTestId('section-rename').click();
    const input = page.getByTestId('section-rename-input');
    await input.fill('Opening segment');
    await page.keyboard.press('Enter');
    // PATCH /sections/all-slots → refetch → new name visible.
    await expect(page.getByTestId('section-name')).toHaveText(
      'Opening segment',
    );
  });
});
