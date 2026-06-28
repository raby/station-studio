import { test, expect } from '@playwright/test';
import { assertNoA11yViolations } from './a11y';

/**
 * Wizard specs focus on the happy path — the full UI → API flow.
 *
 * Client-side validation messages are covered at the Vitest layer (commit 8)
 * because they need timing/interaction precision that makes them flaky in
 * Playwright. What matters here is: "filling the form + clicking submit
 * creates a station in the database and lands the user on the detail page".
 */
test.describe('station wizard', () => {
  test('wizard form renders all four fieldsets', async ({ page }) => {
    await page.goto('/stations/new');
    await expect(page.getByRole('heading', { name: 'New station' })).toBeVisible();
    for (const legend of ['Identity', 'Sources', 'Cursor', 'Layout']) {
      await expect(page.getByRole('group', { name: legend })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: 'Create station' })).toBeVisible();
  });

  test('§16.5 c3 — /stations/new passes axe-core WCAG audit', async ({ page }) => {
    // Wait for hydration so the form is mounted; pre-hydration the
    // tree is just the loading shell + skip-link.
    //
    // §16.5 c3-review R10 — race the meta-ready sentinel against
    // the ErrorBanner so a slow / failed GET /api/meta in CI
    // doesn't time out the test for the wrong reason. Either DOM
    // is a legitimate steady state; axe should audit whichever
    // landed.
    await page.goto('/stations/new');
    await Promise.race([
      page.locator('html[data-wizard-ready]').waitFor({ state: 'attached' }),
      page.getByTestId('error-banner').waitFor({ state: 'visible' }),
    ]);
    await assertNoA11yViolations(page);
  });

  test('§16.20 c2 — /stations/new passes axe-core audit at 375px viewport', async ({
    page,
  }) => {
    // §16.20 c2 — wizard form stacks to single column below 40rem
    // via _form-shared.css overrides; the sources table rotates
    // to cards with data-label::before pseudo-elements. Audit
    // verifies the responsive overrides don't regress label
    // associations / focus order / contrast on a narrow viewport.
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('/stations/new');
    await Promise.race([
      page.locator('html[data-wizard-ready]').waitFor({ state: 'attached' }),
      page.getByTestId('error-banner').waitFor({ state: 'visible' }),
    ]);
    await assertNoA11yViolations(page);
  });

  test('happy path: fill + submit creates station and lands on detail', async ({
    page,
    request,
  }) => {
    await page.goto('/stations/new');
    // Hydration sentinel: +page.svelte sets data-wizard-ready on <html>
    // in onMount. Needed because `bind:value` only wires up post-hydration,
    // so fills run against a pre-hydration DOM would silently drop. This
    // is the Playwright-recommended pattern (`networkidle` is discouraged).
    await expect(page.locator('html[data-wizard-ready]')).toBeAttached();

    // Identity
    await page.getByLabel('Slug').fill('wiz-happy');
    await page.getByLabel('Display name').fill('Wizard Happy');

    // Sources: first row's Name cell. Use getByRole('group', ...) to scope
    // by the <legend> text — `fieldset, { hasText: ... }` is case-insensitive
    // and matches sibling fieldsets whose hint text mentions the other name.
    const sourcesGroup = page.getByRole('group', { name: 'Sources' });
    const sourceNameInput = sourcesGroup.locator('table tbody tr').first().locator('input').first();
    await sourceNameInput.click();
    await sourceNameInput.fill('A');
    await page.keyboard.press('Tab');

    // Layout: collapse the default week-long labels/offsets to a single
    // Mon row — the template becomes a 1-slot playlist against pool A.
    // Use placeholders because getByLabel here is ambiguous (both labels
    // contain 'row_start_offsets' in their helper text).
    await page.getByPlaceholder('Mon, Tue, Wed, Thu, Fri, Sat, Sun').fill('Mon');
    await page.getByPlaceholder('0, 10, 20, 30, 40, 50, 50').fill('0');

    // Row-template block source dropdown. The select carries
    // data-testid="block-source"; the 'A' option is generated reactively
    // from `contentSourceNames`, so we still need to wait for it to attach.
    const blockSource = page.getByTestId('block-source');
    await expect(blockSource.locator('option[value="A"]')).toBeAttached({
      timeout: 5_000,
    });
    await blockSource.selectOption('A');

    await page.getByRole('button', { name: 'Create station' }).click();
    await page.waitForURL('**/stations/wiz-happy', { timeout: 10_000 });
    await expect(page.getByRole('heading', { name: 'Wizard Happy' })).toBeVisible();

    // And the row landed in the DB.
    const r = await request.get('http://127.0.0.1:8001/api/stations/wiz-happy');
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.slug).toBe('wiz-happy');
    expect(body.name).toBe('Wizard Happy');
  });
});
