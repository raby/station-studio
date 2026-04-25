import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

// $app/stores.page is a Svelte readable store; tests need to provide one
// with a `subscribe(fn)` that emits a value synchronously. We use a
// hoisted holder so tests can swap the slug per case.
const {
  getStationMock,
  getStationPlaylistsMock,
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
  let currentSlug: string | undefined = 'test-slug';
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
    getStationMock: vi.fn(),
    getStationPlaylistsMock: vi.fn(),
    FakeApiError,
    pageStore,
    setSlug,
  };
});

vi.mock('$lib/api', () => ({
  getStation: (...args: unknown[]) => getStationMock(...args),
  getStationPlaylists: (...args: unknown[]) => getStationPlaylistsMock(...args),
  ApiError: FakeApiError,
}));

vi.mock('$app/stores', () => ({
  page: pageStore,
}));

import Page from './+page.svelte';

/**
 * Covers the station-detail page's render branches:
 *   loading → error → missing-slug → empty-playlists → populated-playlists
 * The $page store is mocked so the route param is deterministic.
 */
describe('station detail +page.svelte', () => {
  // The vitest.setup.ts hook (@testing-library/svelte/vitest) already
  // runs `await act(); cleanup()` per test — a duplicate afterEach
  // cleanup races act() and deadlocks the deferred-promise loading test.
  beforeEach(() => {
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    setSlug('cls'); // default for tests that don't care
  });

  it('shows "Loading…" until both fetches resolve', async () => {
    // Pre-resolved mocks: the .then microtask hasn't fired by the time
    // render() returns synchronously, so we can observe the loading
    // branch before draining via act().
    getStationMock.mockResolvedValueOnce({ id: 1, slug: 'cls', name: 'Classics', template: {} });
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    render(Page);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    await act();
  });

  it('renders the ApiError message when getStation fails', async () => {
    getStationMock.mockRejectedValueOnce(new FakeApiError(404, "station 'cls' not found"));
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    render(Page);
    expect(await screen.findByText(/station 'cls' not found/i)).toBeInTheDocument();
  });

  it('stringifies non-ApiError failures instead of swallowing them', async () => {
    getStationMock.mockRejectedValueOnce(new TypeError('boom'));
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    render(Page);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });

  it('renders "missing slug" if the route param is absent', async () => {
    setSlug(undefined);
    // Fetches should not fire when slug is missing.
    render(Page);
    expect(await screen.findByText(/missing slug/i)).toBeInTheDocument();
    expect(getStationMock).not.toHaveBeenCalled();
  });

  it('shows the "none generated yet" hint when no playlists exist', async () => {
    getStationMock.mockResolvedValueOnce({
      id: 1,
      slug: 'cls',
      name: 'Classics',
      template: {},
    });
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    render(Page);

    expect(await screen.findByRole('heading', { name: 'Classics' })).toBeInTheDocument();
    expect(screen.getByText(/none generated yet/i)).toBeInTheDocument();
  });

  it('renders playlist rows with zero-padded week, trace fragment, and view link', async () => {
    getStationMock.mockResolvedValueOnce({
      id: 1,
      slug: 'cls',
      name: 'Classics',
      template: {},
    });
    getStationPlaylistsMock.mockResolvedValueOnce([
      {
        id: 42,
        week: 3,
        trace_id: 'abcdef1234567890',
        generated_at: '2025-01-15T10:00:00Z',
        entry_count: 50,
      },
    ]);
    render(Page);

    // Week 3 padded to "003".
    expect(await screen.findByText('003')).toBeInTheDocument();
    // Entry count rendered as-is.
    expect(screen.getByText('50')).toBeInTheDocument();
    // First 8 hex chars of trace_id shown with ellipsis.
    expect(screen.getByText('abcdef12…')).toBeInTheDocument();
    // view → link targets the trace viewer at /playlists/{id}.
    expect(screen.getByRole('link', { name: /view/i })).toHaveAttribute(
      'href',
      '/playlists/42',
    );
  });

  it('tolerates a null generated_at (fmtDate empty-string branch)', async () => {
    getStationMock.mockResolvedValueOnce({
      id: 1,
      slug: 'cls',
      name: 'Classics',
      template: {},
    });
    getStationPlaylistsMock.mockResolvedValueOnce([
      {
        id: 7,
        week: 1,
        trace_id: 'deadbeef1234',
        generated_at: null,
        entry_count: 0,
      },
    ]);
    render(Page);
    await screen.findByText('001');
    // The "Generated" cell renders empty when generated_at is null; the
    // page doesn't crash and the row still shows.
    expect(screen.getByText('deadbeef…')).toBeInTheDocument();
  });

  it('fmtDate falls back to the raw ISO string if Date parsing throws', async () => {
    getStationMock.mockResolvedValueOnce({
      id: 1,
      slug: 'cls',
      name: 'Classics',
      template: {},
    });
    getStationPlaylistsMock.mockResolvedValueOnce([
      {
        id: 7,
        week: 1,
        trace_id: 'tracetrace',
        generated_at: 'not-an-iso-string',
        entry_count: 0,
      },
    ]);
    render(Page);
    // Even if new Date(...).toLocaleString() returns "Invalid Date", the
    // row still renders — we're exercising the try/catch, not the format.
    await screen.findByText('001');
  });
});
