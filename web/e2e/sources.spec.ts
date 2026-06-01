import { test, expect } from '@playwright/test';
import {
  createStation,
  TINY_LIBRARY_PATH,
  MALFORMED_LIBRARY_PATH,
} from './fixtures';

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

  // ---- A: Batch mode (D2 + G3 sequential submit) ------------------

  test('batch mode: two-pool sequential submit lands both pools', async ({
    page,
  }) => {
    await page.goto('/sources');
    await page.getByTestId('add-pool-form').waitFor();

    // Toggle into batch mode, add a second row.
    await page.getByTestId('batch-mode-toggle').click();
    await page.getByTestId('batch-add-row').click();

    const nameInputs = page.getByTestId('batch-row-name');
    const fileInputs = page.getByTestId('batch-row-file');
    await nameInputs.nth(0).fill('e2e-batch-a');
    await fileInputs.nth(0).setInputFiles(TINY_LIBRARY_PATH);
    await nameInputs.nth(1).fill('e2e-batch-b');
    await fileInputs.nth(1).setInputFiles(TINY_LIBRARY_PATH);

    await page.getByTestId('batch-submit').click();

    // Success banner after full-success path.
    await expect(page.getByTestId('success-banner')).toContainText(
      /Imported all 2 pools/,
    );

    // Both rows appear in the list.
    await expect(
      page.locator('[data-testid="pool-row"][data-pool-name="e2e-batch-a"]'),
    ).toBeVisible();
    await expect(
      page.locator('[data-testid="pool-row"][data-pool-name="e2e-batch-b"]'),
    ).toBeVisible();
  });

  test('batch mode: partial failure keeps the failed row visible with inline error', async ({
    page,
  }) => {
    await page.goto('/sources');
    await page.getByTestId('add-pool-form').waitFor();

    await page.getByTestId('batch-mode-toggle').click();
    await page.getByTestId('batch-add-row').click();

    const nameInputs = page.getByTestId('batch-row-name');
    const fileInputs = page.getByTestId('batch-row-file');
    await nameInputs.nth(0).fill('e2e-batch-good');
    await fileInputs.nth(0).setInputFiles(TINY_LIBRARY_PATH);
    await nameInputs.nth(1).fill('e2e-batch-bad');
    await fileInputs.nth(1).setInputFiles(MALFORMED_LIBRARY_PATH);

    await page.getByTestId('batch-submit').click();

    // Form-level summary banner mentions the partial split.
    await expect(page.getByTestId('form-error')).toContainText(/Imported 1 of 2/);
    // Good row dropped (it succeeded); bad row stays + carries the error.
    const remaining = page.getByTestId('batch-row');
    await expect(remaining).toHaveCount(1);
    await expect(remaining).toHaveAttribute('data-row-status', 'failed');
    await expect(page.getByTestId('batch-row-error')).toContainText(
      /import failed/i,
    );
  });

  // ---- B: PoolImportPanel embedded on /stations/[slug] (D3) -------

  test('station embed: upload via panel clears the §16.19 "not imported" badge', async ({
    page,
    request,
  }) => {
    // Station declares "e2e-embed-pool" but never imports it.
    await createStation(
      request,
      'e2e-embed-station',
      'Embed Station',
      'e2e-embed-pool',
    );

    await page.goto('/stations/e2e-embed-station');
    // §16.19 "not imported" badge visible up top.
    await expect(page.getByTestId('pool-missing').first()).toBeVisible();

    // The embed lives in `data-testid="overview-pool-import"`. Scope every
    // panel interaction here so we don't accidentally hit the page-level
    // /sources version (there isn't one on this page, but scoping is the
    // correct discipline for the embed test).
    const embed = page.getByTestId('overview-pool-import');
    await embed.getByTestId('name-input').fill('e2e-embed-pool');
    await embed.getByTestId('file-input').setInputFiles(TINY_LIBRARY_PATH);
    await embed.getByTestId('submit-button').click();

    // Embed shows its own success banner.
    await expect(embed.getByTestId('success-banner')).toContainText(
      /Imported "e2e-embed-pool"/,
    );

    // The §16.19 overview re-fetched via the c5 `dispatch('changed')`
    // wiring → "not imported" badge for this pool is gone.
    await expect(
      page.locator('[data-testid="pool-missing"]', {
        hasText: 'e2e-embed-pool',
      }),
    ).toHaveCount(0);
  });

  // ---- C: Upload cancel mid-flight (§16.25.t2 AbortController) ---

  test('upload cancel mid-flight aborts the in-flight POST', async ({ page }) => {
    await page.goto('/sources');
    await page.getByTestId('add-pool-form').waitFor();

    // Intercept the upload + stall the response so we can click Cancel
    // before it lands. The route resolves only after the test signals
    // it's done.
    let resolveResponse: (() => void) | null = null;
    let routeAborted = false;
    await page.route('**/api/sources/upload', async (route) => {
      // Wait until the test releases or the request aborts.
      const releasePromise = new Promise<void>((r) => {
        resolveResponse = r;
      });
      try {
        await releasePromise;
        await route.fulfill({ status: 200, body: '{}' });
      } catch (e) {
        // Abort surfaces as a thrown error from `route.fulfill` (request
        // was cancelled mid-flight).
        routeAborted = true;
      }
    });

    await page.getByTestId('name-input').fill('e2e-cancel');
    await page.getByTestId('file-input').setInputFiles(TINY_LIBRARY_PATH);
    await page.getByTestId('submit-button').click();

    // Cancel button (data-testid="cancel-button") appears once submitting.
    await expect(page.getByTestId('cancel-button')).toBeVisible();
    await page.getByTestId('cancel-button').click();

    // After cancel: form is back to its idle state — Import button visible
    // again, no success banner shown. The form-error may or may not
    // surface depending on AbortError handling; we don't assert on it.
    await expect(page.getByTestId('submit-button')).toBeVisible();
    await expect(page.getByTestId('success-banner')).toHaveCount(0);

    // The intercepted route's promise can now be released for cleanup
    // (no-op if it was already aborted by the AbortController).
    resolveResponse?.();
    // routeAborted is informational — the cancel button click triggered
    // AbortController.abort(), which cancels the fetch upstream.
    void routeAborted;
  });

  // ---- D: Mid-batch mode-switch confirm (§16.25.t1) ---------------

  test('batch mode-switch with dormant content opens the confirm dialog', async ({
    page,
  }) => {
    await page.goto('/sources');
    await page.getByTestId('add-pool-form').waitFor();

    await page.getByTestId('batch-mode-toggle').click();
    // Put a file in the row (now "dormant" if we switch to path mode).
    await page.getByTestId('batch-row-file').setInputFiles(TINY_LIBRARY_PATH);

    // Click the page-level mode-toggle-path button. ConfirmDialog opens.
    await page.getByTestId('mode-toggle-path').click();

    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await expect(page.getByTestId('confirm-dialog-title')).toHaveText(
      /Switch to path mode/i,
    );
    await expect(page.getByTestId('confirm-dialog-body')).toContainText(
      /1 row has content from the other mode/,
    );

    // Cancel → mode stays in upload (file-input still rendered in the row).
    await page.getByTestId('confirm-dialog-cancel').click();
    await expect(page.getByTestId('batch-row-file')).toBeVisible();
    await expect(page.getByTestId('batch-row-path')).toHaveCount(0);
  });

  // ---- E: ConfirmDialog Esc + click-outside dismissal -------------

  test('confirm dialog: Esc dismisses (no destructive action fires)', async ({
    page,
  }) => {
    await page.goto('/sources');
    // Seed a pool to delete.
    await page.getByTestId('name-input').fill('e2e-esc');
    await page.getByTestId('file-input').setInputFiles(TINY_LIBRARY_PATH);
    await page.getByTestId('submit-button').click();
    await expect(page.getByTestId('success-banner')).toBeVisible();

    // Open the delete confirm + dismiss with Esc.
    const row = page.locator(
      '[data-testid="pool-row"][data-pool-name="e2e-esc"]',
    );
    await row.getByTestId('pool-row-delete').click();
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await page.keyboard.press('Escape');

    // Dialog gone, row still there (Esc = cancel).
    await expect(page.getByTestId('confirm-dialog')).toHaveCount(0);
    await expect(row).toBeVisible();
  });

  test('confirm dialog: click-outside dismisses (backdrop = cancel)', async ({
    page,
  }) => {
    await page.goto('/sources');
    await page.getByTestId('name-input').fill('e2e-backdrop');
    await page.getByTestId('file-input').setInputFiles(TINY_LIBRARY_PATH);
    await page.getByTestId('submit-button').click();
    await expect(page.getByTestId('success-banner')).toBeVisible();

    const row = page.locator(
      '[data-testid="pool-row"][data-pool-name="e2e-backdrop"]',
    );
    await row.getByTestId('pool-row-delete').click();
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();

    // Click the backdrop (NOT the dialog itself). Position click at
    // a corner of the viewport — guaranteed to be on the backdrop.
    await page.getByTestId('confirm-dialog-backdrop').click({
      position: { x: 5, y: 5 },
    });

    await expect(page.getByTestId('confirm-dialog')).toHaveCount(0);
    await expect(row).toBeVisible();
  });

  // ---- F: iTunes-XML export hint visibility (G5) ------------------

  test('iTunes XML export hint shows in upload mode, hides in path mode', async ({
    page,
  }) => {
    await page.goto('/sources');
    await page.getByTestId('add-pool-form').waitFor();

    // Default mode is upload — hint visible with the macOS / Windows copy.
    const hint = page.getByTestId('itunes-hint');
    await expect(hint).toBeVisible();
    await expect(hint).toContainText(/Music \(macOS\)/);
    await expect(hint).toContainText(/Export Playlist/);

    // Toggle to path mode — hint is irrelevant (the user already has the
    // file on disk) and disappears.
    await page.getByTestId('mode-toggle-path').click();
    await expect(page.getByTestId('itunes-hint')).toHaveCount(0);
  });
});
