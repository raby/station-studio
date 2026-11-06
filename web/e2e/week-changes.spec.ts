import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { API, createStation, generate, importTinyPool, minimalFlatTemplate, minimalTemplate } from './fixtures';
import { assertNoA11yViolations } from './a11y';

/**
 * Just-this-week changes on a week's page: a song swapped, or picked by
 * its rule changed, for this week only. Saved at once as a new version
 * of the week, kept when the week is regenerated, undone from the list.
 */

/** Two rules from A, one from B (the tiny library's three songs). */
async function station(request: APIRequestContext, slug: string) {
  await importTinyPool(request, 'A');
  await importTinyPool(request, 'B');
  const t = minimalFlatTemplate(slug);
  t.sources.push({ name: 'B', colour: '#882255', role: 'content' });
  t.layout.flat_slots = [
    { source: 'A', strategy: 'weighted', size: 1 },
    { source: 'A', strategy: 'weighted', size: 1 },
    { source: 'B', strategy: 'weighted', size: 1 },
  ] as typeof t.layout.flat_slots;
  await createStation(request, slug, slug, 'A', t as unknown as ReturnType<typeof minimalTemplate>);
}

async function openWeek(page: Page, playlistId: number) {
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto(`/playlists/${playlistId}`);
  await page.getByTestId('week-view-songs').click();
  await expect(page.getByTestId('song-row')).toHaveCount(3);
}

const songs = async (request: APIRequestContext, playlistId: number) =>
  ((await (await request.get(`${API}/api/playlists/${playlistId}/trace`)).json()).slots as { track_name: string }[])
    .map((s) => s.track_name);

test("swap a song just this week, keep it on regenerate, undo it", async ({ page, request }) => {
  await station(request, 'wk-swap');
  const { playlist_id } = await generate(request, 'wk-swap', 1, 5);
  const before = await songs(request, playlist_id);
  await openWeek(page, playlist_id);

  await page.getByTestId('song-row').nth(1).click();
  await expect(page.getByTestId("this-song")).toContainText(`Song 2: ${before[1]}`);
  await page.getByTestId('this-song-swap').click();
  const dialog = page.getByTestId('swap-song');
  await expect(dialog).toBeVisible();
  // The rule's pool (A), without the song its rule picked.
  const results = dialog.getByTestId('swap-song-result');
  await expect(results).toHaveCount(2);
  await expect(dialog.getByTestId('swap-song-repeat')).toHaveCount(2); // the others play this week too
  await assertNoA11yViolations(page);
  const picked = (await results.first().locator('.title').textContent())!;
  await results.first().click();
  await expect(dialog).toHaveCount(0, { timeout: 15000 });
  await expect(page.getByTestId('this-song-swap')).toBeFocused();

  await expect(page.getByTestId('week-change-status')).toContainText(`song 2 swapped for ${picked}`);
  await expect(page.getByTestId('song-row').nth(1)).toContainText(picked);
  await expect(page.getByTestId('song-week-only')).toHaveCount(1);
  await expect(page.getByTestId('week-only-item')).toContainText(`Swapped for ${picked}`);
  // Only that song changed, in a new version saying so.
  const after = await songs(request, playlist_id);
  expect(after).toEqual([before[0], picked, before[2]]);
  await expect(page.getByTestId('version-select')).toContainText('Just this week: song 2 swapped for');
  await assertNoA11yViolations(page);
  await page.emulateMedia({ colorScheme: 'dark' });
  await assertNoA11yViolations(page);
  await page.emulateMedia({ colorScheme: 'light' });
  // At phone width, the version's note doesn't push the page sideways.
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.setViewportSize({ width: 1400, height: 900 });

  // Regenerated with the same seed: the same week, the swap kept.
  await generate(request, 'wk-swap', 1, 5);
  expect(await songs(request, playlist_id)).toEqual(after);
  await page.reload();
  await page.getByTestId('week-view-songs').click();
  await expect(page.getByTestId('week-only-item')).toHaveCount(1);

  // Undo, from the list: the rule's own song again.
  await page.getByTestId('week-only-undo').click();
  await expect(page.getByTestId('week-only')).toHaveCount(0, { timeout: 15000 });
  await expect(page.getByTestId('week-change-status')).toContainText("back to its rule's song");
  expect(await songs(request, playlist_id)).toEqual(before);
});

test("change a song's rule just this song, from the ask", async ({ page, request }) => {
  await station(request, 'wk-rule');
  const { playlist_id } = await generate(request, 'wk-rule', 1, 5);
  await openWeek(page, playlist_id);

  await page.getByTestId('song-row').first().click();
  await expect(page.getByTestId('detail-panel')).toBeVisible();
  await page.getByTestId('retype-toggle').click();
  await page.getByTestId('retype-source').selectOption('B');
  await page.getByTestId('retype-apply').click();
  const ask = page.getByTestId('change-scope');
  await expect(ask).toBeVisible();
  await expect(page.getByTestId('change-scope-every')).toBeFocused();
  await assertNoA11yViolations(page);
  await page.getByTestId('change-scope-week').click();
  await expect(ask).toHaveCount(0);
  await expect(page.getByTestId('retype-apply')).toBeFocused();

  await expect(page.getByTestId('week-change-status')).toContainText('song 1 picked by its rule changed: source B', { timeout: 15000 });
  await expect(page.getByTestId('week-only-item')).toContainText('Picked by its rule changed: source B');
  await expect(page.getByTestId('song-week-only')).toHaveCount(1);
  // Nothing queued for every week; the station's rule is as it was.
  await expect(page.getByTestId('song-edit-count')).toHaveCount(0);
  const saved = await (await request.get(`${API}/api/stations/wk-rule/sidecar`)).json();
  expect(saved.template.layout.flat_slots[0].source).toBe('A');
  const trace = await (await request.get(`${API}/api/playlists/${playlist_id}/trace`)).json();
  expect(trace.slots[0].source).toBe('A'); // the slot keeps its rule
  expect(trace.slots[0].change).toMatchObject({ kind: 'rule', source: 'B' });

  // Next time, "just this song" is offered first.
  await page.getByTestId('song-row').nth(1).click();
  await page.getByTestId('retype-toggle').click();
  await page.getByTestId('retype-source').selectOption('B');
  await page.getByTestId('retype-apply').click();
  await expect(page.getByTestId('change-scope-week')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('change-scope')).toHaveCount(0);
});
