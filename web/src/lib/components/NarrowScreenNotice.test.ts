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
    expect(notice!.textContent).toContain('drag-and-drop');
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

  it('each render generates a fresh heading id so multiple notices on one page do not collide', () => {
    const { container } = render(NarrowScreenNotice, {
      props: { backHref: '/a' },
    });
    const id1 = container
      .querySelector('[data-testid="narrow-screen-notice"]')!
      .getAttribute('aria-labelledby');
    cleanup();
    const { container: c2 } = render(NarrowScreenNotice, {
      props: { backHref: '/b' },
    });
    const id2 = c2
      .querySelector('[data-testid="narrow-screen-notice"]')!
      .getAttribute('aria-labelledby');
    expect(id1).not.toBe(id2);
  });
});
