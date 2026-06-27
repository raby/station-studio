import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import StatusBanner from './StatusBanner.svelte';

/**
 * §16.5.t2 — pin StatusBanner's contract. Sibling of ErrorBanner;
 * three things are load-bearing:
 *
 *   1. Null/empty message → renders NOTHING (live region only
 *      fires when there is something to announce).
 *   2. Always `role='status'` + `aria-live='polite'`. There is no
 *      assertive variant — a successful completion never warrants
 *      interrupting SR speech (§16.5 c5 severity policy).
 *   3. Prefix defaults to 'Success'; explicit prefix wins; empty
 *      string suppresses the label entirely.
 */
describe('StatusBanner', () => {
  afterEach(() => cleanup());

  it('renders nothing when message is null', () => {
    const { container } = render(StatusBanner, { props: { message: null } });
    expect(container.querySelector('[data-testid="status-banner"]')).toBeNull();
  });

  it('renders nothing when message is empty string', () => {
    const { container } = render(StatusBanner, { props: { message: '' } });
    expect(container.querySelector('[data-testid="status-banner"]')).toBeNull();
  });

  it("renders role='status' + aria-live='polite' + 'Success:' prefix by default", () => {
    const { container } = render(StatusBanner, {
      props: { message: 'Wrote to /tmp/out.xml' },
    });
    const banner = container.querySelector('[data-testid="status-banner"]');
    expect(banner).not.toBeNull();
    expect(banner).toHaveAttribute('role', 'status');
    expect(banner).toHaveAttribute('aria-live', 'polite');
    expect(banner!.textContent).toContain('Success:');
    expect(banner!.textContent).toContain('Wrote to /tmp/out.xml');
  });

  it("explicit prefix={null} falls back to the 'Success' default", () => {
    // §16.5.t2 c1-review R2 — pins the null-prefix branch
    // independently of the prop default. A refactor that changed
    // the default to 'Success' or undefined would silently break
    // callers passing `prefix={someNullable}` if this test
    // weren't here.
    const { container } = render(StatusBanner, {
      props: { message: 'Done', prefix: null },
    });
    const banner = container.querySelector('[data-testid="status-banner"]');
    expect(banner!.textContent).toContain('Success:');
    expect(banner!.textContent).toContain('Done');
  });

  it('explicit prefix overrides the default', () => {
    const { container } = render(StatusBanner, {
      props: { message: '/abs/out.xml (12 KB)', prefix: 'Wrote' },
    });
    const banner = container.querySelector('[data-testid="status-banner"]');
    expect(banner!.textContent).toContain('Wrote:');
    expect(banner!.textContent).not.toContain('Success:');
  });

  it('empty-string prefix suppresses the label entirely', () => {
    const { container } = render(StatusBanner, {
      props: { message: 'Done', prefix: '' },
    });
    const banner = container.querySelector('[data-testid="status-banner"]');
    expect(banner!.textContent).not.toContain('Success:');
    expect(banner!.textContent?.trim()).toBe('Done');
    // No <strong> child when prefix is suppressed.
    expect(banner!.querySelector('strong')).toBeNull();
  });

  it('custom testId overrides the default for selector compat', () => {
    // §16.5.t2 migration — disk-export-status pins its existing
    // testid so trace.test.ts queries keep working.
    const { container } = render(StatusBanner, {
      props: { message: 'Wrote', testId: 'disk-export-status' },
    });
    expect(
      container.querySelector('[data-testid="disk-export-status"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="status-banner"]'),
    ).toBeNull();
  });

  it('compact mode strips the bordered-box chrome but keeps the live region', () => {
    const { container } = render(StatusBanner, {
      props: { message: 'Saved', compact: true },
    });
    const banner = container.querySelector('[data-testid="status-banner"]');
    expect(banner).toHaveAttribute('role', 'status');
    expect(banner).toHaveAttribute('aria-live', 'polite');
    expect(banner!.className).toContain('compact');
  });
});
