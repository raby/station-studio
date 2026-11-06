import { test, expect, type APIRequestContext } from '@playwright/test';
import { API, createStation, generate, importTinyPool, minimalFlatTemplate, minimalTemplate } from './fixtures';
import { assertNoA11yViolations } from './a11y';

/**
 * Moving songs in the Songs list changes when they play: a song (its
 * whole rule), a selection, or a section, dragged or moved from the
 * keyboard, is queued as a move for every week.
 */

/** Three rules from A, then one from B in a section of its own
 *  ("Evening"), so where B's song plays is plain to see. */
async function station(request: APIRequestContext, slug: string) {
  await importTinyPool(request, 'A');
  await importTinyPool(request, 'B');
  const t = minimalFlatTemplate(slug);
  t.sources.push({ name: 'B', colour: '#882255', role: 'content' });
  t.layout.flat_slots = [
    { source: 'A', strategy: 'weighted', size: 1 },
    { source: 'A', strategy: 'weighted', size: 1 },
    { source: 'A', strategy: 'weighted', size: 1 },
    { source: 'B', strategy: 'weighted', size: 1 },
  ] as typeof t.layout.flat_slots;
  await createStation(request, slug, slug, 'A', t as unknown as ReturnType<typeof minimalTemplate>);
  const sc = await (await request.get(`${API}/api/stations/${slug}/sidecar`)).json();
  const r = await request.post(`${API}/api/stations/${slug}/sidecar/batch`, {
    data: {
      edits: [
        { kind: 'sectionCreate', sectionId: 'evening', name: 'Evening' },
        { kind: 'moveBlock', address: { slot_index: 3 }, targetSectionId: 'evening' },
      ],
      expected_updated_at: sc.updated_at,
    },
  });
  expect(r.ok(), await r.text()).toBe(true);
}

const sources = async (request: APIRequestContext, slug: string) =>
  ((await (await request.get(`${API}/api/stations/${slug}/sidecar`)).json()).template.layout.flat_slots as { source: string }[])
    .map((s) => s.source);

test('in the station editor: drag a song to play first, move a section from the keyboard, save', async ({ page, request }) => {
  await station(request, 'mv-editor');
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/stations/mv-editor/visualise');
  await page.getByTestId('live-preview-checkbox').check();
  const rules = page.getByTestId('song-rule');
  await expect(rules).toHaveCount(4, { timeout: 15000 });
  await expect(rules.nth(3)).toContainText('from B');
  await assertNoA11yViolations(page);

  // B's song, by its grip, to the top half of the first song: first.
  const grips = page.getByTestId('song-grip');
  await grips.nth(3).dragTo(page.getByTestId('song-row').first(), { targetPosition: { x: 20, y: 2 } });
  await expect(page.getByTestId('trace-change-item')).toHaveText([/Moved Evening, rule 1 to All slots, rule 1/]);
  await expect(rules.first()).toContainText('from B', { timeout: 15000 });
  await expect(page.getByTestId('song-move-note')).not.toContainText('waits');

  // From the keyboard: B's song, now first, back to the end (End is the
  // last place: the end of All slots, Evening being empty now).
  const first = grips.first();
  await first.focus();
  await page.keyboard.press('Space');
  await expect(page.getByTestId('song-move-status')).toContainText('Picked up');
  await expect(page.locator('li.carried')).toHaveCount(1);
  await assertNoA11yViolations(page);
  await page.keyboard.press('End');
  await expect(page.getByTestId('song-drop-line')).toHaveCount(1);
  await page.keyboard.press('Space');
  await expect(page.getByTestId('trace-change-item')).toHaveCount(2);
  await expect(rules.nth(3)).toContainText('from B', { timeout: 15000 });
  // Focus went with it.
  await expect(page.locator(':focus')).toHaveAttribute('data-rule', '3');

  await page.getByTestId('staged-save').click();
  await expect(page.getByTestId('staged-save')).toHaveCount(0, { timeout: 15000 });
  expect(await sources(request, 'mv-editor')).toEqual(['A', 'A', 'A', 'B']);
});

test('in the station editor: a section dragged before another plays first', async ({ page, request }) => {
  await station(request, 'mv-section');
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/stations/mv-section/visualise');
  await page.getByTestId('live-preview-checkbox').check();
  await expect(page.getByTestId('song-rule')).toHaveCount(4, { timeout: 15000 });
  const evening = page.locator('[data-testid="section-grip"][data-section="evening"]');
  await evening.focus();
  await page.keyboard.press('Space');
  await page.keyboard.press('ArrowUp');
  await expect(page.getByTestId('song-move-status')).toHaveText(/before All slots/);
  await page.keyboard.press('Space');
  await expect(page.getByTestId('trace-change-item')).toHaveText([/Moved section “Evening” before “All slots”/]);
  await expect(page.getByTestId('song-rule').first()).toContainText('from B', { timeout: 15000 });
  await expect(page.getByTestId('song-section').first()).toContainText('Evening');
  await expect(page.locator(':focus')).toHaveAttribute('data-section', 'evening');
  await page.getByTestId('staged-save').click();
  await expect(page.getByTestId('staged-save')).toHaveCount(0, { timeout: 15000 });
  expect(await sources(request, 'mv-section')).toEqual(['B', 'A', 'A', 'A']);
});

test("on a week's page: drag a song, and the move is queued for the station", async ({ page, request }) => {
  await station(request, 'mv-week');
  const { playlist_id } = await generate(request, 'mv-week', 1);
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto(`/playlists/${playlist_id}`);
  await page.getByTestId('week-view-songs').click();
  const rules = page.getByTestId('song-rule');
  await expect(rules).toHaveCount(4);
  await page.getByTestId('song-grip').nth(3).dragTo(page.getByTestId('song-row').first(), { targetPosition: { x: 20, y: 2 } });
  await expect(page.getByTestId('song-edit-count')).toContainText('1 change');
  await expect(rules.first()).toContainText('from B', { timeout: 15000 });
  await assertNoA11yViolations(page);
  await page.getByTestId('song-edit-save').click();
  await expect(page.getByTestId('song-edit-saved')).toBeVisible({ timeout: 15000 });
  expect(await sources(request, 'mv-week')).toEqual(['B', 'A', 'A', 'A']);
});

test('in the station editor: over the line between two sections, a song only changes section', async ({ page, request }) => {
  await station(request, 'mv-regroup');
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/stations/mv-regroup/visualise');
  await page.getByTestId('live-preview-checkbox').check();
  await expect(page.getByTestId('song-rule')).toHaveCount(4, { timeout: 15000 });
  // B's song (Evening's only one) up one stop: the end of All slots,
  // where it plays already.
  await page.getByTestId('song-grip').nth(3).focus();
  await page.keyboard.press('Space');
  await page.keyboard.press('ArrowUp');
  await expect(page.getByTestId('song-move-status')).toHaveText('at the end of All slots');
  await page.keyboard.press('Space');
  await expect(page.getByTestId('trace-change-item')).toHaveText([/Moved Evening, rule 1 to All slots, rule 4/]);
  await expect(page.getByTestId('song-section')).toHaveCount(1, { timeout: 15000 });
  // Playing where it did: not marked moved.
  await expect(page.getByTestId('song-change')).toHaveCount(0);
  await page.getByTestId('staged-save').click();
  await expect(page.getByTestId('staged-save')).toHaveCount(0, { timeout: 15000 });
  const saved = (await (await request.get(`${API}/api/stations/mv-regroup/sidecar`)).json());
  expect(saved.template.layout.flat_slots.map((s: { source: string }) => s.source)).toEqual(['A', 'A', 'A', 'B']);
  const evening = saved.sidecar.sections.find((s: { id: string }) => s.id === 'evening');
  expect(evening.blocks).toEqual([]);
});
