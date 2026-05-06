import { test, expect } from '@playwright/test';
import { createStation, API } from './fixtures';

/**
 * §16.7 edit-mode specs — round-trip: load existing template into the
 * form, change a field, save, station detail reflects the new value.
 *
 * Vitest covers component-level wiring (slug-readonly, reset button,
 * dirty-pill flips, JSON preview, submit payload shape). Playwright
 * covers the integration nobody else can: real backend, Vite proxy,
 * SvelteKit goto, the detail page reading back the fresh template.
 */
test.describe('§16.7 edit mode', () => {
  test('Edit button on detail header navigates to /stations/[slug]/edit', async ({
    page,
    request,
  }) => {
    await createStation(request, 'edit-nav', 'Edit Nav');

    await page.goto('/stations/edit-nav');
    await page.getByTestId('edit-station').click();

    await expect(page).toHaveURL(/\/stations\/edit-nav\/edit$/);
    await expect(
      page.getByRole('heading', { name: /Edit Edit Nav/ }),
    ).toBeVisible();
  });

  test('edit form prepopulates with the loaded template', async ({ page, request }) => {
    await createStation(request, 'edit-prepop', 'Edit Prepop');

    await page.goto('/stations/edit-prepop/edit');
    await expect(page.locator('html[data-wizard-ready]')).toBeAttached();

    // Display name shows the loaded value.
    await expect(page.getByLabel('Display name')).toHaveValue('Edit Prepop');
    // Slug input shows the existing slug AND is readonly (D3).
    const slugInput = page.getByLabel('Slug');
    await expect(slugInput).toHaveValue('edit-prepop');
    await expect(slugInput).toHaveAttribute('readonly');
  });

  test('changing display name and saving lands on detail with the new name', async ({
    page,
    request,
  }) => {
    await createStation(request, 'edit-rename', 'Original Name');

    await page.goto('/stations/edit-rename/edit');
    await expect(page.locator('html[data-wizard-ready]')).toBeAttached();

    await page.getByLabel('Display name').fill('Renamed Station');
    await page.getByRole('button', { name: 'Save changes' }).click();

    // Lands on detail page; heading reflects the new name.
    await expect(page).toHaveURL(/\/stations\/edit-rename$/);
    await expect(
      page.getByRole('heading', { name: 'Renamed Station' }),
    ).toBeVisible();

    // Backend reflects it too (sanity).
    const r = await request.get(`${API}/api/stations/edit-rename`);
    expect((await r.json()).name).toBe('Renamed Station');
  });

  test('reset-to-saved reverts edits with a confirm dialog', async ({
    page,
    request,
  }) => {
    await createStation(request, 'edit-reset', 'Reset Test');

    await page.goto('/stations/edit-reset/edit');
    await expect(page.locator('html[data-wizard-ready]')).toBeAttached();

    await page.getByLabel('Display name').fill('Tweaked');
    // Reset button enabled now that the form is dirty.
    page.once('dialog', (d) => d.accept());
    await page.getByTestId('reset-to-saved').click();

    await expect(page.getByLabel('Display name')).toHaveValue('Reset Test');
  });

  test('JSON preview disclosure renders the live template', async ({
    page,
    request,
  }) => {
    await createStation(request, 'edit-preview', 'Edit Preview');

    await page.goto('/stations/edit-preview/edit');
    await expect(page.locator('html[data-wizard-ready]')).toBeAttached();

    const preview = page.getByTestId('json-preview');
    await preview.click(); // expand the disclosure
    // The body contains a <pre> with parseable JSON; spot-check the slug.
    await expect(preview.locator('pre')).toContainText('"station": "edit-preview"');
  });
});
