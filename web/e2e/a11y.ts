/**
 * §16.5 c1 — shared axe-core check helper for Playwright specs.
 *
 * `await assertNoA11yViolations(page)` runs an axe-core WCAG 2.1
 * audit against the current page and fails the test if any
 * `serious` or `critical` violations are found. `minor` /
 * `moderate` issues surface in the failure message body but don't
 * gate CI (they tend to be background/contrast nits that need
 * design judgment, not regression catches).
 *
 * Why axe only catches ~35% of real a11y bugs (per
 * deque.com/axe-coverage): heuristics can't tell when a
 * semantically-correct `<button>` has a label that reads aloud as
 * "icon icon". The remaining 65% needs a VoiceOver / NVDA pass —
 * see DESIGN.md §16.5 "Manual sweep with VoiceOver".
 *
 * Usage in a spec:
 *   import { assertNoA11yViolations } from './a11y';
 *   await page.goto('/');
 *   await assertNoA11yViolations(page);
 */
import { AxeBuilder } from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

/** Severities that fail CI. Tweak deliberately — every level here
 *  must have an actionable fix path. `minor` is intentionally
 *  omitted (mostly contrast / decorative noise). */
const FAILING_IMPACTS = new Set(['serious', 'critical']);

export async function assertNoA11yViolations(
  page: Page,
  opts: { include?: string[]; exclude?: string[] } = {},
): Promise<void> {
  let builder = new AxeBuilder({ page }).withTags([
    'wcag2a',
    'wcag2aa',
    'wcag21a',
    'wcag21aa',
  ]);
  // §16.5 c1-review R6 — include + exclude share shape (string[])
  // so callers can scope to multiple regions without dropping
  // selectors silently (the prior single-string include forced
  // callers to either chain twice or lose coverage).
  if (opts.include?.length) {
    for (const sel of opts.include) builder = builder.include(sel);
  }
  if (opts.exclude?.length) {
    for (const sel of opts.exclude) builder = builder.exclude(sel);
  }
  const results = await builder.analyze();
  const failing = results.violations.filter(
    (v) => v.impact && FAILING_IMPACTS.has(v.impact),
  );
  // §16.5 c1-review R5 — log only when a failing-impact finding
  // is present. Earlier impl logged on ANY violation, which would
  // drown CI in 'minor: region missing landmark' chatter as c2/c3/c4
  // add per-route audits. Keeping a single-line summary on passing
  // runs surfaces "the audit ran" without the violation noise.
  if (failing.length) {
    console.log(
      `[axe] ${failing.length} failing violation(s) on ${page.url()}:`,
      results.violations.map((v) => `${v.impact}: ${v.id} — ${v.help}`),
    );
  } else if (results.violations.length) {
    console.log(
      `[axe] ${page.url()}: 0 failing, ${results.violations.length} minor/moderate (not gated)`,
    );
  }
  expect(
    failing,
    `Axe found ${failing.length} serious/critical violations: ` +
      failing.map((v) => `${v.id} (${v.help})`).join('; '),
  ).toEqual([]);
}
