import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, generate } from './fixtures';

/**
 * §16.1 in-UI generate button — end-to-end against a real backend.
 *
 * Vitest covers the wiring (mock POST, confirm() yes/no, error rendering).
 * Playwright covers what Vitest can't: the FastAPI round-trip + Vite proxy
 * + actual SvelteKit navigation to the trace viewer. One spec per major
 * flow; an underflow spec proves the inline error path against a real
 * engine error rather than a mocked rejection.
 */
test.describe('§16.1 generate panel', () => {
  test('empty-state CTA: click → POST → trace viewer with 3 slots', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'gen-empty', 'Gen Empty');

    await page.goto('/stations/gen-empty');
    await expect(page.getByRole('heading', { name: 'Gen Empty' })).toBeVisible();
    await page.getByTestId('generate-empty-cta').click();

    // SvelteKit goto navigates to /playlists/{id}; the trace viewer's heading
    // doubles as our success signal.
    await expect(page).toHaveURL(/\/playlists\/\d+$/);
    await expect(page.getByRole('heading', { name: /gen empty/i })).toBeVisible();
  });

  test('populated state: "Generate next week (N+1)" advances to week 2', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'gen-next', 'Gen Next');
    await generate(request, 'gen-next', 1);

    await page.goto('/stations/gen-next');
    const nextBtn = page.getByTestId('generate-next');
    await expect(nextBtn).toHaveText(/generate next week.*2/i);
    await nextBtn.click();

    await expect(page).toHaveURL(/\/playlists\/\d+$/);
    // Trace viewer renders the week as a zero-padded "002" badge.
    await expect(page.getByText('002').first()).toBeVisible();
  });

  test('advanced disclosure: "Replace week N" + confirm path replaces an existing trace', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'gen-replace', 'Gen Replace');
    const orig = await generate(request, 'gen-replace', 1);

    await page.goto('/stations/gen-replace');
    // Open the advanced disclosure and target week 1 (the existing one).
    await page.getByText(/advanced — pick a week/i).click();
    const weekInput = page.getByTestId('advanced-week');
    await weekInput.fill('1');
    const submit = page.getByTestId('advanced-submit');
    await expect(submit).toHaveText(/replace week 1/i);

    // Accept the browser confirm() dialog.
    page.once('dialog', (d) => d.accept());
    await submit.click();

    await expect(page).toHaveURL(/\/playlists\/\d+$/);
    // The new trace_id differs from the original — i.e. the regen really
    // produced a fresh `PlaylistTrace` row, not just navigated to the old one.
    // (For rng:off + same week the *content* is byte-identical, but the
    // trace_id is a fresh UUID per call.)
    const newId = Number(page.url().split('/').pop());
    const r = await request.get(`http://127.0.0.1:8001/api/playlists/${newId}/trace`);
    const trace = await r.json();
    expect(trace.trace_id).not.toBe(orig.trace_id);
  });

  test('per-row regen: ↻ regen confirm → fresh trace_id, lands on viewer', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'gen-row-regen', 'Gen Row Regen');
    const orig = await generate(request, 'gen-row-regen', 1);

    await page.goto('/stations/gen-row-regen');
    page.once('dialog', (d) => d.accept());
    await page.getByTestId('regen-row-1').click();

    await expect(page).toHaveURL(/\/playlists\/\d+$/);
    const newId = Number(page.url().split('/').pop());
    const r = await request.get(`http://127.0.0.1:8001/api/playlists/${newId}/trace`);
    const trace = await r.json();
    expect(trace.trace_id).not.toBe(orig.trace_id);
  });

  test('cancelled confirm: clicking "Replace" then dismissing leaves the page where it was', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'gen-cancel', 'Gen Cancel');
    await generate(request, 'gen-cancel', 1);

    await page.goto('/stations/gen-cancel');
    await page.getByText(/advanced — pick a week/i).click();
    await page.getByTestId('advanced-week').fill('1');

    page.once('dialog', (d) => d.dismiss());
    await page.getByTestId('advanced-submit').click();

    // No navigation: still on the station detail page.
    await expect(page).toHaveURL(/\/stations\/gen-cancel$/);
  });

  test('inline error on engine failure (pool underflow): no navigation', async ({
    page,
    request,
  }) => {
    // Build a station where week 2 underflows the pool. Tiny library has
    // 3 tracks; cursor advance of 10 means week 2's pick reads pool index
    // 11, which doesn't exist → engine raises, router returns 400.
    await importTinyPool(request, 'A');
    const r = await request.post('http://127.0.0.1:8001/api/stations', {
      data: {
        slug: 'gen-underflow',
        name: 'Gen Underflow',
        template: {
          station: 'gen-underflow',
          cadence: 'weekly',
          rng: 'off',
          sources: [{ name: 'A', colour: '#117733', role: 'content' }],
          // Big enough advance that week 2 jumps past the 3-track pool.
          cursor: { base_advance_per_week: 10, cycle_weeks: 10 },
          layout: {
            unit: 'day',
            unit_labels: ['Mon'],
            row_start_offsets: [0],
            row_template: [{ source: 'A', strategy: 'pick', size: 1 }],
          },
        },
      },
    });
    expect(r.status()).toBe(201);

    // Week 1 generates fine (base=0, pick at index 0 → first track).
    await generate(request, 'gen-underflow', 1);
    await page.goto('/stations/gen-underflow');

    // Click "Generate next week" — should hit the underflow on the backend.
    await page.getByTestId('generate-next').click();

    // Inline error renders, page stays put.
    await expect(page.getByTestId('generate-error')).toBeVisible({ timeout: 5_000 });
    await expect(page).toHaveURL(/\/stations\/gen-underflow$/);
  });
});
