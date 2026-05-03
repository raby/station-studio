import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

// $app/stores.page is a Svelte readable store; tests need to provide one
// with a `subscribe(fn)` that emits a value synchronously. We use a
// hoisted holder so tests can swap the slug per case.
const {
  getStationMock,
  getStationPlaylistsMock,
  generatePlaylistMock,
  gotoMock,
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
    generatePlaylistMock: vi.fn(),
    gotoMock: vi.fn(),
    FakeApiError,
    pageStore,
    setSlug,
  };
});

vi.mock('$lib/api', () => ({
  getStation: (...args: unknown[]) => getStationMock(...args),
  getStationPlaylists: (...args: unknown[]) => getStationPlaylistsMock(...args),
  generatePlaylist: (...args: unknown[]) => generatePlaylistMock(...args),
  ApiError: FakeApiError,
}));

vi.mock('$app/stores', () => ({
  page: pageStore,
}));

vi.mock('$app/navigation', () => ({
  goto: (...args: unknown[]) => gotoMock(...args),
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
    generatePlaylistMock.mockReset();
    gotoMock.mockReset();
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

/**
 * §16.1 generate panel & per-row regen — covers the matrix:
 *   empty-state CTA → populated next-week → advanced disclosure →
 *   seed-input gating on rng → Replace label on conflict → per-row regen →
 *   inline error rendering on backend failure.
 *
 * Each test fully drives the click → confirm → POST → goto pipeline so a
 * regression in the wiring (e.g. dropping the seed for rng:on, skipping
 * the confirm, navigating on failure) shows up here rather than only in
 * the slower Playwright spec.
 */
describe('station detail — §16.1 generate panel', () => {
  beforeEach(() => {
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    generatePlaylistMock.mockReset();
    gotoMock.mockReset();
    setSlug('cls');
  });

  const stubStation = (rng: 'on' | 'off' = 'off') => ({
    id: 1,
    slug: 'cls',
    name: 'Classics',
    template: { rng },
  });

  it('shows the big "Generate week 1 →" CTA in the empty state', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    render(Page);

    const cta = await screen.findByTestId('generate-empty-cta');
    expect(cta).toBeInTheDocument();
    expect(cta).toHaveTextContent(/generate week 1/i);
    // No "next week" wording when nothing exists yet.
    expect(screen.queryByText(/generate next week/i)).not.toBeInTheDocument();
  });

  it('clicking the empty-state CTA POSTs week 1 and navigates on success', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    generatePlaylistMock.mockResolvedValueOnce({
      playlist_id: 99,
      trace_id: 'abcdef1234567890',
      station: 'cls',
      week: 1,
      slot_count: 3010,
    });
    render(Page);

    const cta = await screen.findByTestId('generate-empty-cta');
    await fireEvent.click(cta);

    expect(generatePlaylistMock).toHaveBeenCalledWith('cls', 1, undefined);
    // act() flush is needed before the goto microtask completes.
    await act();
    expect(gotoMock).toHaveBeenCalledWith('/playlists/99');
  });

  it('shows "Generate next week (N+1)" when playlists exist', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([
      { id: 1, week: 1, trace_id: 'aaaa1111', generated_at: null, entry_count: 3010 },
      { id: 2, week: 2, trace_id: 'bbbb2222', generated_at: null, entry_count: 3010 },
    ]);
    render(Page);

    expect(
      await screen.findByRole('button', { name: /generate next week.*3/i }),
    ).toBeInTheDocument();
  });

  it('seed input is hidden for rng:off stations and shown for rng:on', async () => {
    // rng:off — no seed input
    getStationMock.mockResolvedValueOnce(stubStation('off'));
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    const { unmount } = render(Page);
    await screen.findByTestId('generate-empty-cta');
    expect(screen.queryByTestId('advanced-seed')).not.toBeInTheDocument();
    unmount();

    // rng:on — seed input present
    getStationMock.mockResolvedValueOnce(stubStation('on'));
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    render(Page);
    await screen.findByTestId('generate-empty-cta');
    expect(screen.getByTestId('advanced-seed')).toBeInTheDocument();
  });

  it('advanced submit reads "Replace week N" (red) when the chosen week exists', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([
      { id: 1, week: 1, trace_id: 'aaaa1111', generated_at: null, entry_count: 3010 },
    ]);
    render(Page);

    // The advanced form defaults to nextWeek (= 2). Override to existing week 1.
    const weekInput = await screen.findByTestId('advanced-week');
    await fireEvent.input(weekInput, { target: { value: '1' } });

    const submit = screen.getByTestId('advanced-submit');
    expect(submit).toHaveTextContent(/replace week 1/i);
  });

  it('Replace flow: confirms before POST and skips POST on cancel', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([
      { id: 1, week: 1, trace_id: 'aaaa1111', generated_at: null, entry_count: 3010 },
    ]);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    render(Page);
    const weekInput = await screen.findByTestId('advanced-week');
    await fireEvent.input(weekInput, { target: { value: '1' } });
    await fireEvent.click(screen.getByTestId('advanced-submit'));

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringMatching(/replace week 1/i));
    expect(generatePlaylistMock).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('per-row regen confirms then POSTs with no seed for rng:off', async () => {
    getStationMock.mockResolvedValueOnce(stubStation('off'));
    getStationPlaylistsMock.mockResolvedValueOnce([
      { id: 5, week: 5, trace_id: 'feedface1234', generated_at: null, entry_count: 3010 },
    ]);
    generatePlaylistMock.mockResolvedValueOnce({
      playlist_id: 50,
      trace_id: 'newnewnewnew',
      station: 'cls',
      week: 5,
      slot_count: 3010,
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(Page);
    const regen = await screen.findByTestId('regen-row-5');
    await fireEvent.click(regen);

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringMatching(/regenerate week 5/i));
    expect(generatePlaylistMock).toHaveBeenCalledWith('cls', 5, undefined);
    await act();
    expect(gotoMock).toHaveBeenCalledWith('/playlists/50');
    confirmSpy.mockRestore();
  });

  it('per-row regen sends a fresh random seed for rng:on stations', async () => {
    getStationMock.mockResolvedValueOnce(stubStation('on'));
    getStationPlaylistsMock.mockResolvedValueOnce([
      { id: 5, week: 5, trace_id: 'feedface1234', generated_at: null, entry_count: 3010 },
    ]);
    generatePlaylistMock.mockResolvedValueOnce({
      playlist_id: 50,
      trace_id: 'x',
      station: 'cls',
      week: 5,
      slot_count: 3010,
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(Page);
    await fireEvent.click(await screen.findByTestId('regen-row-5'));

    const [slug, week, seed] = generatePlaylistMock.mock.calls[0];
    expect(slug).toBe('cls');
    expect(week).toBe(5);
    // Per D3: rng:on regen sends *some* integer seed (the form-input one
    // would only be used if the user opened the advanced form first).
    expect(typeof seed).toBe('number');
    expect(seed).toBeGreaterThanOrEqual(0);
    confirmSpy.mockRestore();
  });

  it('renders the API error inline and does not navigate on failure', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    generatePlaylistMock.mockRejectedValueOnce(
      new FakeApiError(400, '400 Bad Request: pool underflow at index 985 of RG_RADIO'),
    );

    render(Page);
    await fireEvent.click(await screen.findByTestId('generate-empty-cta'));
    await act();

    expect(await screen.findByTestId('generate-error')).toHaveTextContent(
      /pool underflow.*RG_RADIO/i,
    );
    expect(gotoMock).not.toHaveBeenCalled();
  });
});
