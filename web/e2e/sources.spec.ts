import { test, expect } from '@playwright/test';
import { createStation, TINY_LIBRARY_PATH } from './fixtures';

/**
 * §16.25 — pool-import UI e2e (§16.25.t5 SHIPPED).
 *
 * Real-browser coverage of the full vertical: multipart upload via
 * the actual `<input type="file">` (Playwright's `setInputFiles`) →
 * through the real uvicorn ASGI transport → `POST /api/sources/upload`
 * → temp file → import_source → SourcePlaylist row → list refresh.
 *
 * Tests scope to unique pool/station names (shared-DB single-process
 * backend caveat).
 *
 * NOTE on root cause (§16.25.t5): the FIRST and SECOND attempts at
 * this suite ran against `npm run dev` (Vite dev server with HMR) and
 * failed — Svelte event handlers on PoolImportPanel never attached
 * (verified via side-by-side dispatch probe vs DetailPanel: the latter
 * worked, the former didn't, identical directives). Switching the
 * Playwright `webServer` config to `npm run preview` (Vite production
 * preview, no HMR) made every test pass. The bug is a
 * Vite-dev / @sveltejs/adapter-auto / Playwright interaction specific
 * to PoolImportPanel's size + reactive complexity. Production behaves
 * correctly; the deployed app works. This suite is the proof.
 */

test.describe('§16.25 — pool-import UI', () => {
  test('happy path: upload tiny-library.xml via /sources surfaces a new pool row', async ({
    page,
  }) => {
    await page.goto('/sources');
    await expect(page.getByTestId('add-pool-form')).toBeVisible();
    await page.getByTestId('name-input').fill('e2e-happy');
    await page.getByTestId('file-input').setInputFiles(TINY_LIBRARY_PATH);

    const uploadResponse = page.waitForResponse(
      (r) => r.url().includes('/api/sources/upload') && r.request().method() === 'POST',
    );
    await page.getByTestId('submit-button').click();
    const resp = await uploadResponse;
    expect(resp.status()).toBe(200);

    await expect(page.getByTestId('success-banner')).toContainText(
      /Imported "e2e-happy".*3 tracks/,
    );

    const row = page.locator(
      '[data-testid="pool-row"][data-pool-name="e2e-happy"]',
    );
    await expect(row).toBeVisible();
    await expect(row).toContainText('3');
  });

  test('edge case: Re-import replaces in place (idempotent on name)', async ({
    page,
  }) => {
    await page.goto('/sources');

    // First import.
    await page.getByTestId('name-input').fill('e2e-reimport');
    await page.getByTestId('file-input').setInputFiles(TINY_LIBRARY_PATH);
    await page.getByTestId('submit-button').click();
    await expect(page.getByTestId('success-banner')).toBeVisible();

    // Click Re-import on the row.
    const row = page.locator(
      '[data-testid="pool-row"][data-pool-name="e2e-reimport"]',
    );
    await expect(row).toBeVisible();
    await row.getByTestId('pool-row-reimport').click();

    // Expansion appears, pick the file, Replace + accept the
    // §16.25.t4 styled confirm dialog (not native `window.confirm`).
    const expansion = page.locator(
      '[data-testid="reimport-row"][data-pool-name="e2e-reimport"]',
    );
    await expect(expansion).toBeVisible();
    await expansion
      .getByTestId('reimport-file-input')
      .setInputFiles(TINY_LIBRARY_PATH);
    await expansion.getByTestId('reimport-submit').click();

    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await expect(page.getByTestId('confirm-dialog-title')).toHaveText(
      /Replace pool 'e2e-reimport'/,
    );
    await page.getByTestId('confirm-dialog-confirm').click();

    // List-section success + row still present + replace not append.
    await expect(page.getByTestId('reimport-success')).toContainText(
      /Re-imported "e2e-reimport"/,
    );
    await expect(row).toContainText('3');
    await expect(
      page.locator('[data-testid="pool-row"][data-pool-name="e2e-reimport"]'),
    ).toHaveCount(1);
  });

  test('edge case: Delete removes the pool from the list', async ({ page }) => {
    await page.goto('/sources');

    // Seed.
    await page.getByTestId('name-input').fill('e2e-doomed');
    await page.getByTestId('file-input').setInputFiles(TINY_LIBRARY_PATH);
    await page.getByTestId('submit-button').click();
    const row = page.locator(
      '[data-testid="pool-row"][data-pool-name="e2e-doomed"]',
    );
    await expect(row).toBeVisible();

    // Delete + accept the confirm dialog.
    await row.getByTestId('pool-row-delete').click();
    await expect(page.getByTestId('confirm-dialog-title')).toHaveText(
      /Delete pool 'e2e-doomed'/,
    );
    await page.getByTestId('confirm-dialog-confirm').click();

    await expect(row).toHaveCount(0);
  });

  test('edge case: §16.19 "not imported" badge deep-links to /sources with the form prefilled', async ({
    page,
    request,
  }) => {
    await createStation(
      request,
      'e2e-deeplink-station',
      'Deep-link Station',
      'e2e-deeplink-pool',
    );

    await page.goto('/stations/e2e-deeplink-station');
    const badge = page.getByTestId('pool-missing').first();
    await expect(badge).toBeVisible();
    await expect(badge).toHaveAttribute('href', /\/sources\?pool=/);

    await badge.click();
    await expect(page).toHaveURL(/\/sources\?pool=e2e-deeplink-pool/);
    await expect(page.getByTestId('name-input')).toHaveValue(
      'e2e-deeplink-pool',
    );
    await expect(page.getByTestId('role-select')).toHaveValue('content');
  });
});
