import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import NarrowScreenNotice from './NarrowScreenNotice.svelte';

/**
 * §16.20 c1 — pin the NarrowScreenNotice contract:
 *
 *   1. Renders a region with the heading as its accessible
 *      name (aria-labelledby resolves to a unique id).
 *   2. Body + back-link copy reflect the props.
 *   3. testId override works for callers that pin a specific
 *      selector (e.g. visualise-narrow-notice).
 *   4. The viewport-toggle CSS is in the @media query — jsdom
 *      doesn't simulate viewport-based media-query matching, so
 *      the visibility test belongs in Playwright at c2.
 */
describe('NarrowScreenNotice', () => {
  afterEach(() => cleanup());

  it('renders heading + body + back-link from defaults', () => {
    const { container } = render(NarrowScreenNotice, {
      props: { backHref: '/back' },
    });
    const notice = container.querySelector(
      '[data-testid="narrow-screen-notice"]',
    );
    expect(notice).not.toBeNull();
    // A labelled <section> is implicitly role=region — no explicit
    // role attribute needed (svelte-check a11y rule flags the
    // redundancy). The aria-labelledby assertion in the next test
    // pins the landmark name contract.
    expect(notice!.tagName).toBe('SECTION');
    expect(notice!.textContent).toContain('wider screen');
    // §16.20 c1-review R2 — default body is now generic
    // ('desktop-only', not visualise-specific). Callers with
    // surface-specific copy pass an explicit body.
    expect(notice!.textContent).toContain('desktop-only');
    const link = notice!.querySelector('.back-link');
    expect(link).toHaveAttribute('href', '/back');
    expect(link!.textContent).toContain('Back');
  });

  it('aria-labelledby points to the heading id', () => {
    const { container } = render(NarrowScreenNotice, {
      props: { backHref: '/back' },
    });
    const notice = container.querySelector(
      '[data-testid="narrow-screen-notice"]',
    );
    const labelId = notice!.getAttribute('aria-labelledby');
    expect(labelId).toBeTruthy();
    const heading = container.querySelector(`#${labelId}`);
    expect(heading).not.toBeNull();
    expect(heading!.tagName).toBe('H2');
  });

  it('heading + body + backLabel props override defaults', () => {
    const { container } = render(NarrowScreenNotice, {
      props: {
        backHref: '/elsewhere',
        heading: 'Custom title',
        body: 'Custom body explanation.',
        backLabel: 'Go back',
      },
    });
    const notice = container.querySelector(
      '[data-testid="narrow-screen-notice"]',
    );
    expect(notice!.querySelector('h2')!.textContent).toBe('Custom title');
    expect(notice!.querySelector('p')!.textContent).toBe(
      'Custom body explanation.',
    );
    const link = notice!.querySelector('.back-link');
    expect(link).toHaveAttribute('href', '/elsewhere');
    expect(link!.textContent?.trim()).toBe('Go back');
  });

  it('custom testId overrides the default for selector compat', () => {
    const { container } = render(NarrowScreenNotice, {
      props: { backHref: '/back', testId: 'visualise-narrow-notice' },
    });
    expect(
      container.querySelector('[data-testid="visualise-narrow-notice"]'),
    ).not.toBeNull();
    expect(
      container.querySelector('[data-testid="narrow-screen-notice"]'),
    ).toBeNull();
  });

  it('two notices mounted simultaneously have distinct aria-labelledby ids', () => {
    // §16.20 c1-review R3 — the original test used cleanup()
    // between renders, which only proved per-mount id
    // regeneration. The docstring claim is stronger: 'multiple
    // notices on one page do not collide'. Mount two in the
    // same container (no cleanup) and assert the ids differ —
    // this catches a regression where headingId became
    // module-level / shared.
    const { container } = render(NarrowScreenNotice, {
      props: { backHref: '/a', testId: 'first-notice' },
    });
    // Mount the second instance into the same JSDOM document
    // (testing-library renders into document.body by default;
    // back-to-back renders share the body unless cleanup runs).
    render(NarrowScreenNotice, {
      props: { backHref: '/b', testId: 'second-notice' },
    });
    const id1 = document
      .querySelector('[data-testid="first-notice"]')!
      .getAttribute('aria-labelledby');
    const id2 = document
      .querySelector('[data-testid="second-notice"]')!
      .getAttribute('aria-labelledby');
    expect(id1).toBeTruthy();
    expect(id2).toBeTruthy();
    expect(id1).not.toBe(id2);
    // Both ids must resolve to their own heading — collision
    // would cause one section to point at the wrong heading.
    expect(document.getElementById(id1!)).not.toBeNull();
    expect(document.getElementById(id2!)).not.toBeNull();
  });

  it('headingLevel prop controls the rendered heading tag (default h2)', () => {
    // §16.20 c1-review R6 — pin the new prop's contract.
    const { container } = render(NarrowScreenNotice, {
      props: { backHref: '/back' },
    });
    expect(
      container.querySelector(
        '[data-testid="narrow-screen-notice"] h2',
      ),
    ).not.toBeNull();
  });

  it('explicit headingLevel renders the matching tag', () => {
    const { container } = render(NarrowScreenNotice, {
      props: { backHref: '/back', headingLevel: 1 },
    });
    expect(
      container.querySelector(
        '[data-testid="narrow-screen-notice"] h1',
      ),
    ).not.toBeNull();
    expect(
      container.querySelector(
        '[data-testid="narrow-screen-notice"] h2',
      ),
    ).toBeNull();
  });
});
