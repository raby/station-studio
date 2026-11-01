import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, minimalTemplate } from './fixtures';
import { assertNoA11yViolations } from './a11y';

/**
 * The station editor's preview column: the week as songs beside the
 * blocks. A song opens its rule's panel in the column, so the songs stay
 * in view while the rule changes, and the songs it changes are marked.
 */
test('a song opens its rule in the preview column; a change marks the songs it alters', async ({ page, request }) => {
  await importTinyPool(request, 'A');
  const t = minimalTemplate('songs-col');
  t.layout.unit_labels = ['Mon', 'Tue'];
  t.layout.row_start_offsets = [0, 0];
  t.layout.row_template = [
    { source: 'A', strategy: 'pick', size: 1 },
    { source: 'A', strategy: 'window', size: 2 },
  ] as typeof t.layout.row_template;
  await createStation(request, 'songs-col', 'Songs Col', 'A', t);

  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/stations/songs-col/visualise');
  await page.getByTestId('live-preview-checkbox').check();
  await expect(page.getByTestId('trace-tab-songs')).toHaveAttribute('aria-selected', 'true');
  const songs = page.getByTestId('song-row');
  await expect(songs).toHaveCount(6);

  // Song 2 comes from the window rule (2 in a row from A).
  await songs.nth(1).click();
  const panel = page.locator('[data-testid="column-panel"] [data-testid="detail-panel"]');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('2 in a row from A');
  // The songs stay in view beside it: the picked one marked, the other
  // song from that rule (song 3, the window's second) highlighted.
  await expect(page.getByTestId('trace-col')).toBeVisible();
  await expect(songs.nth(1)).toHaveAttribute('aria-pressed', 'true');
  await expect(songs.nth(2)).toHaveClass(/related/);
  await expect(songs.nth(0)).not.toHaveClass(/related/);
  await assertNoA11yViolations(page);

  // Change the rule: picked rather than in a row. It's queued, the
  // preview reruns, and the songs it changes are marked.
  await page.getByTestId('retype-toggle').click();
  await page.getByTestId('retype-strategy').selectOption('pick');
  await page.getByTestId('retype-apply').click();
  await expect(page.getByTestId('staged-save')).toHaveText(/Save 1 edit/);
  await expect(page.getByTestId('song-only-changed')).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId('song-rule').nth(1)).toContainText('Picked from A');
  // The panel and the blocks show the queued change too, not the saved rule.
  await expect(panel.locator('.rule-words')).toHaveText('Picked from A');
  await expect(page.locator('[data-ref-key]').nth(1)).toHaveText(/Pk/);

  // Closing the panel unmarks the song; the list stays.
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(songs.nth(1)).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('trace-songs')).toBeVisible();

  // From the keyboard: a song opens its rule, and closing the panel from
  // inside it puts focus back on the song.
  await songs.nth(0).focus();
  await page.keyboard.press('Enter');
  await expect(panel).toBeVisible();
  await page.getByTestId('detail-close').focus();
  await page.keyboard.press('Enter');
  await expect(panel).toHaveCount(0);
  await expect(songs.nth(0)).toBeFocused();
});

test('with the preview off, a rule\'s panel is on the window edge as before', async ({ page, request }) => {
  await importTinyPool(request, 'A');
  await createStation(request, 'songs-off', 'Songs Off', 'A', minimalTemplate('songs-off'));
  await page.goto('/stations/songs-off/visualise');
  await expect(page.getByTestId('live-preview-checkbox')).not.toBeChecked();
  await page.locator('[data-ref-key]').first().click();
  const panel = page.getByTestId('detail-panel');
  await expect(panel).toBeVisible();
  await expect(panel).not.toHaveClass(/detail-panel--column/);
  await expect(page.getByTestId('column-panel')).toHaveCount(0);
});
