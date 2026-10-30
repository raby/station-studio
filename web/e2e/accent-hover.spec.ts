import { test, expect, type Page } from '@playwright/test';
import { createStation, importTinyPool, generate, minimalTemplate } from './fixtures';

/**
 * Accent (primary) buttons keep a readable background on hover. The
 * app-wide `button:hover` turns a button panel-grey; a component's own
 * accent style has to win over it, or the button goes white text on
 * light grey (it did on several pages).
 */
async function hoverCheck(page: Page, path: string, prepare?: () => Promise<void>): Promise<string[]> {
  await page.goto(path);
  await page.waitForLoadState('networkidle');
  // Colours change at once, so each read is the settled state.
  await page.addStyleTag({ content: '*, *::before, *::after { transition: none !important; }' });
  if (prepare) await prepare();
  const accent = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim());
  const buttons = page.locator('button:not([disabled])');
  const bad: string[] = [];
  const n = await buttons.count();
  for (let i = 0; i < n; i++) {
    const b = buttons.nth(i);
    if (!(await b.isVisible())) continue;
    await page.mouse.move(0, 0);
    const before = await b.evaluate((el) => getComputedStyle(el).backgroundColor);
    const isAccent = await b.evaluate((el, a) => {
      const probe = document.createElement('div');
      probe.style.color = a;
      document.body.appendChild(probe);
      const rgb = getComputedStyle(probe).color;
      probe.remove();
      return getComputedStyle(el).backgroundColor === rgb;
    }, accent);
    if (!isAccent) continue;
    await b.hover();
    const after = await b.evaluate((el) => getComputedStyle(el).backgroundColor);
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
    if (ratio < 4.5) {
      bad.push(`${path} "${(await b.innerText()).trim()}": ${color} on ${after} (was ${before}), contrast ${ratio.toFixed(2)}`);
    }
  }
  return bad;
}

for (const scheme of ['light', 'dark'] as const) test(`accent buttons stay readable on hover, on every page (${scheme})`, async ({ page, request }) => {
  await page.emulateMedia({ colorScheme: scheme });
  const slug = `hover-check-${scheme}`;
  await importTinyPool(request, 'A');
  await createStation(request, slug, `Hover Check ${scheme}`, 'A', minimalTemplate(slug));
  const { playlist_id } = await generate(request, slug, 1);
  const bad: string[] = [];
  for (const path of ['/', '/settings', '/sources', '/stations/new', `/stations/${slug}`,
    `/playlists/${playlist_id}`]) {
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
  expect(bad).toEqual([]);
});
