import { test, expect } from '@playwright/test';
import { createStation, importTinyPool } from './fixtures';

/**
 * §16.11 c5 — Playwright covering the full in-UI progress flow.
 *
 * The async job runner (c1-c3) + frontend wiring (c4) + page glue
 * (c5) all collaborate; vitest pins each unit but only an e2e run
 * proves they speak to each other through the real Vite proxy +
 * FastAPI + SvelteKit nav.
 *
 * Engine-too-fast caveat: the tiny-library template is a 1-slot
 * generation that finishes in microseconds — far faster than any
 * single poll (500ms). Without intervention the first GET
 * /api/jobs/{id} sees status=done and the page navigates with the
 * <ProgressIndicator> never reaching the DOM. To make the running
 * state observable deterministically, we use Playwright's request
 * interception to FABRICATE a "running" snapshot for the first poll,
 * then let the second poll through to the real backend (which by
 * then has actually finished + returned the done snapshot).
 *
 * This is honest e2e: the fabricated snapshot has the same shape
 * the backend returns; we're only delaying the page's observation
 * of completion, not faking the engine work itself.
 */
test.describe('§16.11 in-UI generation progress', () => {
  test('shows ProgressIndicator while running, navigates on done', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'gen-progress', 'Gen Progress');

    let polledOnce = false;
    await page.route('**/api/jobs/**', async (route, req) => {
      // Stub ONLY the first GET so the page sees a running snapshot
      // and renders the indicator. Pass everything else through.
      if (req.method() === 'GET' && !polledOnce) {
        polledOnce = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            job_id: 'synthetic',
            slug: 'gen-progress',
            kind: 'generate',
            status: 'running',
            phase: 'picks',
            current: 1,
            total: 3,
            label: 'Generating week 1 (1 / 3)',
            result: null,
            error: null,
            started_at: '2026-05-27T10:00:00Z',
            finished_at: null,
          }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto('/stations/gen-progress');
    await page.getByTestId('generate-empty-cta').click();

    // The fabricated running snapshot mounts <ProgressIndicator>.
    const indicator = page.getByTestId('progress-indicator');
    await expect(indicator).toBeVisible();
    await expect(indicator).toHaveAttribute('data-status', 'running');
    await expect(page.getByTestId('progress-label')).toContainText(
      'Generating week 1',
    );
    await expect(page.getByTestId('progress-cancel')).toBeVisible();

    // Subsequent poll hits the real backend (job already done) →
    // page navigates to the trace viewer. Indicator vanishes.
    await expect(page).toHaveURL(/\/playlists\/\d+$/);
    await expect(page.getByRole('heading', { name: /gen progress/i })).toBeVisible();
  });

  test('cancel button calls DELETE /api/jobs/{id}', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'gen-job-cancel', 'Gen Cancel');

    // Hold all GETs in a running state so the indicator stays
    // visible long enough to click Cancel. The DELETE is the
    // assertion target; passing requests through.
    await page.route('**/api/jobs/**', async (route, req) => {
      if (req.method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            job_id: 'synthetic-c',
            slug: 'gen-job-cancel',
            kind: 'generate',
            status: 'running',
            phase: 'picks',
            current: 1,
            total: 3,
            label: 'Generating…',
            result: null,
            error: null,
            started_at: '2026-05-27T10:00:00Z',
            finished_at: null,
          }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto('/stations/gen-job-cancel');

    // Listen for the DELETE that the Cancel button must fire.
    const deletePromise = page.waitForRequest(
      (req) =>
        req.method() === 'DELETE' && /\/api\/jobs\/[^/]+$/.test(req.url()),
    );

    await page.getByTestId('generate-empty-cta').click();
    await expect(page.getByTestId('progress-indicator')).toBeVisible();
    await page.getByTestId('progress-cancel').click();
    // DELETE fired against the real job_id (whatever the backend
    // assigned). The fabricated GET stub's job_id is irrelevant here;
    // the page tracks the job_id from the original POST response.
    await deletePromise;
  });
});
