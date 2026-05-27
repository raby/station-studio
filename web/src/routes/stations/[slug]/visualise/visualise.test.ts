import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

/**
 * §16.2.2.t2 — load-function pin for the visualise route.
 *
 * The api.test pins getStationSidecar; the editor.test pins helpers;
 * the component tests pin the canvas internals; Playwright e2e
 * exercises the full stack. This file closes the gap in the middle:
 * the route's compose-of (fetch → cast → render-states).
 *
 * Three states are pinned:
 *   - loading              → "Loading…"
 *   - 404 (missing slug)   → error message + back link
 *   - 200 happy path       → EditorCanvas mounts
 *   - 200 + sidecar_error  → warning banner above the canvas
 */


const {
  getStationSidecarMock,
  FakeApiError,
  pageStore,
  setSlug,
} = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }
  let currentSlug: string | undefined = 'viz-test';
  const subscribers = new Set<(v: { params: { slug: string | undefined } }) => void>();
  const pageStore = {
    subscribe(fn: (v: { params: { slug: string | undefined } }) => void) {
      subscribers.add(fn);
      fn({ params: { slug: currentSlug } });
      return () => subscribers.delete(fn);
    },
  };
  const setSlug = (s: string | undefined) => {
    currentSlug = s;
    for (const fn of subscribers) fn({ params: { slug: currentSlug } });
  };
  return {
    getStationSidecarMock: vi.fn(),
    FakeApiError,
    pageStore,
    setSlug,
  };
});


vi.mock('$lib/api', () => ({
  getStationSidecar: (...args: unknown[]) => getStationSidecarMock(...args),
  ApiError: FakeApiError,
}));


vi.mock('$app/stores', () => ({
  page: pageStore,
}));


import Page from './+page.svelte';


function makeTemplate() {
  return {
    station: 'viz-test', name: 'Viz Test', cadence: 'weekly', rng: 'off',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    cursor: { base_advance_per_week: 0, cycle_weeks: 1 },
    layout: {
      unit: 'slot', unit_labels: [], row_start_offsets: [],
      flat_slots: [{ source: 'A', strategy: 'pick', size: 1 }],
    },
  };
}


function makeSidecar() {
  return {
    schema_version: 1,
    sections: [
      { id: 'all-slots', name: 'All slots',
        blocks: [{ kind: 'atomic', slot_index: 0 }] },
    ],
    macros: [],
    layout_order: ['all-slots'],
  };
}


describe('visualise +page.svelte', () => {
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    setSlug('viz-test');
  });

  it('shows "Loading…" while the sidecar fetch is in flight', async () => {
    // Deferred promise — never resolves during the test window.
    getStationSidecarMock.mockReturnValueOnce(new Promise(() => {}));
    render(Page);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('renders <EditorCanvas> on 200 happy path', async () => {
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    render(Page);
    expect(await screen.findByTestId('editor-canvas')).toBeInTheDocument();
    // The canvas renders the one chip from the minimal fixture.
    expect(await screen.findByTestId('chip')).toBeInTheDocument();
    // No warning banner on the happy path.
    expect(screen.queryByTestId('sidecar-warning')).toBeNull();
  });

  it('renders the sidecar_error warning banner on broken-sidecar fallback', async () => {
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(), // trivial fallback, populated by server
      sidecar_error: 'sidecar JSON malformed at line 3: unexpected token',
    });
    render(Page);
    const banner = await screen.findByTestId('sidecar-warning');
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent(/malformed/i);
    // Canvas STILL renders alongside the warning (Decision E
    // "frontend never handles 'no sidecar'").
    expect(await screen.findByTestId('editor-canvas')).toBeInTheDocument();
  });

  it('renders the API error inline on station-not-found (404)', async () => {
    getStationSidecarMock.mockRejectedValueOnce(
      new FakeApiError(404, "404 Not Found: station 'ghost' not found"),
    );
    render(Page);
    expect(await screen.findByTestId('visualise-error')).toHaveTextContent(
      /ghost/,
    );
    expect(screen.queryByTestId('editor-canvas')).toBeNull();
  });

  it('renders the back link to the station detail page', async () => {
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    render(Page);
    const back = await screen.findByRole('link', { name: '← viz-test' });
    expect(back).toHaveAttribute('href', '/stations/viz-test');
  });

  it('renders "missing slug" when route param is absent', async () => {
    setSlug(undefined);
    render(Page);
    expect(await screen.findByTestId('visualise-error')).toHaveTextContent(
      /missing slug/i,
    );
    // No fetch should have been attempted.
    expect(getStationSidecarMock).not.toHaveBeenCalled();
  });
});
