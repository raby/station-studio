import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

/**
 * Week-vs-week diff page (§16.23). Sibling to
 * `/playlists/[id]/compare` (§16.4.t1) — same diff-table render
 * but station-scoped + week-labelled.
 */

const {
  getStationMock,
  getStationPlaylistsMock,
  getStationWeekDiffMock,
  FakeApiError,
  pageStore,
  setSlug,
  setSearch,
} = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }
  let currentSlug: string | undefined = 'cls';
  let currentSearch = '';
  interface PageValue {
    params: { slug: string | undefined };
    url: URL;
  }
  const buildValue = (): PageValue => ({
    params: { slug: currentSlug },
    url: new URL(
      `http://localhost/stations/${currentSlug ?? ''}/diff${currentSearch}`,
    ),
  });
  const subscribers = new Set<(v: PageValue) => void>();
  const pageStore = {
    subscribe(fn: (v: PageValue) => void) {
      subscribers.add(fn);
      fn(buildValue());
      return () => subscribers.delete(fn);
    },
  };
  const notify = () => {
    for (const fn of subscribers) fn(buildValue());
  };
  return {
    getStationMock: vi.fn(),
    getStationPlaylistsMock: vi.fn(),
    getStationWeekDiffMock: vi.fn(),
    FakeApiError,
    pageStore,
    setSlug: (s: string | undefined) => { currentSlug = s; notify(); },
    setSearch: (s: string) => { currentSearch = s; notify(); },
  };
});

vi.mock('$lib/api', () => ({
  getStation: (...args: unknown[]) => getStationMock(...args),
  getStationPlaylists: (...args: unknown[]) => getStationPlaylistsMock(...args),
  getStationWeekDiff: (...args: unknown[]) => getStationWeekDiffMock(...args),
  ApiError: FakeApiError,
}));

vi.mock('$app/stores', () => ({ page: pageStore }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

import Page from './+page.svelte';

const stubStation = () => ({ id: 1, slug: 'cls', name: 'Classics', template: {} });
const stubPlaylists = () => [
  { id: 1, week: 1, trace_id: 't1', generated_at: 'a', entry_count: 5 },
  { id: 2, week: 2, trace_id: 't2', generated_at: 'b', entry_count: 5 },
  { id: 3, week: 3, trace_id: 't3', generated_at: 'c', entry_count: 5 },
];
const stubDiff = () => ({
  station: 'cls',
  from: {
    week: 2, playlist_id: 2, version_no: 1, trace_id: 't2', generated_at: 'b',
  },
  to: {
    week: 3, playlist_id: 3, version_no: 1, trace_id: 't3', generated_at: 'c',
  },
  summary: { total_slots: 3, changed: 1, unchanged: 2 },
  slots: [
    {
      slot: 1, unit: 'Mon', row: 1, block: 1, changed: false,
      from: {
        slot: 1, unit: 'Mon', row: 1, block: 1,
        source: 'A', strategy: 'window', pool_index: 1,
        track_id: 100, track_name: 'Same', track_artist: 'Same', reason: '',
      },
      to: {
        slot: 1, unit: 'Mon', row: 1, block: 1,
        source: 'A', strategy: 'window', pool_index: 1,
        track_id: 100, track_name: 'Same', track_artist: 'Same', reason: '',
      },
    },
    {
      slot: 2, unit: 'Mon', row: 1, block: 1, changed: true,
      from: {
        slot: 2, unit: 'Mon', row: 1, block: 1,
        source: 'A', strategy: 'window', pool_index: 5,
        track_id: 200, track_name: 'Old Track', track_artist: 'Old', reason: '',
      },
      to: {
        slot: 2, unit: 'Mon', row: 1, block: 1,
        source: 'A', strategy: 'window', pool_index: 8,
        track_id: 201, track_name: 'New Track', track_artist: 'New', reason: '',
      },
    },
    {
      slot: 3, unit: 'Mon', row: 1, block: 1, changed: false,
      from: {
        slot: 3, unit: 'Mon', row: 1, block: 1,
        source: 'A', strategy: 'window', pool_index: 2,
        track_id: 300, track_name: 'Same', track_artist: null, reason: '',
      },
      to: {
        slot: 3, unit: 'Mon', row: 1, block: 1,
        source: 'A', strategy: 'window', pool_index: 2,
        track_id: 300, track_name: 'Same', track_artist: null, reason: '',
      },
    },
  ],
});

describe('§16.23 week-vs-week diff page', () => {
  beforeEach(() => {
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationWeekDiffMock.mockReset();
    setSlug('cls');
    setSearch('?from=2&to=3');
  });

  it('renders the picker strip + diff table with changed rows by default', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(stubPlaylists());
    getStationWeekDiffMock.mockResolvedValueOnce(stubDiff());

    render(Page);
    await screen.findByText('New Track');

    expect(screen.getByTestId('picker-strip')).toBeInTheDocument();
    expect((screen.getByTestId('picker-from') as HTMLSelectElement).value).toBe('2');
    expect((screen.getByTestId('picker-to') as HTMLSelectElement).value).toBe('3');
    // Template whitespace splits the text node; normalize before
    // asserting the human-readable phrase.
    const summaryText = (
      screen.getByTestId('summary').textContent ?? ''
    ).replace(/\s+/g, ' ').trim();
    expect(summaryText).toMatch(/1 of 3 slots changed/);

    // Default hides unchanged → 1 row.
    expect(screen.getAllByTestId('diff-row')).toHaveLength(1);
  });

  it('shows all rows (including unchanged) when "Show unchanged" is on', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(stubPlaylists());
    getStationWeekDiffMock.mockResolvedValueOnce(stubDiff());

    render(Page);
    await screen.findByText('New Track');

    const toggle = screen.getByTestId('show-all-toggle').querySelector(
      'input',
    ) as HTMLInputElement;
    await act(async () => {
      toggle.checked = true;
      await fireEvent.change(toggle);
    });
    expect(screen.getAllByTestId('diff-row')).toHaveLength(3);
  });

  it('renders an inline error when the station has < 2 playlists', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([stubPlaylists()[0]]);
    setSearch('');

    render(Page);
    expect(
      await screen.findByText(/Compare requires at least two generated weeks/i),
    ).toBeInTheDocument();
  });

  it('surfaces the backend ApiError message on 404 from /diff', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(stubPlaylists());
    getStationWeekDiffMock.mockRejectedValueOnce(
      new FakeApiError(404, 'no playlist for week 99 (to) in station cls'),
    );
    setSearch('?from=1&to=99');

    render(Page);
    expect(
      await screen.findByText(/no playlist for week 99/i),
    ).toBeInTheDocument();
  });
});
