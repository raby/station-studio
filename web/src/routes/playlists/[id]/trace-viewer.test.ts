import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

/**
 * Trace viewer (`playlists/[id]/+page.svelte`) — Vitest coverage backfill
 * (DESIGN.md §14.14, closing the §14.8 punt).
 *
 * The page was Playwright-only by §14.5 / §16.6 design. This file pins
 * its render-branch and disk-export-wiring contracts under jsdom so a
 * regression in those branches breaks fast (vitest milliseconds) instead
 * of slow (playwright seconds, only on full e2e runs).
 *
 * Layout-shaped behaviour (chip colours, table widths, scroll) stays
 * Playwright's job — jsdom doesn't render CSS faithfully and asserting
 * on `getBoundingClientRect()` in unit tests is a fool's errand.
 */

// Hoisted mock surface — same pattern as landing.test.ts / detail.test.ts.
const {
  getPlaylistMock,
  getTraceMock,
  getStationMock,
  exportPlaylistToDiskMock,
  FakeApiError,
  pageStore,
  setId,
} = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }
  // Page store carrying the route param (`id`). Tests swap via setId().
  let currentId: string | undefined = '1';
  const subscribers = new Set<(v: { params: { id: string | undefined } }) => void>();
  const pageStore = {
    subscribe(fn: (v: { params: { id: string | undefined } }) => void) {
      subscribers.add(fn);
      fn({ params: { id: currentId } });
      return () => subscribers.delete(fn);
    },
  };
  const setId = (s: string | undefined) => {
    currentId = s;
    for (const fn of subscribers) fn({ params: { id: currentId } });
  };
  return {
    getPlaylistMock: vi.fn(),
    getTraceMock: vi.fn(),
    getStationMock: vi.fn(),
    exportPlaylistToDiskMock: vi.fn(),
    FakeApiError,
    pageStore,
    setId,
  };
});

vi.mock('$lib/api', () => ({
  getPlaylist: (...args: unknown[]) => getPlaylistMock(...args),
  getTrace: (...args: unknown[]) => getTraceMock(...args),
  getStation: (...args: unknown[]) => getStationMock(...args),
  exportPlaylistToDisk: (...args: unknown[]) => exportPlaylistToDiskMock(...args),
  ApiError: FakeApiError,
}));

vi.mock('$app/stores', () => ({
  page: pageStore,
}));

import Page from './+page.svelte';

// ---- fixtures -------------------------------------------------------------

const stubStation = () => ({
  id: 1,
  slug: 'cls',
  name: 'Classics',
  template: {
    sources: [
      { name: 'A', colour: '#c0392b' },
      { name: 'B', colour: '#8e44ad' },
    ],
  },
});

const stubPlaylist = () => ({
  id: 7,
  station: 'cls',
  week: 3,
  trace_id: 'abc12345-trace-id',
  entries: [],
});

const stubTrace = (overrides: Record<string, unknown> = {}) => ({
  trace_id: 'abc12345-trace-id',
  station: 'cls',
  week: 3,
  rng: 'off',
  seed: null,
  generated_at: '2026-05-04T12:00:00Z',
  slots: [
    {
      slot: 1,
      unit: 'Mon',
      row: 1,
      block: 1,
      source: 'A',
      strategy: 'window',
      pool_index: 1,
      track_id: 100,
      track_name: 'Track Mon-1',
      track_artist: 'Artist 1',
      reason: 'first slot of row 1',
    },
    {
      slot: 2,
      unit: 'Mon',
      row: 1,
      block: 1,
      source: 'A',
      strategy: 'window',
      pool_index: 2,
      track_id: 101,
      track_name: 'Track Mon-2',
      track_artist: null,
      reason: 'second slot of row 1',
    },
    {
      slot: 3,
      unit: 'Tue',
      row: 2,
      block: 1,
      source: 'B',
      strategy: 'window',
      pool_index: 1,
      track_id: 200,
      track_name: 'Track Tue-1',
      track_artist: 'Artist 2',
      reason: 'first slot of row 2',
    },
  ],
  ...overrides,
});

// ---- render-branch tests --------------------------------------------------

describe('trace viewer +page.svelte — render branches', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('1');
  });

  it('renders "Invalid playlist ID" when the route param isn\'t numeric', async () => {
    setId('abc');
    render(Page);
    expect(await screen.findByText(/invalid playlist id/i)).toBeInTheDocument();
    // No fetches fire when the id is invalid — saves a 422 round-trip.
    expect(getTraceMock).not.toHaveBeenCalled();
  });

  it('shows "Loading…" before the fetches resolve', async () => {
    // Pre-resolved mocks: render() returns synchronously before microtasks
    // fire, so we observe the loading branch first. Same pattern as
    // landing.test.ts / detail.test.ts.
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    await act();
  });

  it('renders the ApiError message when getTrace 404s', async () => {
    getTraceMock.mockRejectedValueOnce(new FakeApiError(404, '404: trace for playlist 1 not found'));
    render(Page);
    expect(
      await screen.findByText(/trace for playlist 1 not found/i),
    ).toBeInTheDocument();
  });

  it('stringifies non-ApiError failures so the user still sees something', async () => {
    getTraceMock.mockRejectedValueOnce(new TypeError('network down'));
    render(Page);
    expect(await screen.findByText(/network down/i)).toBeInTheDocument();
  });

  it('renders header + meta + slot rows grouped by row when populated', async () => {
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    // Header shows the station name + zero-padded week ("003" for week 3).
    await screen.findByRole('heading', { name: /Classics/ });
    expect(screen.getByText('003')).toBeInTheDocument();

    // Trace metadata is laid out as <dl class="meta">.
    expect(screen.getByText(/abc12345-trace-id/)).toBeInTheDocument();
    expect(screen.getByText(/^off$/)).toBeInTheDocument();

    // Each row is a <section.row> with its own header. groupByRow sorts
    // by row index, so Mon (row 1) comes before Tue (row 2).
    expect(screen.getByText('Mon')).toBeInTheDocument();
    expect(screen.getByText('Tue')).toBeInTheDocument();

    // Slots from row 1 are present; slot 3 (Tue) is in its own group.
    expect(screen.getByText('Track Mon-1')).toBeInTheDocument();
    expect(screen.getByText('Track Mon-2')).toBeInTheDocument();
    expect(screen.getByText('Track Tue-1')).toBeInTheDocument();
    expect(screen.getByText('Artist 1')).toBeInTheDocument();
    // null artist renders as empty, not the literal "null".
    expect(screen.queryByText('null')).not.toBeInTheDocument();
  });

  it('renders empty for null generated_at (fmtDate guard branch)', async () => {
    // Defensive branch in fmtDate: `if (!iso) return ''`. Without this
    // test the branch is uncovered (review N3) and a regression that
    // returned `null` or `'null'` from fmtDate wouldn't break a test.
    getTraceMock.mockResolvedValueOnce(stubTrace({ generated_at: null }));
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    // Page renders without throwing; trace-id meta is still present.
    await screen.findByRole('heading', { name: /Classics/ });
    expect(screen.getByText(/abc12345-trace-id/)).toBeInTheDocument();
    // The generated_at <dd> renders as empty — we don't see "null" or
    // "Invalid Date" in the DOM.
    expect(screen.queryByText(/null/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/invalid date/i)).not.toBeInTheDocument();
  });

  it('falls back to the raw ISO when Date parsing returns NaN (fmtDate try branch)', async () => {
    // The other defensive branch: `isNaN(d.getTime()) ? iso : ...`.
    // A malformed ISO string round-trips as the raw input rather than
    // rendering "Invalid Date" — keeps debug-from-the-page possible
    // when a clock drifts or a backend bug emits garbage timestamps.
    getTraceMock.mockResolvedValueOnce(stubTrace({ generated_at: 'not-an-iso-string' }));
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    await screen.findByRole('heading', { name: /Classics/ });
    // The raw garbage string surfaces verbatim in the meta dl.
    expect(screen.getByText('not-an-iso-string')).toBeInTheDocument();
  });

  it('shows seed when the trace has one (rng:on path)', async () => {
    getTraceMock.mockResolvedValueOnce(stubTrace({ rng: 'on', seed: 42 }));
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    // RNG cell renders as `on · seed 42` per the template's conditional.
    expect(await screen.findByText(/on.*seed 42/i)).toBeInTheDocument();
  });

  it('clicking a row header collapses its slot table', async () => {
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    // Pre-collapse: row 1's Mon slot is rendered.
    await screen.findByText('Track Mon-1');

    // Click the Mon row header. Scoping `getByText('Mon')` to `.unit-label`
    // because in a future fixture "Mon" could appear in a track name
    // ("Mondays in May") and unscoped getByText would match multiple. The
    // header element is the parent; walk up via .closest('header').
    const monLabel = screen.getByText('Mon', { selector: '.unit-label' });
    const monHeader = monLabel.closest('header')!;
    await fireEvent.click(monHeader);

    // After collapse, Mon's slots are gone but Tue's remain.
    expect(screen.queryByText('Track Mon-1')).not.toBeInTheDocument();
    expect(screen.getByText('Track Tue-1')).toBeInTheDocument();
  });
});

// ---- disk-export wiring tests ---------------------------------------------

describe('trace viewer — §16.6 disk-export wiring', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('1');
  });

  const seedAllResolved = () => {
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
  };

  it('clicking "Export to disk" POSTs and renders the path + size on success', async () => {
    seedAllResolved();
    exportPlaylistToDiskMock.mockResolvedValueOnce({
      path: '/abs/out/cls/003.xml',
      bytes: 868990,
    });
    render(Page);

    await fireEvent.click(await screen.findByTestId('export-to-disk'));
    expect(exportPlaylistToDiskMock).toHaveBeenCalledWith(7);

    // Status line shows the path and a KB figure derived from bytes.
    // 868990 / 1024 ≈ 849 → "849 KB".
    const status = await screen.findByTestId('disk-export-status');
    expect(status).toHaveTextContent(/\/abs\/out\/cls\/003\.xml/);
    expect(status).toHaveTextContent(/849.*KB/);
  });

  it('renders sub-KB sizes as raw bytes (S1 from §16.6 review)', async () => {
    seedAllResolved();
    // 47 bytes — sub-KB. Math.round(47/1024) === 0 would render "0 KB"
    // which reads like the write didn't happen. formatBytes() picks
    // "47 bytes" instead.
    exportPlaylistToDiskMock.mockResolvedValueOnce({
      path: '/abs/tiny.xml',
      bytes: 47,
    });
    render(Page);
    await fireEvent.click(await screen.findByTestId('export-to-disk'));

    const status = await screen.findByTestId('disk-export-status');
    expect(status).toHaveTextContent(/47 bytes/);
    expect(status).not.toHaveTextContent(/KB/);
  });

  it('on disk-export failure the error renders inline with no nav side-effect', async () => {
    seedAllResolved();
    exportPlaylistToDiskMock.mockRejectedValueOnce(
      new FakeApiError(500, '500: permission denied: /readonly/cls/003.xml'),
    );
    render(Page);

    await fireEvent.click(await screen.findByTestId('export-to-disk'));
    const err = await screen.findByTestId('disk-export-error');
    expect(err).toHaveTextContent(/permission denied/i);
    // Success status not shown (mutually exclusive renders).
    expect(screen.queryByTestId('disk-export-status')).not.toBeInTheDocument();
  });
});
