import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import ErrorBanner from './ErrorBanner.svelte';

/**
 * §16.5 c1-review R9 — pin the ErrorBanner ARIA + render contract
 * before c3/c4 wire it across the app. Three things are load-bearing:
 *
 *   1. Null/empty message → component renders NOTHING (so the live
 *      region only fires when there is something to announce).
 *   2. severity='polite'    → role='status' + aria-live='polite'
 *      severity='assertive' → role='alert'  + aria-live='assertive'
 *      (paired so role + live agree; mixing them is the conflicting
 *      ARIA c1-review R2 fixed).
 *   3. prefix defaults from severity ('Notice' for polite,
 *      'Error' for assertive). Explicit prefix wins.
 */
describe('ErrorBanner', () => {
  afterEach(() => cleanup());

  it('renders nothing when message is null', () => {
    const { container } = render(ErrorBanner, { props: { message: null } });
    expect(container.querySelector('[data-testid="error-banner"]')).toBeNull();
  });

  it('renders nothing when message is empty string', () => {
    const { container } = render(ErrorBanner, { props: { message: '' } });
    expect(container.querySelector('[data-testid="error-banner"]')).toBeNull();
  });

  it("polite severity → role='status' + aria-live='polite' + 'Notice:' prefix", () => {
    const { container } = render(ErrorBanner, {
      props: { message: 'Saved with warnings', severity: 'polite' },
    });
    const banner = container.querySelector('[data-testid="error-banner"]');
    expect(banner).not.toBeNull();
    expect(banner).toHaveAttribute('role', 'status');
    expect(banner).toHaveAttribute('aria-live', 'polite');
    expect(banner!.textContent).toContain('Notice:');
    expect(banner!.textContent).toContain('Saved with warnings');
  });

  it("assertive severity → role='alert' + aria-live='assertive' + 'Error:' prefix", () => {
    const { container } = render(ErrorBanner, {
      props: { message: 'Server unreachable', severity: 'assertive' },
    });
    const banner = container.querySelector('[data-testid="error-banner"]');
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner).toHaveAttribute('aria-live', 'assertive');
    expect(banner!.textContent).toContain('Error:');
  });

  it('explicit prefix overrides the severity-derived default', () => {
    const { container } = render(ErrorBanner, {
      props: { message: 'Watch out', severity: 'polite', prefix: 'Warning' },
    });
    const banner = container.querySelector('[data-testid="error-banner"]');
    expect(banner!.textContent).toContain('Warning:');
    expect(banner!.textContent).not.toContain('Notice:');
  });
});

