import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, minimalFlatTemplate, minimalTemplate } from './fixtures';
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
  await expect(page.getByTestId('staged-save')).toHaveText(/Save 1 change/);
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

test('the queued changes are listed, each with an Undo; one built on asks first', async ({ page, request }) => {
  await importTinyPool(request, 'A');
  // Rules the tiny pool can fill, so the week previews as it changes.
  const t = minimalFlatTemplate('undo-each');
  t.layout.flat_slots = [1, 2, 3].map(() => ({ source: 'A', strategy: 'weighted', size: 1 })) as typeof t.layout.flat_slots;
  await createStation(request, 'undo-each', 'Undo Each', 'A', t as unknown as ReturnType<typeof minimalTemplate>);
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/stations/undo-each/visualise');
  await page.getByTestId('live-preview-checkbox').check();
  const chips = page.getByTestId('chip');
  await expect(chips).toHaveCount(3);
  const panel = page.locator('[data-testid="column-panel"] [data-testid="detail-panel"]');
  const changes = page.getByTestId('trace-change-item');

  // One change, undone on its own.
  await chips.nth(2).click();
  await page.getByTestId('structural-size-input').fill('5');
  await page.getByTestId('structural-size-apply').click();
  await page.keyboard.press('Escape');
  await expect(changes).toHaveCount(1);
  await expect(changes).toHaveText([/rule 3: 5 songs/]);
  // The week previews with it: 1 + 1 + 5 songs.
  await expect(page.getByTestId('song-row')).toHaveCount(7, { timeout: 15000 });
  await expect(page.getByTestId('trace-changes-title')).toHaveText('1 change, not saved yet');
  await assertNoA11yViolations(page);
  await page.getByTestId('trace-change-undo').click();
  await expect(changes).toHaveCount(0);
  await expect(page.getByTestId('staged-save')).toHaveCount(0);
  // Focus doesn't drop to the page: it's on the tab shown.
  await expect(page.getByTestId('trace-tab-songs')).toBeFocused();

  // Remove the first rule, then resize the one now first (the second
  // rule as saved). Undoing the remove would make that resize the first
  // rule's, so it asks to undo both.
  await chips.nth(0).click();
  await page.getByTestId('structural-delete').click();
  await page.getByTestId('structural-delete').click();
  await expect(chips).toHaveCount(2);
  await chips.nth(0).click();
  await expect(panel).toBeVisible();
  await page.getByTestId('structural-size-input').fill('3');
  await page.getByTestId('structural-size-apply').click();
  await page.keyboard.press('Escape');
  await expect(changes).toHaveText([/^Removed .*rule 1: Shuffled from A/, /rule 1: 3 songs/]);
  await expect(page.getByTestId('staged-save')).toHaveText('Save 2 changes');

  await page.getByTestId('trace-change-undo').first().click();
  const dialog = page.getByTestId('confirm-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.getByTestId('confirm-dialog-title')).toHaveText('Undo a later change too?');
  await expect(page.getByTestId('confirm-dialog-body')).toContainText(/• .*rule 1: 3 songs/);
  // Keep them: nothing changes.
  await page.getByTestId('confirm-dialog-cancel').click();
  await expect(dialog).toHaveCount(0);
  await expect(changes).toHaveCount(2);
  // Undo both.
  await page.getByTestId('trace-change-undo').first().click();
  await page.getByTestId('confirm-dialog-confirm').click();
  await expect(changes).toHaveCount(0);
  await expect(chips).toHaveCount(3);
  await expect(page.getByTestId('staged-save')).toHaveCount(0);
});

test("a song's Revert (Slots tab, Compare on) is on its rule's songs and undoes that rule's change", async ({ page, request }) => {
  await importTinyPool(request, 'A');
  const t = minimalFlatTemplate('revert-song');
  t.layout.flat_slots = [1, 2, 3].map(() => ({ source: 'A', strategy: 'weighted', size: 1 })) as typeof t.layout.flat_slots;
  await createStation(request, 'revert-song', 'Revert Song', 'A', t as unknown as ReturnType<typeof minimalTemplate>);
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/stations/revert-song/visualise');
  await page.getByTestId('live-preview-checkbox').check();
  await page.getByTestId('trace-tab-slots').click();
  await page.getByTestId('trace-show-diff').check();

  // Rule 2 plays three songs instead of one: songs 3-4 are new (rule 2's),
  // and song 5 is rule 3's, moved along.
  await page.getByTestId('chip').nth(1).click();
  await page.getByTestId('structural-size-input').fill('3');
  await page.getByTestId('structural-size-apply').click();
  await page.keyboard.press('Escape');
  const rows = page.getByTestId('trace-row');
  await expect(rows).toHaveCount(5, { timeout: 15000 });
  const revert = (n: number) => rows.nth(n).getByTestId('trace-row-revert');
  await expect(revert(3)).toBeEnabled();
  await expect(rows.nth(4)).toHaveAttribute('data-diff-kind', 'added');
  await expect(revert(4)).toBeDisabled();
  await assertNoA11yViolations(page);

  await revert(3).click();
  await expect(page.getByTestId('trace-change-item')).toHaveCount(0);
  await expect(rows).toHaveCount(3, { timeout: 15000 });
});
