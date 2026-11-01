import { test, expect, type Page } from '@playwright/test';
import { createStation, importTinyPool, generate, minimalTemplate } from './fixtures';

/**
 * Every button answers the mouse, and main buttons stay readable doing it.
 *
 * The app-wide `button:hover` is deliberately weak (theme.css), so a
 * component's own button style decides its hover. That cuts both ways: a
 * style that sets a background but no hover of its own leaves the button
 * dead under the mouse, and an accent style that loses to a grey hover
 * goes white text on light grey (it did on several pages).
 */
async function hoverCheck(page: Page, path: string | null, prepare?: () => Promise<void>, within = 'body'): Promise<string[]> {
  if (path) {
    await page.goto(path);
    await page.waitForLoadState('networkidle');
  }
  // Colours change at once, so each read is the settled state.
  await page.addStyleTag({ content: '*, *::before, *::after { transition: none !important; }' });
  if (prepare) await prepare();
  const where = path ?? page.url().replace(/^https?:\/\/[^/]+/, '');
  const accent = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim());
  const buttons = page.locator(within).locator('button:not([disabled]), a.btn-primary');
  const bad: string[] = [];
  const n = await buttons.count();
  for (let i = 0; i < n; i++) {
    const b = buttons.nth(i);
    if (!(await b.isVisible())) continue;
    // Skip what the mouse can't reach (under the editor's open side panel, say).
    if (!(await b.hover({ trial: true, timeout: 1000 }).then(() => true, () => false))) continue;
    const name = `${where} "${(await b.innerText()).trim().slice(0, 40)}"`;
    await page.mouse.move(0, 0);
    const look = (el: Element) => {
      const s = getComputedStyle(el);
      return [s.backgroundColor, s.borderColor, s.color, s.filter, s.boxShadow, s.textDecorationLine].join(' | ');
    };
    const before = await b.evaluate(look);
    const isAccent = await b.evaluate((el, a) => {
      const probe = document.createElement('div');
      probe.style.color = a;
      document.body.appendChild(probe);
      const rgb = getComputedStyle(probe).color;
      probe.remove();
      return getComputedStyle(el).backgroundColor === rgb;
    }, accent);
    // The current choice of a toggle (a pressed filter, the open tab)
    // can stay as it is: clicking it changes nothing.
    const current = await b.evaluate((el) =>
      el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-selected') === 'true');
    await b.hover();
    if (!current && (await b.evaluate(look)) === before) bad.push(`${name}: no change on hover`);
    if (!isAccent) continue;
    const after = await b.evaluate((el) => getComputedStyle(el).backgroundColor);
    // A see-through background shows whatever is behind the button; the
    // contrast sum below can't account for that, so it's flagged as is.
    const alpha = Number(after.match(/rgba\([^)]*,\s*([\d.]+)\)/)?.[1] ?? '1');
    if (alpha < 1) {
      bad.push(`${name}: see-through on hover (${after})`);
      continue;
    }
    // Text and background must still contrast (WCAG AA for normal text).
    const color = await b.evaluate((el) => getComputedStyle(el).color);
    const rel = (c: string) => {
      const [r, g, bl] = (c.match(/\d+(\.\d+)?/g) ?? ['0', '0', '0']).slice(0, 3).map(Number).map((v) => {
        const x = v / 255;
        return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
    };
    const [hi, lo] = [rel(after), rel(color)].sort((x, y) => y - x);
    const ratio = (hi + 0.05) / (lo + 0.05);
    if (ratio < 4.5) bad.push(`${name}: ${color} on ${after}, contrast ${ratio.toFixed(2)}`);
  }
  return bad;
}

for (const scheme of ['light', 'dark'] as const) test(`buttons answer hover and stay readable, on every page (${scheme})`, async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.emulateMedia({ colorScheme: scheme });
  const slug = `hover-check-${scheme}`;
  await importTinyPool(request, 'A');
  await createStation(request, slug, `Hover Check ${scheme}`, 'A', minimalTemplate(slug));
  const { playlist_id } = await generate(request, slug, 1);
  const bad: string[] = [];
  for (const path of ['/', '/settings', '/sources', '/stations/new', `/stations/${slug}`,
    `/stations/${slug}/edit`, `/playlists/${playlist_id}`]) {
    bad.push(...await hoverCheck(page, path));
  }
  // Buttons that only light up once there's something to save.
  bad.push(...await hoverCheck(page, `/stations/${slug}/rules`, async () => {
    await page.getByTestId('rule-input').first().fill('7');
    await page.getByTestId('list-rule-input').first().fill('X');
    await page.getByTestId('list-rule-add').first().click();
    await page.getByTestId('list-rule-input').first().fill('Y');
  }));
  bad.push(...await hoverCheck(page, `/stations/${slug}/visualise`, async () => {
    await page.locator('section[data-section-id="all-slots"] [data-testid="chip"]').first().click();
    await page.getByTestId('detail-panel').locator('input[data-testid^="input-"]').first().fill('Other');
  }));
  bad.push(...await hoverCheck(page, null, async () => {
    await page.getByTestId('add-section').click();
  }));
  // The preview column: Songs and Slots tabs, and the songs themselves.
  bad.push(...await hoverCheck(page, `/stations/${slug}/visualise`, async () => {
    const preview = page.getByTestId('live-preview-checkbox');
    if (!(await preview.isChecked())) await preview.check();
    await page.getByTestId('trace-songs').waitFor();
  }, '[data-testid="trace-col"]'));
  // The week's songs, with a change waiting to be saved.
  bad.push(...await hoverCheck(page, `/playlists/${playlist_id}`, async () => {
    await page.getByTestId('week-view-songs').click();
    await page.getByTestId('song-row').first().click();
    await page.getByTestId('detail-panel').locator('input[data-testid^="input-"]').first().fill('Other');
    await page.getByTestId('detail-save').click();
    await expect(page.getByTestId('song-edit-count')).toBeVisible();
  }));
  bad.push(...await hoverCheck(page, `/stations/${slug}`, async () => {
    await page.getByTestId('delete-station').click();
    await expect(page.getByTestId('confirm-dialog-cancel')).toBeVisible();
  }, '[data-testid="confirm-dialog"]')); // the page behind is covered
  // Not reached here: Station Server's "send this week" (needs a linked
  // Station Server). It uses the theme's .btn-primary like the rest.
  expect(bad).toEqual([]);
});
