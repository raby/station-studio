import { test, expect, type APIRequestContext } from '@playwright/test';
import { createStation, importTinyPool, generate, minimalTemplate } from './fixtures';

/**
 * A station's generated weeks sort from their headings (plan item 12b).
 * Vitest covers the order; this covers what it can't: the real page,
 * and that on a narrow screen, where the headings are hidden, a sort
 * button that has keyboard focus shows rather than focus vanishing.
 */
// A station with two generated weeks. Each test makes its own: tests
// share the backend's database, and a slug can only be made once.
async function twoWeeks(request: APIRequestContext, slug: string) {
  await importTinyPool(request, 'A');
  await createStation(request, slug, slug, 'A', minimalTemplate(slug));
  await generate(request, slug, 1);
  await generate(request, slug, 2);
}

test.describe('sorting a station’s weeks', () => {
  test('Generated sorts newest first, then turns round', async ({ page, request }) => {
    await twoWeeks(request, 'weeks-sort-wide');
    await page.goto('/stations/weeks-sort-wide');
    const generated = page.getByRole('columnheader', { name: 'Generated' });
    await page.getByTestId('sort-generated').click();
    await expect(generated).toHaveAttribute('aria-sort', 'descending');
    await page.getByTestId('sort-generated').click();
    await expect(generated).toHaveAttribute('aria-sort', 'ascending');
  });

  test('on a narrow screen, a focused sort button shows', async ({ page, request }) => {
    await twoWeeks(request, 'weeks-sort-narrow');
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto('/stations/weeks-sort-narrow');
    const button = page.getByTestId('sort-week');
    // The headings are clipped to a 1px box; a clip doesn't shrink the
    // button's own box, so it's the headings' box that's measured.
    const headings = page.getByTestId('playlists-table').locator('thead');
    const width = async () => (await headings.boundingBox())?.width ?? 0;
    await button.waitFor({ state: 'attached' });
    expect(await width()).toBeLessThan(5);
    await button.focus();
    await expect(button).toBeFocused();
    await expect.poll(width).toBeGreaterThan(20);
  });
});
