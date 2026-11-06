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
  getStationPlaylistsMock,
  getVersionsMock,
  exportPlaylistToDiskMock,
  stationServerSettingsMock,
  sendToStationServerMock,
  getStationSidecarMock,
  startPreviewJobMock,
  watchJobMock,
  applyBatchMock,
  startGenerateJobMock,
  gotoMock,
  FakeApiError,
  pageStore,
  setId,
  setSearch,
  getWeekChangesMock,
  addWeekChangeMock,
  undoWeekChangeMock,
  searchStationSongsMock,
} = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }
  // Page store carrying the route param (`id`) AND the URL (so
  // §16.4's `$page.url.searchParams.get('v')` resolves). Tests swap
  // via setId() / setSearch().
  let currentId: string | undefined = '1';
  let currentSearch = '';
  interface PageValue {
    params: { id: string | undefined };
    url: URL;
  }
  const buildValue = (): PageValue => ({
    params: { id: currentId },
    url: new URL(`http://localhost/playlists/${currentId ?? ''}${currentSearch}`),
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
  const setId = (s: string | undefined) => { currentId = s; notify(); };
  const setSearch = (s: string) => { currentSearch = s; notify(); };
  return {
    getPlaylistMock: vi.fn(),
    // Rejects by default: no Songs view, which is how most tests see the page.
    getStationSidecarMock: vi.fn(
      async (..._args: unknown[]): Promise<unknown> => { throw new Error('no sidecar'); },
    ),
    // Editing a song's rule (staged-edit session + regenerate).
    startPreviewJobMock: vi.fn(async (..._args: unknown[]): Promise<unknown> => ({ job_id: 'pv', status: 'running' })),
    watchJobMock: vi.fn((..._args: unknown[]): AsyncIterable<unknown> => (async function* () {})()),
    applyBatchMock: vi.fn(async (..._args: unknown[]): Promise<unknown> => ({})),
    startGenerateJobMock: vi.fn(async (..._args: unknown[]): Promise<unknown> => ({ job_id: 'gen', status: 'running' })),
    getTraceMock: vi.fn(),
    getStationMock: vi.fn(),
    getStationPlaylistsMock: vi.fn(),
    getVersionsMock: vi.fn(),
    exportPlaylistToDiskMock: vi.fn(),
    // No Station Server set up unless a test says so (mockResolvedValueOnce).
    stationServerSettingsMock: vi.fn(async (..._args: unknown[]) => ({ address: '', has_code: false, stations: {} })),
    sendToStationServerMock: vi.fn(),
    gotoMock: vi.fn(),
    FakeApiError,
    pageStore,
    setId,
    setSearch,
    // Just-this-week changes: off (as an older server) unless a test
    // says otherwise.
    getWeekChangesMock: vi.fn(async (..._args: unknown[]): Promise<unknown> => { throw new Error('no changes'); }),
    addWeekChangeMock: vi.fn(async (..._args: unknown[]): Promise<unknown> => ({})),
    undoWeekChangeMock: vi.fn(async (..._args: unknown[]): Promise<unknown> => ({})),
    searchStationSongsMock: vi.fn(async (..._args: unknown[]): Promise<unknown> => ({ songs: [], more: false })),
  };
});

vi.mock('$lib/api', async () => ({
  // The real error classes and type guards (the staged-edit session uses
  // BlockPatchConflict, SidecarBatchFailure, isPreviewResult); every
  // network call below is a mock.
  ...(await vi.importActual<typeof import('$lib/api')>('$lib/api')),
  startPreviewJob: (...args: unknown[]) => startPreviewJobMock(...args),
  watchJob: (...args: unknown[]) => watchJobMock(...args),
  cancelJob: async () => undefined,
  applyBatch: (...args: unknown[]) => applyBatchMock(...args),
  startGenerateJob: (...args: unknown[]) => startGenerateJobMock(...args),
  getPlaylist: (...args: unknown[]) => getPlaylistMock(...args),
  getTrace: (...args: unknown[]) => getTraceMock(...args),
  getStation: (...args: unknown[]) => getStationMock(...args),
  getStationPlaylists: (...args: unknown[]) => getStationPlaylistsMock(...args),
  getVersions: (...args: unknown[]) => getVersionsMock(...args),
  exportPlaylistToDisk: (...args: unknown[]) => exportPlaylistToDiskMock(...args),
  getStationServerSettings: (...args: unknown[]) => stationServerSettingsMock(...args),
  getStationSidecar: (...args: unknown[]) => getStationSidecarMock(...args),
  sendToStationServer: (...args: unknown[]) => sendToStationServerMock(...args),
  getWeekChanges: (...args: unknown[]) => getWeekChangesMock(...args),
  addWeekChange: (...args: unknown[]) => addWeekChangeMock(...args),
  undoWeekChange: (...args: unknown[]) => undoWeekChangeMock(...args),
  searchStationSongs: (...args: unknown[]) => searchStationSongsMock(...args),
  errorText: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  ApiError: FakeApiError,
}));

// The app's confirm dialog (its host is in the layout, not mounted here).
const { confirmDialogMock } = vi.hoisted(() => ({
  confirmDialogMock: vi.fn(async (_o: { title: string; body: string }) => true),
}));
vi.mock('$lib/confirm', () => ({ confirmDialog: confirmDialogMock }));

vi.mock('$app/stores', () => ({
  page: pageStore,
}));

vi.mock('$app/navigation', () => ({
  goto: (...args: unknown[]) => gotoMock(...args),
}));

import Page from './+page.svelte';
import { JobStartConflict, SidecarBatchFailure } from '$lib/api';

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

/**
 * Convenience: seeds the three fetches with the default stubs and
 * renders the page. Used by the populated-render tests so each
 * focused assertion can stand alone with one line of setup.
 */
function renderPopulated() {
  getTraceMock.mockResolvedValueOnce(stubTrace());
  getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
  getStationMock.mockResolvedValueOnce(stubStation());
  return render(Page);
}

// ---- render-branch tests --------------------------------------------------

describe('trace viewer +page.svelte — render branches', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    // Default: an empty playlist list. The §16.9 nav tests override with
    // multi-week fixtures via mockResolvedValueOnce. Without this default,
    // tests that don't seed the mock would resolve `undefined`, causing
    // the page's `stationPlaylists.length` reactive to crash.
    getStationPlaylistsMock.mockResolvedValue([]);
    // §16.4 — default single-version response. Picker tests override
    // with `mockResolvedValueOnce` for multi-version fixtures.
    getVersionsMock.mockReset();
    getVersionsMock.mockResolvedValue({
      playlist_id: 1, current_v: 1,
      versions: [{
        version_no: 1, trace_id: 'abc12345-trace-id',
        generated_at: '2026-05-04T12:00:00Z', seed: null,
      }],
    });
    gotoMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('1');
    setSearch('');
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

  it('getStationPlaylists failure degrades gracefully (review S2)', async () => {
    // Non-essential fetch — the trace itself loaded fine, but the
    // siblings list (which only drives the §16.9 nav strip) flaked.
    // Pre-S2 the catch killed the whole page render. Post-S2 the
    // page still shows the trace; the nav strip is suppressed because
    // siblings.prev / .next are both null.
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockRejectedValueOnce(
      new FakeApiError(500, 'db locked'),
    );
    render(Page);

    // Trace renders normally — heading + first slot visible.
    await screen.findByRole('heading', { name: /Classics/ });
    expect(screen.getByText('Track Mon-1')).toBeInTheDocument();

    // No error banner — the failure was swallowed by the inner catch.
    expect(screen.queryByText(/db locked/i)).not.toBeInTheDocument();

    // Nav strip suppressed — single playlist (the current one isn't even
    // in the empty list) means siblings = {prev: null, next: null}, and
    // the {#if siblings.prev || siblings.next} gate hides the whole nav.
    expect(screen.queryByTestId('week-nav')).not.toBeInTheDocument();
  });

  // ---- populated-render assertions, split per concern (review S2) -------
  // The previous "renders header + meta + slot rows grouped by row" test
  // asserted ~10 things in one block. Splitting per concern means a
  // failure tells you "the rng cell stopped rendering" rather than "this
  // big test broke" — easier diagnosis at the cost of a few extra mounts.

  it('populated: renders the station heading and zero-padded week', async () => {
    renderPopulated();
    await screen.findByRole('heading', { name: /Classics/ });
    // Week 3 → "003" badge inside the h1.
    expect(screen.getByText('003')).toBeInTheDocument();
  });

  it('populated: renders the breadcrumb with a station-detail link', async () => {
    // Review N4 — the trace viewer's <p class="crumbs"> has three pieces;
    // the station-name link is the navigationally meaningful one. Pinning
    // its href catches breadcrumb wiring regressions.
    renderPopulated();
    const crumb = await screen.findByRole('link', { name: 'Classics' });
    expect(crumb).toHaveAttribute('href', '/stations/cls');
  });

  it('populated: renders trace metadata (trace_id and rng) in the meta dl', async () => {
    renderPopulated();
    await screen.findByRole('heading', { name: /Classics/ });
    expect(screen.getByText(/abc12345-trace-id/)).toBeInTheDocument();
    // rng cell renders just "off" when seed is null.
    expect(screen.getByText(/^off$/)).toBeInTheDocument();
  });

  it('populated: groups slots by row with each row\'s unit label', async () => {
    renderPopulated();
    await screen.findByRole('heading', { name: /Classics/ });
    // groupByRow sorts by row index, so Mon (row 1) precedes Tue (row 2).
    // Scope to .unit-label so a future fixture introducing "Mon" in a track
    // name doesn't trigger the multi-match guard (same lesson as the
    // collapse test below).
    expect(screen.getByText('Mon', { selector: '.unit-label' })).toBeInTheDocument();
    expect(screen.getByText('Tue', { selector: '.unit-label' })).toBeInTheDocument();
  });

  it('populated: renders track name + artist; null artist falls back to empty', async () => {
    renderPopulated();
    await screen.findByRole('heading', { name: /Classics/ });
    // All three slots' track names appear (two Mon + one Tue).
    expect(screen.getByText('Track Mon-1')).toBeInTheDocument();
    expect(screen.getByText('Track Mon-2')).toBeInTheDocument();
    expect(screen.getByText('Track Tue-1')).toBeInTheDocument();
    // Mon-1's artist is set; Mon-2's is null and renders as empty
    // (no literal "null" string in the DOM).
    expect(screen.getByText('Artist 1')).toBeInTheDocument();
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
    getStationPlaylistsMock.mockReset();
    // Default: an empty playlist list. The §16.9 nav tests override with
    // multi-week fixtures via mockResolvedValueOnce. Without this default,
    // tests that don't seed the mock would resolve `undefined`, causing
    // the page's `stationPlaylists.length` reactive to crash.
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockReset();
    getVersionsMock.mockResolvedValue({
      playlist_id: 1, current_v: 1,
      versions: [{
        version_no: 1, trace_id: 'abc12345-trace-id',
        generated_at: '2026-05-04T12:00:00Z', seed: null,
      }],
    });
    gotoMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('1');
    setSearch('');
  });

  const seedAllResolved = () => {
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
  };

  it('clicking "Export to disk" POSTs and renders "Wrote: <path> (<size>)" on success', async () => {
    seedAllResolved();
    exportPlaylistToDiskMock.mockResolvedValueOnce({
      path: '/abs/out/cls/003.xml',
      bytes: 868990,
    });
    render(Page);

    await fireEvent.click(await screen.findByTestId('export-to-disk'));
    // §16.4.t2 — call signature is now (id, version). null = current.
    // §16.4.t6 signature: (id, version, versioned). archiveDiskExports defaults
// off; current-version path passes (7, null, false).
expect(exportPlaylistToDiskMock).toHaveBeenCalledWith(7, null, false);

    // §16.5.t2 c1-review R1+R3 — banner reads
    // "Wrote: /abs/out/cls/003.xml (849 KB)". 868990 / 1024 ≈ 849.
    const status = await screen.findByTestId('disk-export-status');
    expect(status).toHaveTextContent(/\/abs\/out\/cls\/003\.xml/);
    expect(status).toHaveTextContent(/849.*KB/);
    // R3 — assert the "Wrote:" prefix is present (the §16.5.t2
    // migration's headline copy change; previously only the path
    // + KB regex was checked, which would pass even if the prefix
    // were dropped to the 'Success:' default).
    expect(status.textContent?.trim()).toMatch(/^Wrote: /);
    // R1 — guard against the "Wrote: Wrote to ..." double-prefix
    // bug caught in live verification. If a refactor restores
    // `diskExportStatus = \`Wrote to ${path}\`` in +page.svelte
    // and forgets to drop the `prefix="Wrote"` prop, the rendered
    // banner reads "Wrote: Wrote to /abs/..." — the path regex
    // still passes, but the SR + visual UX is broken.
    expect(status.textContent).not.toMatch(/Wrote:\s*Wrote/);
  });

  it("Send to Station Server is hidden for a station that doesn't send there", async () => {
    seedAllResolved();
    render(Page);
    await screen.findByTestId('export-to-disk');
    expect(screen.queryByTestId('send-to-station-server')).toBeNull();
  });

  it('sends the week to Station Server and shows its answer', async () => {
    stationServerSettingsMock.mockResolvedValueOnce({
      address: '10.0.0.5', has_code: true, stations: { cls: { station: 'abc', auto: false } },
    });
    sendToStationServerMock.mockResolvedValueOnce({
      station: 'Rebirth Classics', week: '003.xml', replaced: false, tracks: 410, missing_count: 2,
      missing: [{ artist: 'Madness', title: 'One Step Beyond', location: '/x.mp3' }, { artist: null, title: 'Untitled', location: '/y.mp3' }],
      when: 'later', goes_on: null,
    });
    seedAllResolved();
    render(Page);
    await fireEvent.click(await screen.findByTestId('send-to-station-server'));
    expect(sendToStationServerMock).toHaveBeenCalledWith(7);
    const status = await screen.findByTestId('station-server-sent');
    expect(status).toHaveTextContent(
      "Sent: Rebirth Classics, week 3: 410 tracks found on the station's computer, 2 not found (Madness – One Step Beyond; Untitled). It waits for its week.",
    );
  });

  it('Export to disk shows when the week also went to Station Server, or why not', async () => {
    seedAllResolved();
    exportPlaylistToDiskMock.mockResolvedValueOnce({
      path: '/abs/out/cls/003.xml', bytes: 2048, station_server_error: "Couldn't reach Station Server at 10.0.0.5",
    });
    render(Page);
    await fireEvent.click(await screen.findByTestId('export-to-disk'));
    expect(await screen.findByTestId('station-server-error')).toHaveTextContent("Couldn't reach Station Server");
    expect(await screen.findByTestId('disk-export-status')).toHaveTextContent('003.xml');
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

// ---- §16.8 search / filter ----------------------------------------------

describe('trace viewer — §16.8 search / filter', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    // Default: an empty playlist list. The §16.9 nav tests override with
    // multi-week fixtures via mockResolvedValueOnce. Without this default,
    // tests that don't seed the mock would resolve `undefined`, causing
    // the page's `stationPlaylists.length` reactive to crash.
    getStationPlaylistsMock.mockResolvedValue([]);
    exportPlaylistToDiskMock.mockReset();
    setId('1');
  });

  /**
   * Render with the default fixture: 3 slots across 2 row groups.
   *   Mon: Track Mon-1 (Artist 1, source A, window) + Track Mon-2 (null, A, window)
   *   Tue: Track Tue-1 (Artist 2, source B, window)
   * Total = 3, distinct sources = ['A', 'B'], distinct strategies = ['window'].
   */
  function renderForFilter() {
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    return render(Page);
  }

  it('renders the filter bar with source / strategy options derived from trace', async () => {
    renderForFilter();
    await screen.findByRole('heading', { name: /Classics/ });

    // The bar is present.
    expect(screen.getByTestId('filter-bar')).toBeInTheDocument();

    // Source dropdown carries one "All sources" + one option per distinct
    // source in the trace. Order is sorted (so 'A' before 'B').
    const sourceSelect = screen.getByTestId('filter-source') as HTMLSelectElement;
    const sourceValues = Array.from(sourceSelect.options).map((o) => o.value);
    expect(sourceValues).toEqual(['', 'A', 'B']);

    const strategySelect = screen.getByTestId('filter-strategy') as HTMLSelectElement;
    const strategyValues = Array.from(strategySelect.options).map((o) => o.value);
    expect(strategyValues).toEqual(['', 'window']);
  });

  it('counter shows "Showing N of M" with locale-formatted thousands', async () => {
    renderForFilter();
    const counter = await screen.findByTestId('filter-counter');
    // 3 of 3 with no filters active.
    expect(counter).toHaveTextContent(/showing 3 of 3/i);
  });

  it('search filter narrows to slots whose track name matches', async () => {
    renderForFilter();
    await screen.findByText('Track Mon-1');

    const search = screen.getByTestId('filter-search') as HTMLInputElement;
    await fireEvent.input(search, { target: { value: 'Mon-1' } });

    // Mon-1 visible; Mon-2 + Tue-1 hidden.
    expect(screen.getByText('Track Mon-1')).toBeInTheDocument();
    expect(screen.queryByText('Track Mon-2')).not.toBeInTheDocument();
    expect(screen.queryByText('Track Tue-1')).not.toBeInTheDocument();
    // Counter updates: 1 of 3.
    expect(screen.getByTestId('filter-counter')).toHaveTextContent(/showing 1 of 3/i);
  });

  it('search filter also matches against artist (case-insensitive)', async () => {
    renderForFilter();
    await screen.findByText('Track Tue-1');

    // 'Artist 2' belongs only to Tue-1; uppercase to confirm case-insensitive.
    const search = screen.getByTestId('filter-search') as HTMLInputElement;
    await fireEvent.input(search, { target: { value: 'ARTIST 2' } });

    expect(screen.getByText('Track Tue-1')).toBeInTheDocument();
    expect(screen.queryByText('Track Mon-1')).not.toBeInTheDocument();
  });

  it('source filter narrows to slots from the chosen pool', async () => {
    renderForFilter();
    await screen.findByText('Track Mon-1');

    const sourceSelect = screen.getByTestId('filter-source') as HTMLSelectElement;
    await fireEvent.change(sourceSelect, { target: { value: 'B' } });

    // Only Tue-1 is from source B.
    expect(screen.queryByText('Track Mon-1')).not.toBeInTheDocument();
    expect(screen.queryByText('Track Mon-2')).not.toBeInTheDocument();
    expect(screen.getByText('Track Tue-1')).toBeInTheDocument();
  });

  it('combined filters AND together (search + source narrows further)', async () => {
    renderForFilter();
    await screen.findByText('Track Mon-1');

    // Source A leaves Mon-1 + Mon-2; search 'Mon-2' narrows to one row.
    const sourceSelect = screen.getByTestId('filter-source') as HTMLSelectElement;
    await fireEvent.change(sourceSelect, { target: { value: 'A' } });
    const search = screen.getByTestId('filter-search') as HTMLInputElement;
    await fireEvent.input(search, { target: { value: 'Mon-2' } });

    expect(screen.queryByText('Track Mon-1')).not.toBeInTheDocument();
    expect(screen.getByText('Track Mon-2')).toBeInTheDocument();
    expect(screen.queryByText('Track Tue-1')).not.toBeInTheDocument();
  });

  it('row sections with 0 matching slots are hidden entirely (D8)', async () => {
    renderForFilter();
    await screen.findByText('Mon', { selector: '.unit-label' });

    // Filter to source B: only Tue-1 matches → Mon row should disappear.
    const sourceSelect = screen.getByTestId('filter-source') as HTMLSelectElement;
    await fireEvent.change(sourceSelect, { target: { value: 'B' } });

    // Mon row header gone; Tue still there.
    expect(
      screen.queryByText('Mon', { selector: '.unit-label' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Tue', { selector: '.unit-label' })).toBeInTheDocument();
  });

  it('shows the empty-state copy when filters match nothing', async () => {
    renderForFilter();
    await screen.findByText('Track Mon-1');

    const search = screen.getByTestId('filter-search') as HTMLInputElement;
    await fireEvent.input(search, { target: { value: 'no such track' } });

    expect(await screen.findByTestId('filter-empty')).toHaveTextContent(
      /no slots match/i,
    );
    expect(screen.getByTestId('filter-counter')).toHaveTextContent(/showing 0 of 3/i);
  });

  it('clear button resets all three filter states atomically', async () => {
    renderForFilter();
    await screen.findByText('Track Mon-1');

    // Set all three filters.
    const search = screen.getByTestId('filter-search') as HTMLInputElement;
    const sourceSelect = screen.getByTestId('filter-source') as HTMLSelectElement;
    const strategySelect = screen.getByTestId('filter-strategy') as HTMLSelectElement;
    await fireEvent.input(search, { target: { value: 'love' } });
    await fireEvent.change(sourceSelect, { target: { value: 'A' } });
    await fireEvent.change(strategySelect, { target: { value: 'window' } });

    // Clear button should now be visible (filters active).
    const clearBtn = screen.getByTestId('filter-clear');
    await fireEvent.click(clearBtn);

    // All inputs back to empty.
    expect(search.value).toBe('');
    expect(sourceSelect.value).toBe('');
    expect(strategySelect.value).toBe('');
    // Counter back to full.
    expect(screen.getByTestId('filter-counter')).toHaveTextContent(/showing 3 of 3/i);
  });

  it('clear button hidden when no filters active', async () => {
    renderForFilter();
    await screen.findByText('Track Mon-1');
    expect(screen.queryByTestId('filter-clear')).not.toBeInTheDocument();
  });

  it('strategy filter narrows correctly (review S1)', async () => {
    // The default fixture has 3 slots all with strategy='window' so
    // setting strategyFilter='window' wouldn't narrow anything. Use a
    // fixture variant with mixed strategies so the predicate's strategy
    // gate has discriminating power. Setting filter='pick' should hide
    // the window slots; counter goes from 3 to 1.
    getTraceMock.mockResolvedValueOnce(
      stubTrace({
        slots: [
          {
            slot: 1, unit: 'Mon', row: 1, block: 1,
            source: 'A', strategy: 'window', pool_index: 1,
            track_id: 100, track_name: 'Track Window-1',
            track_artist: null, reason: 'window slot',
          },
          {
            slot: 2, unit: 'Mon', row: 1, block: 1,
            source: 'A', strategy: 'pick', pool_index: 2,
            track_id: 101, track_name: 'Track Pick-1',
            track_artist: null, reason: 'pick slot',
          },
          {
            slot: 3, unit: 'Tue', row: 2, block: 1,
            source: 'A', strategy: 'window', pool_index: 1,
            track_id: 102, track_name: 'Track Window-2',
            track_artist: null, reason: 'window slot',
          },
        ],
      }),
    );
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);
    await screen.findByText('Track Window-1');

    // Strategy dropdown now has both options ['', 'pick', 'window'].
    const strategySelect = screen.getByTestId('filter-strategy') as HTMLSelectElement;
    await fireEvent.change(strategySelect, { target: { value: 'pick' } });

    // Only the 'pick' slot survives.
    expect(screen.queryByText('Track Window-1')).not.toBeInTheDocument();
    expect(screen.queryByText('Track Window-2')).not.toBeInTheDocument();
    expect(screen.getByText('Track Pick-1')).toBeInTheDocument();
    expect(screen.getByTestId('filter-counter')).toHaveTextContent(/showing 1 of 3/i);
  });

  it('combined filters with no match show the empty state (review N4)', async () => {
    // The empty-state test above uses search-only. This one combines
    // source + search where neither filter is empty individually but
    // their AND yields zero. Catches a hypothetical regression that
    // OR'd filters instead of AND'ing them — single-filter empty-state
    // would still pass, only the combined-empty case would surface it.
    renderForFilter();
    await screen.findByText('Track Mon-1');

    // Source A keeps Mon-1 + Mon-2 only. Search 'Tue-1' would normally
    // match the Tue slot — but Tue is from source B, not A. AND = 0.
    const sourceSelect = screen.getByTestId('filter-source') as HTMLSelectElement;
    await fireEvent.change(sourceSelect, { target: { value: 'A' } });
    const search = screen.getByTestId('filter-search') as HTMLInputElement;
    await fireEvent.input(search, { target: { value: 'Tue-1' } });

    expect(screen.getByTestId('filter-empty')).toBeInTheDocument();
    expect(screen.getByTestId('filter-counter')).toHaveTextContent(/showing 0 of 3/i);
  });
});

// ---- §14.23 excluded-candidates disclosure ------------------------------

describe('trace viewer — §14.23 excluded-candidates disclosure', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    // §16.4 — default single-version response; multi-version picker
    // tests override via mockResolvedValueOnce.
    getVersionsMock.mockReset();
    getVersionsMock.mockResolvedValue({
      playlist_id: 1, current_v: 1,
      versions: [{
        version_no: 1, trace_id: 'abc12345-trace-id',
        generated_at: '2026-05-04T12:00:00Z', seed: null,
      }],
    });
    gotoMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('1');
    setSearch('');
  });

  /**
   * Trace where slot 2 has two rejected candidates. Mirrors what a
   * future `weighted` strategy will emit: pool indices considered, the
   * track / artist that was rejected, and the per-candidate reason.
   */
  const traceWithExcluded = () =>
    stubTrace({
      slots: [
        {
          slot: 1, unit: 'Mon', row: 1, block: 1,
          source: 'A', strategy: 'window', pool_index: 1,
          track_id: 100, track_name: 'Track Mon-1',
          track_artist: 'Artist 1', reason: 'window slot 1',
        },
        {
          slot: 2, unit: 'Mon', row: 1, block: 1,
          source: 'A', strategy: 'weighted', pool_index: 5,
          track_id: 105, track_name: 'Picked Track',
          track_artist: 'Picked Artist', reason: 'highest weight',
          excluded: [
            {
              pool_index: 3, track_id: 103, track_name: 'Rejected Alpha',
              track_artist: 'Alpha Artist',
              reason: 'artist already in dedup window',
            },
            {
              pool_index: 4, track_id: 104, track_name: 'Rejected Beta',
              track_artist: 'Beta Artist',
              reason: 'recently used in week 2',
            },
          ],
        },
      ],
    });

  it('renders the "Why not these? (N)" toggle only when excluded list is non-empty', async () => {
    getTraceMock.mockResolvedValueOnce(traceWithExcluded());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    await screen.findByText('Picked Track');
    // Slot 2 has two excluded — one toggle. Slot 1 has none — no toggle.
    const toggles = screen.getAllByTestId('excluded-toggle');
    expect(toggles).toHaveLength(1);
    // §16.17 c2 relabel: "Why not these? (2)".
    expect(toggles[0]).toHaveTextContent(/why not these\? \(2\)/i);
  });

  it('clicking the toggle reveals the per-candidate reject table', async () => {
    getTraceMock.mockResolvedValueOnce(traceWithExcluded());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    const toggle = await screen.findByTestId('excluded-toggle');
    // Sub-row not yet rendered (default collapsed).
    expect(screen.queryByTestId('excluded-row')).not.toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    await fireEvent.click(toggle);

    // Sub-row appears with both rejected entries + their reasons.
    expect(screen.getByTestId('excluded-row')).toBeInTheDocument();
    expect(screen.getByText('Rejected Alpha')).toBeInTheDocument();
    expect(screen.getByText('Rejected Beta')).toBeInTheDocument();
    expect(
      screen.getByText(/artist already in dedup window/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/recently used in week 2/i)).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });

  it('legacy trace without excluded field renders without crashing or toggles', async () => {
    // §14.23 backwards-compat: traces persisted before this release lack
    // the `excluded` field on slots. The viewer treats missing as empty —
    // no toggle, no sub-row, no console warning.
    getTraceMock.mockResolvedValueOnce(stubTrace());  // default fixture: no `excluded`
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    await screen.findByText('Track Mon-1');
    expect(screen.queryByTestId('excluded-toggle')).not.toBeInTheDocument();
    expect(screen.queryByTestId('excluded-row')).not.toBeInTheDocument();
  });
});


// ---- §16.17 c3 — expand-all / collapse-all rejections -------------------

describe('trace viewer — §16.17 expand-all rejections', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    // §16.4 — default single-version response; multi-version picker
    // tests override via mockResolvedValueOnce.
    getVersionsMock.mockReset();
    getVersionsMock.mockResolvedValue({
      playlist_id: 1, current_v: 1,
      versions: [{
        version_no: 1, trace_id: 'abc12345-trace-id',
        generated_at: '2026-05-04T12:00:00Z', seed: null,
      }],
    });
    gotoMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('1');
    setSearch('');
  });

  /** Two slots with rejections so "all" is meaningful. */
  const traceWithTwoExcludedSlots = () =>
    stubTrace({
      slots: [
        {
          slot: 1, unit: 'Mon', row: 1, block: 1,
          source: 'A', strategy: 'weighted', pool_index: 5,
          track_id: 105, track_name: 'Picked One',
          track_artist: 'Artist One', reason: 'highest weight',
          excluded: [
            { pool_index: 3, track_id: 103, track_name: 'Rej A1',
              track_artist: 'A1', reason: 'artist dedup' },
          ],
        },
        {
          slot: 2, unit: 'Mon', row: 1, block: 1,
          source: 'A', strategy: 'weighted', pool_index: 8,
          track_id: 108, track_name: 'Picked Two',
          track_artist: 'Artist Two', reason: 'highest weight',
          excluded: [
            { pool_index: 6, track_id: 106, track_name: 'Rej B1',
              track_artist: 'B1', reason: 'recently used' },
          ],
        },
      ],
    });

  it('shows the expand-all control only when some slot has rejections', async () => {
    // No excluded → no control.
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);
    await screen.findByText('Track Mon-1');
    expect(screen.queryByTestId('toggle-all-excluded')).not.toBeInTheDocument();
  });

  it('expands then collapses every slot\'s rejections at once', async () => {
    getTraceMock.mockResolvedValueOnce(traceWithTwoExcludedSlots());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);

    await screen.findByText('Picked One');
    const btn = screen.getByTestId('toggle-all-excluded');
    // Default: all collapsed → label offers Expand, no sub-rows.
    expect(btn).toHaveTextContent(/expand all rejections/i);
    expect(screen.queryAllByTestId('excluded-row')).toHaveLength(0);

    await fireEvent.click(btn);
    // Both slots' sub-rows now visible; label flips to Collapse.
    expect(screen.getAllByTestId('excluded-row')).toHaveLength(2);
    expect(btn).toHaveTextContent(/collapse all rejections/i);

    await fireEvent.click(btn);
    expect(screen.queryAllByTestId('excluded-row')).toHaveLength(0);
    expect(btn).toHaveTextContent(/expand all rejections/i);
  });

  it('per-trace summary counts the rejection-bearing slots (plural)', async () => {
    getTraceMock.mockResolvedValueOnce(traceWithTwoExcludedSlots());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);
    await screen.findByText('Picked One');
    expect(screen.getByTestId('rejections-summary')).toHaveTextContent(
      /2 slots have rejected candidates/i,
    );
  });

  it('per-trace summary uses the singular for one slot', async () => {
    const oneSlot = stubTrace({
      slots: [
        {
          slot: 1, unit: 'Mon', row: 1, block: 1,
          source: 'A', strategy: 'weighted', pool_index: 5,
          track_id: 105, track_name: 'Only Picked',
          track_artist: 'Solo', reason: 'highest weight',
          excluded: [
            { pool_index: 3, track_id: 103, track_name: 'Rej',
              track_artist: 'R', reason: 'artist dedup' },
          ],
        },
      ],
    });
    getTraceMock.mockResolvedValueOnce(oneSlot);
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);
    await screen.findByText('Only Picked');
    expect(screen.getByTestId('rejections-summary')).toHaveTextContent(
      /1 slot has rejected candidates/i,
    );
  });

  it('per-trace summary is absent when no slot has rejections', async () => {
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    render(Page);
    await screen.findByText('Track Mon-1');
    expect(screen.queryByTestId('rejections-summary')).not.toBeInTheDocument();
  });
});

// ---- §16.9 multi-week prev / next nav -----------------------------------

describe('trace viewer — §16.9 multi-week prev/next nav', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('1');
  });

  /**
   * A station with 3 weeks. Tests pick which one is "current" by setting
   * the route param + matching playlist id. Sorted-by-week is deterministic.
   */
  const threeWeekStation = (): Array<{
    id: number;
    week: number;
    trace_id: string;
    generated_at: string | null;
    entry_count: number;
  }> => [
    { id: 10, week: 1, trace_id: 'wk1', generated_at: null, entry_count: 3 },
    { id: 20, week: 2, trace_id: 'wk2', generated_at: null, entry_count: 3 },
    { id: 30, week: 3, trace_id: 'wk3', generated_at: null, entry_count: 3 },
  ];

  /**
   * Helper: render the viewer at a given playlist id with a list of
   * station playlists. trace.week and playlist.id are aligned.
   */
  function renderForNav(opts: {
    currentId: number;
    currentWeek: number;
    siblings?: ReturnType<typeof threeWeekStation>;
  }) {
    setId(String(opts.currentId));
    getTraceMock.mockResolvedValueOnce(stubTrace({ week: opts.currentWeek }));
    getPlaylistMock.mockResolvedValueOnce({ ...stubPlaylist(), id: opts.currentId, week: opts.currentWeek });
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(opts.siblings ?? threeWeekStation());
    return render(Page);
  }

  it('middle week: both prev and next links render with correct hrefs', async () => {
    // Current = id 20 / week 2; siblings are week 1 (id 10) and week 3 (id 30).
    renderForNav({ currentId: 20, currentWeek: 2 });

    const prev = await screen.findByTestId('week-nav-prev');
    expect(prev).toHaveAttribute('href', '/playlists/10');
    expect(prev).toHaveTextContent(/week 001/i);

    const next = screen.getByTestId('week-nav-next');
    expect(next).toHaveAttribute('href', '/playlists/30');
    expect(next).toHaveTextContent(/week 003/i);
  });

  it('first week: prev shows the disabled placeholder, next is a link', async () => {
    renderForNav({ currentId: 10, currentWeek: 1 });

    // Prev disabled — rendered as <span> not <a>.
    expect(await screen.findByTestId('week-nav-prev-disabled')).toBeInTheDocument();
    expect(screen.queryByTestId('week-nav-prev')).not.toBeInTheDocument();

    // Next still active.
    expect(screen.getByTestId('week-nav-next')).toHaveAttribute(
      'href',
      '/playlists/20',
    );
  });

  it('last week: next shows the disabled placeholder, prev is a link', async () => {
    renderForNav({ currentId: 30, currentWeek: 3 });

    expect(await screen.findByTestId('week-nav-next-disabled')).toBeInTheDocument();
    expect(screen.queryByTestId('week-nav-next')).not.toBeInTheDocument();
    expect(screen.getByTestId('week-nav-prev')).toHaveAttribute(
      'href',
      '/playlists/20',
    );
  });

  it('single-week station: nav strip is suppressed (no prev or next)', async () => {
    // Only one playlist exists for the station — `{#if siblings.prev || siblings.next}`
    // gates the whole nav, so neither link nor disabled placeholder renders.
    renderForNav({
      currentId: 10,
      currentWeek: 1,
      siblings: [
        { id: 10, week: 1, trace_id: 'wk1', generated_at: null, entry_count: 3 },
      ],
    });
    await screen.findByRole('heading', { name: /Classics/ });

    expect(screen.queryByTestId('week-nav')).not.toBeInTheDocument();
    expect(screen.queryByTestId('week-nav-prev')).not.toBeInTheDocument();
    expect(screen.queryByTestId('week-nav-next')).not.toBeInTheDocument();
  });

  it('out-of-order playlist list still finds the right siblings', async () => {
    // The list comes back in some order; sorting happens client-side.
    // Pass an out-of-order list to make sure findIndex hits the right
    // neighbour after sort, not before.
    renderForNav({
      currentId: 20,
      currentWeek: 2,
      siblings: [
        // Week 3 first, then 1, then 2 — wire-order; we expect sorted (1,2,3).
        { id: 30, week: 3, trace_id: 'wk3', generated_at: null, entry_count: 3 },
        { id: 10, week: 1, trace_id: 'wk1', generated_at: null, entry_count: 3 },
        { id: 20, week: 2, trace_id: 'wk2', generated_at: null, entry_count: 3 },
      ],
    });

    expect(await screen.findByTestId('week-nav-prev')).toHaveAttribute(
      'href',
      '/playlists/10',
    );
    expect(screen.getByTestId('week-nav-next')).toHaveAttribute(
      'href',
      '/playlists/30',
    );
  });
});


// ---- §16.4 trace versioning picker ----------------------------------------

describe('trace viewer — §16.4 version picker', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockReset();
    gotoMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('7');
    setSearch('');
  });

  /** Multi-version fixture: 3 generations of the same playlist. */
  const multiVersion = () => ({
    playlist_id: 7,
    current_v: 3,
    versions: [
      {
        version_no: 1, trace_id: 'trace-v1',
        generated_at: '2026-05-01T12:00:00Z', seed: 100,
      },
      {
        version_no: 2, trace_id: 'trace-v2',
        generated_at: '2026-05-02T12:00:00Z', seed: 200,
      },
      {
        version_no: 3, trace_id: 'trace-v3',
        generated_at: '2026-05-03T12:00:00Z', seed: 300,
      },
    ],
  });

  function seedMultiVersion(traceIdOverride?: string) {
    // Default: trace returns v3 (the current). Overrides let tests
    // simulate `?v=2` resolving to the v2 trace.
    getTraceMock.mockResolvedValueOnce(
      stubTrace({ trace_id: traceIdOverride ?? 'trace-v3' }),
    );
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    getVersionsMock.mockResolvedValueOnce(multiVersion());
  }

  it('hides the picker for a single-version playlist', async () => {
    getTraceMock.mockResolvedValueOnce(stubTrace());
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    getVersionsMock.mockResolvedValueOnce({
      playlist_id: 7, current_v: 1,
      versions: [{
        version_no: 1, trace_id: 'abc12345-trace-id',
        generated_at: '2026-05-04T12:00:00Z', seed: null,
      }],
    });
    render(Page);
    await screen.findByText('Track Mon-1');
    expect(screen.queryByTestId('version-picker')).toBeNull();
  });

  it('renders the picker with one option per version (oldest first)', async () => {
    seedMultiVersion();
    render(Page);
    await screen.findByText('Track Mon-1');
    expect(screen.getByTestId('version-picker')).toBeInTheDocument();
    const select = screen.getByTestId('version-select') as HTMLSelectElement;
    const options = Array.from(select.querySelectorAll('option'));
    expect(options.map((o) => o.getAttribute('data-version'))).toEqual(
      ['1', '2', '3'],
    );
    // Latest gets "(current)" suffix.
    expect(options[2].textContent).toContain('(current)');
    expect(options[0].textContent).not.toContain('(current)');
  });

  it('reports the version count in the meta line', async () => {
    seedMultiVersion();
    render(Page);
    await screen.findByText('Track Mon-1');
    expect(screen.getByTestId('version-meta').textContent).toContain(
      '3 versions',
    );
  });

  it('hydrates the selected option from ?v=N on initial load', async () => {
    setSearch('?v=2');
    // The mocked trace endpoint returns v2's trace.
    seedMultiVersion('trace-v2');
    render(Page);
    await screen.findByText('Track Mon-1');
    const select = screen.getByTestId('version-select') as HTMLSelectElement;
    expect(select.value).toBe('2');
    // The fetch path passed v=2 through.
    const firstCallArgs = getTraceMock.mock.calls[0];
    expect(firstCallArgs[0]).toBe(7);
    expect(firstCallArgs[1]).toBe(2);
  });

  it('default load (no ?v) sends version=null and selects current', async () => {
    seedMultiVersion();
    render(Page);
    await screen.findByText('Track Mon-1');
    const select = screen.getByTestId('version-select') as HTMLSelectElement;
    expect(select.value).toBe('3'); // matches trace-v3
    expect(getTraceMock.mock.calls[0][1]).toBeNull();
  });

  it('change event calls goto(?v=N)', async () => {
    seedMultiVersion();
    render(Page);
    await screen.findByText('Track Mon-1');
    const select = screen.getByTestId('version-select') as HTMLSelectElement;
    await act(async () => {
      select.value = '1';
      await fireEvent.change(select);
    });
    expect(gotoMock).toHaveBeenCalledTimes(1);
    const arg = gotoMock.mock.calls[0][0] as string;
    expect(arg).toContain('v=1');
    // Pathname preserved.
    expect(arg).toContain('/playlists/7');
  });

  it('ignores non-positive or non-integer ?v values', async () => {
    // ?v=0 (out-of-range) + ?v=abc (non-integer) both fall back to
    // current (null) so the picker doesn't address a bogus version.
    setSearch('?v=abc');
    seedMultiVersion();
    render(Page);
    await screen.findByText('Track Mon-1');
    expect(getTraceMock.mock.calls[0][1]).toBeNull();
  });
});


// ---- §16.4.t2 historical-version export wiring ----------------------------

describe('trace viewer — §16.4.t2 historical-version export', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockReset();
    gotoMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('7');
    setSearch('');
  });

  function seedMulti(traceIdOverride?: string) {
    getTraceMock.mockResolvedValueOnce(
      stubTrace({ trace_id: traceIdOverride ?? 'trace-v3' }),
    );
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    getVersionsMock.mockResolvedValueOnce({
      playlist_id: 7,
      current_v: 3,
      versions: [
        { version_no: 1, trace_id: 'trace-v1', generated_at: 'a', seed: 100 },
        { version_no: 2, trace_id: 'trace-v2', generated_at: 'b', seed: 200 },
        { version_no: 3, trace_id: 'trace-v3', generated_at: 'c', seed: 300 },
      ],
    });
  }

  it('current version: download anchor has no ?v= + button passes null', async () => {
    seedMulti();
    render(Page);
    await screen.findByText('Track Mon-1');
    expect(
      (screen.getByTestId('download-xml') as HTMLAnchorElement).getAttribute('href'),
    ).toBe('/api/playlists/7/export');
    // Verify the disk-export click would pass null.
    exportPlaylistToDiskMock.mockResolvedValueOnce({ path: '/x', bytes: 100 });
    await act(async () => {
      (screen.getByTestId('export-to-disk') as HTMLButtonElement).click();
    });
    // §16.4.t6 signature: (id, version, versioned). archiveDiskExports defaults
// off; current-version path passes (7, null, false).
expect(exportPlaylistToDiskMock).toHaveBeenCalledWith(7, null, false);
  });

  it('historical version: download anchor + disk button pass the version', async () => {
    setSearch('?v=1');
    seedMulti('trace-v1');
    render(Page);
    await screen.findByText('Track Mon-1');
    expect(
      (screen.getByTestId('download-xml') as HTMLAnchorElement).getAttribute('href'),
    ).toBe('/api/playlists/7/export?v=1');
    // Label gets the suffix.
    expect(
      screen.getByTestId('download-xml').textContent,
    ).toMatch(/Download iTunes XML \(v1\)/);

    exportPlaylistToDiskMock.mockResolvedValueOnce({
      path: '/abs/out/cls/001-v1.xml', bytes: 100,
    });
    await act(async () => {
      (screen.getByTestId('export-to-disk') as HTMLButtonElement).click();
    });
    expect(exportPlaylistToDiskMock).toHaveBeenCalledWith(7, 1, false);
  });
});


// ---- §16.4.t6 archive-every-version disk-export -------------------------

describe('trace viewer — §16.4.t6 archive disk exports', () => {
  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockReset();
    gotoMock.mockReset();
    exportPlaylistToDiskMock.mockReset();
    setId('7');
    setSearch('');
    try { window.localStorage.clear(); } catch { /* */ }
  });

  function seedMulti() {
    getTraceMock.mockResolvedValueOnce(stubTrace({ trace_id: 'trace-v2' }));
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    getVersionsMock.mockResolvedValueOnce({
      playlist_id: 7,
      current_v: 2,
      versions: [
        { version_no: 1, trace_id: 'trace-v1', generated_at: 'a', seed: 100, tags: [] },
        { version_no: 2, trace_id: 'trace-v2', generated_at: 'b', seed: 200, tags: [] },
      ],
    });
  }

  it('checkbox renders + defaults OFF + reflects current_v in the hint', async () => {
    seedMulti();
    render(Page);
    await screen.findByText('Track Mon-1');
    const cb = screen.getByTestId('archive-disk-exports') as HTMLInputElement;
    expect(cb.checked).toBe(false);
    // The inline hint shows the would-be filename.
    expect(
      screen.getByTestId('archive-disk-exports-label').textContent,
    ).toMatch(/003-v2\.xml/);
  });

  it('hydrates from per-station localStorage on mount', async () => {
    window.localStorage.setItem('archiveDiskExports:cls', 'on');
    seedMulti();
    render(Page);
    await screen.findByText('Track Mon-1');
    const cb = screen.getByTestId('archive-disk-exports') as HTMLInputElement;
    expect(cb.checked).toBe(true);
  });

  it('toggling persists the new value to localStorage', async () => {
    seedMulti();
    render(Page);
    await screen.findByText('Track Mon-1');
    const cb = screen.getByTestId('archive-disk-exports') as HTMLInputElement;
    await act(async () => {
      cb.checked = true;
      await fireEvent.change(cb);
    });
    expect(window.localStorage.getItem('archiveDiskExports:cls')).toBe('on');
    await act(async () => {
      cb.checked = false;
      await fireEvent.change(cb);
    });
    expect(window.localStorage.getItem('archiveDiskExports:cls')).toBe('off');
  });

  it('when ON + current version: passes (id, null, true) to the API', async () => {
    window.localStorage.setItem('archiveDiskExports:cls', 'on');
    seedMulti();
    render(Page);
    await screen.findByText('Track Mon-1');
    exportPlaylistToDiskMock.mockResolvedValueOnce({
      path: '/abs/out/cls/003-v2.xml', bytes: 100,
    });
    await act(async () => {
      (screen.getByTestId('export-to-disk') as HTMLButtonElement).click();
    });
    expect(exportPlaylistToDiskMock).toHaveBeenCalledWith(7, null, true);
  });

  it('when ON + historical version: still passes (id, version, true) — server ignores flag', async () => {
    window.localStorage.setItem('archiveDiskExports:cls', 'on');
    setSearch('?v=1');
    getTraceMock.mockResolvedValueOnce(stubTrace({ trace_id: 'trace-v1' }));
    getPlaylistMock.mockResolvedValueOnce(stubPlaylist());
    getStationMock.mockResolvedValueOnce(stubStation());
    getVersionsMock.mockResolvedValueOnce({
      playlist_id: 7,
      current_v: 2,
      versions: [
        { version_no: 1, trace_id: 'trace-v1', generated_at: 'a', seed: 100, tags: [] },
        { version_no: 2, trace_id: 'trace-v2', generated_at: 'b', seed: 200, tags: [] },
      ],
    });
    render(Page);
    await screen.findByText('Track Mon-1');
    exportPlaylistToDiskMock.mockResolvedValueOnce({
      path: '/abs/out/cls/003-v1.xml', bytes: 100,
    });
    await act(async () => {
      (screen.getByTestId('export-to-disk') as HTMLButtonElement).click();
    });
    expect(exportPlaylistToDiskMock).toHaveBeenCalledWith(7, 1, true);
  });
});


// ---- Songs view: each song with its rule and why ------------------------

describe('trace viewer — Songs view', () => {
  // A two-block row; the stub trace's songs all say block 1, the first.
  const editorData = () => ({
    template: {
      station: 'cls',
      name: 'Classics',
      sources: [
        { name: 'A', colour: '#c0392b', role: 'content' },
        { name: 'B', colour: '#8e44ad', role: 'content' },
      ],
      layout: {
        unit: 'day', unit_labels: [], row_start_offsets: [],
        row_template: [
          { source: 'A', strategy: 'window', size: 2 },
          { source: 'B', strategy: 'pick', size: 1 },
        ],
      },
    },
    sidecar: {
      schema_version: 1,
      sections: [{ id: 'day', name: 'The day', blocks: [
        { kind: 'atomic', row_idx: 0, block_idx: 0 },
        { kind: 'atomic', row_idx: 0, block_idx: 1 },
      ] }],
      macros: [],
      layout_order: ['day'],
    },
    sidecar_error: null,
    updated_at: 'ts-1',
  });

  beforeEach(() => {
    getPlaylistMock.mockReset();
    getTraceMock.mockReset();
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockReset();
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 1, versions: [] });
    stationServerSettingsMock.mockResolvedValue(
      null as unknown as Awaited<ReturnType<typeof stationServerSettingsMock>>, // no Station Server link
    );
    getStationSidecarMock.mockReset();
    getStationSidecarMock.mockRejectedValue(new Error('no sidecar'));
    setId('7');
    setSearch('');
    try { window.localStorage.clear(); } catch { /* */ }
  });

  it('switches to Songs: each song with its rule and the reason it was picked', async () => {
    getStationSidecarMock.mockResolvedValueOnce(editorData());
    renderPopulated();
    await screen.findByText('Track Mon-1');
    expect(screen.queryByTestId('song-list')).toBeNull(); // the table is the default

    await fireEvent.click(screen.getByTestId('week-view-songs'));
    const rows = await screen.findAllByTestId('song-row');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('Track Mon-1');
    expect(screen.getAllByTestId('song-rule')[0]).toHaveTextContent('2 in a row from A');
    expect(screen.getAllByTestId('song-why')[0]).toHaveTextContent('first slot of row 1');
    expect(screen.getAllByTestId('song-section')[0]).toHaveTextContent('The day');
    expect(screen.queryByTestId('filter-bar')).toBeNull(); // the table's filters

    await fireEvent.click(rows[1]);
    expect(rows[1]).toHaveAttribute('aria-pressed', 'true');
    expect(window.localStorage.getItem('weekView')).toBe('songs');
  });

  it('remembers Songs for the next week page, and Table switches back', async () => {
    window.localStorage.setItem('weekView', 'songs');
    getStationSidecarMock.mockResolvedValueOnce(editorData());
    renderPopulated();
    expect(await screen.findAllByTestId('song-row')).toHaveLength(3);
    await fireEvent.click(screen.getByTestId('week-view-table'));
    expect(screen.queryByTestId('song-list')).toBeNull();
    expect(screen.getByTestId('filter-bar')).toBeInTheDocument();
    expect(window.localStorage.getItem('weekView')).toBe('table');
  });

  it('without the station sidecar there is no Songs view, just the table', async () => {
    window.localStorage.setItem('weekView', 'songs');
    getStationSidecarMock.mockRejectedValueOnce(new Error('404'));
    renderPopulated();
    await screen.findByText('Track Mon-1');
    expect(screen.queryByTestId('week-view')).toBeNull();
    expect(screen.queryByTestId('song-list')).toBeNull();
  });
});


// ---- Songs view: click a song, change its rule, save, regenerate ----------

describe('trace viewer — changing a song\'s rule', () => {
  // Same two-block row as the Songs view tests: block 1 (window A) made
  // every song in the stub week.
  const template = (first: Record<string, unknown> = { source: 'A', strategy: 'window', size: 2 }) => ({
    station: 'cls', name: 'Classics',
    sources: [
      { name: 'A', colour: '#c0392b', role: 'content' },
      { name: 'B', colour: '#8e44ad', role: 'content' },
    ],
    layout: {
      unit: 'day', unit_labels: [], row_start_offsets: [],
      row_template: [first, { source: 'B', strategy: 'pick', size: 1 }],
    },
  });
  const sidecar = () => ({
    schema_version: 1,
    sections: [{ id: 'day', name: 'The day', blocks: [
      { kind: 'atomic', row_idx: 0, block_idx: 0 },
      { kind: 'atomic', row_idx: 0, block_idx: 1 },
    ] }],
    macros: [],
    layout_order: ['day'],
  });
  const editorData = () => ({ template: template(), sidecar: sidecar(), sidecar_error: null, updated_at: 'ts-1' });

  /** Preview jobs finish with the stub week (the saved rules), then with
   *  its first song swapped (the edited rules). */
  function previewsSavedThenEdited() {
    let n = 0;
    startPreviewJobMock.mockImplementation(async () => ({ job_id: `pv-${++n}`, status: 'running' }));
    watchJobMock.mockImplementation((id: unknown) => (async function* () {
      const base = stubTrace();
      // The edited rules' preview records the new strategy, as the engine would.
      const edited = base.slots.map((x) => ({ ...x, strategy: 'pick' }));
      const trace = id === 'pv-1' ? base : {
        ...base,
        slots: [{ ...edited[0], track_id: 999, track_name: 'Swapped In' }, ...edited.slice(1)],
      };
      yield { status: 'done', result: { trace, slot_count: trace.slots.length } };
    })());
  }

  beforeEach(() => {
    for (const m of [getPlaylistMock, getTraceMock, getStationMock, getStationPlaylistsMock, getVersionsMock,
      getStationSidecarMock, startPreviewJobMock, watchJobMock, applyBatchMock, startGenerateJobMock]) m.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 1, versions: [] });
    stationServerSettingsMock.mockResolvedValue(
      null as unknown as Awaited<ReturnType<typeof stationServerSettingsMock>>,
    );
    getStationSidecarMock.mockImplementation(async () => editorData());
    getTraceMock.mockImplementation(async () => stubTrace());
    getPlaylistMock.mockImplementation(async () => stubPlaylist());
    getStationMock.mockImplementation(async () => stubStation());
    setId('7');
    setSearch('');
    try { window.localStorage.clear(); } catch { /* */ }
    window.localStorage.setItem('weekView', 'songs');
  });

  it('pick a song, change its rule, see the week with the change, save, regenerate', async () => {
    previewsSavedThenEdited();
    render(Page);
    const rows = await screen.findAllByTestId('song-row');
    await fireEvent.click(rows[0]);

    // The rule panel opens; every song from the same rule is marked.
    await screen.findByTestId('detail-panel');
    expect(rows.filter((r) => r.classList.contains('related'))).toHaveLength(3);

    // Change the rule: window → pick.
    await fireEvent.click(screen.getByTestId('retype-toggle'));
    await fireEvent.change(screen.getByTestId('retype-strategy'), { target: { value: 'pick' } });
    await fireEvent.click(screen.getByTestId('retype-apply'));
    expect(await screen.findByTestId('song-edit-count')).toHaveTextContent('1 change');
    expect(screen.getByTestId('song-edit-count')).toHaveTextContent('for every week');

    // The week re-previews: the edited rules give a different first song.
    const changed = await screen.findByTestId('song-change');
    expect(changed).toHaveTextContent('changed');
    expect(screen.getAllByTestId('song-row')[0]).toHaveTextContent('Swapped In');
    expect(screen.getAllByTestId('song-rule')[0]).toHaveTextContent('Picked from A');

    // Save: one batch, against the version loaded.
    applyBatchMock.mockResolvedValueOnce({
      template: template({ source: 'A', strategy: 'pick', size: 2 }), sidecar: sidecar(), updated_at: 'ts-2',
    });
    await fireEvent.click(screen.getByTestId('song-edit-save'));
    expect(await screen.findByTestId('song-edit-saved')).toHaveTextContent('regenerate it');
    expect(applyBatchMock).toHaveBeenCalledWith(
      'cls',
      [expect.objectContaining({ kind: 'retypeBlock', address: { block_idx: 0 }, newStrategy: 'pick' })],
      'ts-1',
    );

    // Regenerate this week with the saved rules; the page reloads it.
    watchJobMock.mockImplementation(() => (async function* () {
      yield { status: 'done', result: { playlist_id: 7, trace_id: 'new', slot_count: 3 } };
    })());
    const tracesBefore = getTraceMock.mock.calls.length;
    await fireEvent.click(screen.getByTestId('song-edit-regenerate'));
    await vi.waitFor(() => expect(getTraceMock.mock.calls.length).toBeGreaterThan(tracesBefore));
    expect(startGenerateJobMock).toHaveBeenCalledWith('cls', 3); // no seed: the server reuses the week's
    await vi.waitFor(() => expect(screen.queryByTestId('song-edit-bar')).toBeNull());
  });

  it('discard drops the change and shows the week as generated', async () => {
    previewsSavedThenEdited();
    render(Page);
    await fireEvent.click((await screen.findAllByTestId('song-row'))[0]);
    await screen.findByTestId('detail-panel');
    await fireEvent.click(screen.getByTestId('retype-toggle'));
    await fireEvent.change(screen.getByTestId('retype-strategy'), { target: { value: 'pick' } });
    await fireEvent.click(screen.getByTestId('retype-apply'));
    await screen.findByTestId('song-change');

    await fireEvent.click(screen.getByTestId('song-edit-discard'));
    await vi.waitFor(() => expect(screen.queryByTestId('song-edit-count')).toBeNull());
    expect(screen.queryByTestId('song-change')).toBeNull();
    expect(screen.getAllByTestId('song-row')[0]).toHaveTextContent('Track Mon-1');
    expect(applyBatchMock).not.toHaveBeenCalled();
  });

  it('an older version of the week is read-only', async () => {
    setSearch('?v=1');
    getVersionsMock.mockResolvedValue({
      playlist_id: 7, current_v: 2,
      versions: [
        { version_no: 1, trace_id: 'abc12345-trace-id', generated_at: 'a', seed: null, tags: [] },
        { version_no: 2, trace_id: 'newer', generated_at: 'b', seed: null, tags: [] },
      ],
    });
    render(Page);
    expect(await screen.findByTestId('song-edit-older')).toHaveTextContent('older version');
    await fireEvent.click(screen.getAllByTestId('song-row')[0]);
    expect(screen.queryByTestId('detail-panel')).toBeNull();
    expect(startPreviewJobMock).not.toHaveBeenCalled();
  });

  /** Pick the first song and change its rule window → pick. */
  async function changeFirstRule(): Promise<void> {
    await fireEvent.click((await screen.findAllByTestId('song-row'))[0]);
    await screen.findByTestId('detail-panel');
    await fireEvent.click(screen.getByTestId('retype-toggle'));
    await fireEvent.change(screen.getByTestId('retype-strategy'), { target: { value: 'pick' } });
    await fireEvent.click(screen.getByTestId('retype-apply'));
    await screen.findByTestId('song-change');
  }

  it('moving to the next week keeps the changes and previews that week', async () => {
    previewsSavedThenEdited();
    render(Page);
    await changeFirstRule();
    const sidecarLoads = getStationSidecarMock.mock.calls.length;

    getPlaylistMock.mockImplementation(async () => ({ ...stubPlaylist(), id: 8, week: 4 }));
    getTraceMock.mockImplementation(async () => stubTrace({ week: 4 }));
    await act(() => setId('8'));

    await vi.waitFor(() => expect(startPreviewJobMock).toHaveBeenLastCalledWith(
      'cls', expect.anything(), expect.anything(), 4,
    ));
    expect(screen.getByTestId('song-edit-count')).toHaveTextContent('1 change');
    expect(screen.getByTestId('detail-panel')).toBeInTheDocument(); // the picked rule stays open
    // The page loads the station for the new week; the session doesn't reload.
    expect(getStationSidecarMock.mock.calls.length).toBe(sidecarLoads + 1);
  });

  /** Change the first rule and save it. */
  async function changeAndSave(): Promise<void> {
    await changeFirstRule();
    applyBatchMock.mockResolvedValueOnce({
      template: template({ source: 'A', strategy: 'pick', size: 2 }), sidecar: sidecar(), updated_at: 'ts-2',
    });
    await fireEvent.click(screen.getByTestId('song-edit-save'));
    await screen.findByTestId('song-edit-saved');
  }

  const weekSummary = (id: number, week: number) =>
    ({ id, week, trace_id: `t${week}`, generated_at: 'x', entry_count: 3 });

  it('after saving, offers to regenerate this week and the later ones, in order', async () => {
    previewsSavedThenEdited();
    getStationPlaylistsMock.mockResolvedValue([2, 3, 4, 5].map((w) => weekSummary(w + 4, w)));
    render(Page);
    await changeAndSave();
    expect(screen.getByTestId('song-edit-saved'))
      .toHaveTextContent('Week 3 and the 2 weeks after it (weeks 4–5) were made with the old rules');

    const order: number[] = [];
    startGenerateJobMock.mockImplementation(async (_s: unknown, week: unknown) => {
      order.push(week as number);
      return { job_id: `gen-${week}`, status: 'running' };
    });
    watchJobMock.mockImplementation(() => (async function* () {
      yield { status: 'done', result: { playlist_id: 7, trace_id: 'new', slot_count: 3 } };
    })());
    const tracesBefore = getTraceMock.mock.calls.length;
    await fireEvent.click(screen.getByTestId('song-edit-regenerate-later'));
    await vi.waitFor(() => expect(getTraceMock.mock.calls.length).toBeGreaterThan(tracesBefore));
    expect(order).toEqual([3, 4, 5]);
    // No seed sent: the server reuses each week's own.
    expect(startGenerateJobMock.mock.calls.map((c) => c.length)).toEqual([2, 2, 2]);
    await vi.waitFor(() => expect(screen.queryByTestId('song-edit-bar')).toBeNull());
    expect(await screen.findByTestId('song-regenerated')).toHaveTextContent('Regenerated weeks 3–5 with the saved rules.');
  });

  it('stops at a week that fails and says which', async () => {
    previewsSavedThenEdited();
    getStationPlaylistsMock.mockResolvedValue([3, 4, 5].map((w) => weekSummary(w + 4, w)));
    render(Page);
    await changeAndSave();

    startGenerateJobMock.mockImplementation(async (_s: unknown, week: unknown) =>
      ({ job_id: `gen-${week}`, status: 'running' }));
    watchJobMock.mockImplementation((id: unknown) => (async function* () {
      if (id === 'gen-4') yield { status: 'error', error: { kind: 'ValueError', message: 'pool A is empty' } };
      else yield { status: 'done', result: { playlist_id: 7, trace_id: 'new', slot_count: 3 } };
    })());
    await fireEvent.click(screen.getByTestId('song-edit-regenerate-later'));
    expect(await screen.findByTestId('song-edit-regenerate-error')).toHaveTextContent('week 4: pool A is empty');
    expect(startGenerateJobMock.mock.calls.map((c) => c[1])).toEqual([3, 4]);
  });

  it("another generate already running for the station: waits for it, then does this week", async () => {
    previewsSavedThenEdited();
    getStationPlaylistsMock.mockResolvedValue([3].map((w) => weekSummary(w + 4, w)));
    render(Page);
    await changeAndSave();

    let tries = 0;
    startGenerateJobMock.mockImplementation(async (_s: unknown, week: unknown) => {
      if (++tries === 1) throw new JobStartConflict('busy', 'other-job');
      return { job_id: `gen-${week}`, status: 'running' };
    });
    const watched: string[] = [];
    watchJobMock.mockImplementation((id: unknown) => (async function* () {
      watched.push(id as string);
      yield { status: 'done', result: { playlist_id: 99, trace_id: 'x', slot_count: 3 } };
    })());
    const tracesBefore = getTraceMock.mock.calls.length;
    await fireEvent.click(screen.getByTestId('song-edit-regenerate'));
    await vi.waitFor(() => expect(getTraceMock.mock.calls.length).toBeGreaterThan(tracesBefore));
    // The other job was waited out, then this week's started and watched.
    expect(watched).toEqual(['other-job', 'gen-3']);
    expect(startGenerateJobMock).toHaveBeenCalledTimes(2);
  });

  it('a later week that can\'t even start says which, and shows the weeks that did regenerate', async () => {
    previewsSavedThenEdited();
    getStationPlaylistsMock.mockResolvedValue([3, 4, 5].map((w) => weekSummary(w + 4, w)));
    render(Page);
    await changeAndSave();
    startGenerateJobMock.mockImplementation(async (_s: unknown, week: unknown) => {
      if (week === 4) throw new Error('pool A has no songs');
      return { job_id: `gen-${week}`, status: 'running' };
    });
    watchJobMock.mockImplementation(() => (async function* () {
      yield { status: 'done', result: { playlist_id: 7, trace_id: 'new', slot_count: 3 } };
    })());
    const tracesBefore = getTraceMock.mock.calls.length;
    await fireEvent.click(screen.getByTestId('song-edit-regenerate-later'));
    expect(await screen.findByTestId('song-edit-regenerate-error')).toHaveTextContent('week 4: pool A has no songs');
    await vi.waitFor(() => expect(getTraceMock.mock.calls.length).toBeGreaterThan(tracesBefore));
    expect(startGenerateJobMock.mock.calls.map((c) => c[1])).toEqual([3, 4]);
  });

  it('with no later weeks there is just Regenerate this week', async () => {
    previewsSavedThenEdited();
    getStationPlaylistsMock.mockResolvedValue([1, 2, 3].map((w) => weekSummary(w + 4, w)));
    render(Page);
    await changeAndSave();
    expect(screen.getByTestId('song-edit-saved')).toHaveTextContent('Week 3 was made with the old rules');
    expect(screen.queryByTestId('song-edit-regenerate-later')).toBeNull();
  });

  it('a search stays while previewing a change; only changed songs on demand', async () => {
    previewsSavedThenEdited();
    render(Page);
    const search = await screen.findByTestId('song-search');
    await fireEvent.input(search, { target: { value: 'Track Mon-1' } });
    expect(screen.getAllByTestId('song-row')).toHaveLength(1);
    expect(screen.queryByTestId('song-only-changed')).toBeNull(); // nothing to compare yet

    await fireEvent.click(screen.getByTestId('song-row'));
    await screen.findByTestId('detail-panel');
    await fireEvent.click(screen.getByTestId('retype-toggle'));
    await fireEvent.change(screen.getByTestId('retype-strategy'), { target: { value: 'pick' } });
    await fireEvent.click(screen.getByTestId('retype-apply'));
    // The first song is swapped out in the preview, so nothing matches
    // now; the search itself is kept.
    expect(await screen.findByTestId('song-search-empty')).toBeInTheDocument();
    expect(screen.getByTestId('song-search')).toHaveValue('Track Mon-1');

    await fireEvent.click(screen.getByTestId('song-search-clear'));
    expect(screen.getAllByTestId('song-row')).toHaveLength(3);
    const only = screen.getByTestId('song-only-changed');
    expect(only.closest('label')).toHaveTextContent('Only changed songs (1)');
    await fireEvent.click(only);
    expect(screen.getAllByTestId('song-row')).toHaveLength(1);
    expect(screen.getByTestId('song-row')).toHaveTextContent('Swapped In');
    expect(screen.getByTestId('song-shown')).toHaveTextContent('Showing 1 of 3');
  });

  it('can change the identical rules in the row too', async () => {
    previewsSavedThenEdited();
    // A third block just like the first.
    const twinTemplate = template();
    twinTemplate.layout.row_template.push({ source: 'A', strategy: 'window', size: 2 });
    const twinSidecar = sidecar();
    twinSidecar.sections[0].blocks.push({ kind: 'atomic', row_idx: 0, block_idx: 2 });
    getStationSidecarMock.mockImplementation(async () =>
      ({ template: twinTemplate, sidecar: twinSidecar, sidecar_error: null, updated_at: 'ts-1' }));
    render(Page);
    await fireEvent.click((await screen.findAllByTestId('song-row'))[0]);
    await screen.findByTestId('detail-panel');
    expect(screen.getByTestId('also-identical')).toHaveTextContent('Also change the 1 other rule just like this one');
    await fireEvent.click(screen.getByTestId('also-identical-input'));
    await fireEvent.click(screen.getByTestId('retype-toggle'));
    await fireEvent.change(screen.getByTestId('retype-strategy'), { target: { value: 'pick' } });
    await fireEvent.click(screen.getByTestId('retype-apply'));
    // One change, to two rules: said once, undone together.
    expect(await screen.findByTestId('song-edit-count')).toHaveTextContent("1 change to 2 of this station's rules");
    expect(screen.getByTestId('song-edit-item'))
      .toHaveTextContent('The day, rule 1: Picked from A, and the 1 rule just like it');
    await fireEvent.click(screen.getByTestId('song-edit-undo'));
    await vi.waitFor(() => expect(screen.queryByTestId('song-edit-count')).toBeNull());
  });

  it('lists each change in plain words with how many songs move, and undoes one', async () => {
    previewsSavedThenEdited();
    render(Page);
    await changeFirstRule();
    expect(screen.getByTestId('song-edit-item')).toHaveTextContent('The day, rule 1: Picked from A');
    expect(await screen.findByTestId('song-edit-moved')).toHaveTextContent('1 of 3 songs in week 3 change.');

    await fireEvent.click(screen.getByTestId('song-edit-undo'));
    await vi.waitFor(() => expect(screen.queryByTestId('song-edit-bar')).toBeNull());
    expect(screen.queryByTestId('song-change')).toBeNull();
    // The bar's gone; focus is on the song picked, not lost.
    await vi.waitFor(() => expect(document.activeElement).toHaveAttribute('aria-pressed', 'true'));
    expect(document.activeElement).toHaveAttribute('data-testid', 'song-row');
    expect(window.localStorage.getItem('stagedEdits:cls')).toBeNull();
  });

  it('switching to an older version shows that version, not the changes', async () => {
    previewsSavedThenEdited();
    getVersionsMock.mockResolvedValue({
      playlist_id: 7, current_v: 2,
      versions: [
        { version_no: 1, trace_id: 'older', generated_at: 'a', seed: null, tags: [] },
        { version_no: 2, trace_id: 'abc12345-trace-id', generated_at: 'b', seed: null, tags: [] },
      ],
    });
    render(Page);
    await changeFirstRule();

    await act(() => setSearch('?v=1'));
    expect(await screen.findByTestId('song-edit-older')).toBeInTheDocument();
    await vi.waitFor(() => expect(screen.queryByTestId('song-change')).toBeNull());
    expect(screen.getAllByTestId('song-row')[0]).toHaveTextContent('Track Mon-1');
    expect(screen.queryByTestId('detail-panel')).toBeNull();
    // The changes are still queued for the station.
    expect(screen.getByTestId('song-edit-count')).toHaveTextContent('1 change');
  });

  it('picking a song with nothing changed shows no edit bar', async () => {
    render(Page);
    await fireEvent.click((await screen.findAllByTestId('song-row'))[0]);
    await screen.findByTestId('detail-panel');
    expect(screen.queryByTestId('song-edit-bar')).toBeNull();
  });

  it("undoing a change a later one builds on asks first: a retype, then that rule's settings as retyped", async () => {
    previewsSavedThenEdited();
    window.localStorage.setItem('stagedEdits:cls', JSON.stringify({
      schemaVersion: 1,
      opLog: [
        { kind: 'retypeBlock', address: { block_idx: 0 }, newStrategy: 'pick', newSource: null, newFields: {} },
        { kind: 'patchBlock', address: { block_idx: 0 }, fields: { allowlist_sources: ['B'] } },
      ],
      updatedAtSnapshot: 'ts-1',
    }));
    confirmDialogMock.mockReset();
    confirmDialogMock.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    render(Page);
    expect(await screen.findAllByTestId('song-edit-item')).toHaveLength(2);

    // Told no: both stay.
    await fireEvent.click(screen.getAllByTestId('song-edit-undo')[0]);
    await vi.waitFor(() => expect(confirmDialogMock).toHaveBeenCalledTimes(1));
    expect(confirmDialogMock.mock.calls[0][0].body)
      .toMatch(/:\n\n• The day, rule 1: Picked from A, only songs also in B$/);
    expect(screen.getAllByTestId('song-edit-item')).toHaveLength(2);

    // Yes: both go.
    await fireEvent.click(screen.getAllByTestId('song-edit-undo')[0]);
    await vi.waitFor(() => expect(screen.queryByTestId('song-edit-bar')).toBeNull());
    expect(window.localStorage.getItem('stagedEdits:cls')).toBeNull();
  });

  it("a failed save's Drop takes the changes built on the failed one too, once told yes", async () => {
    previewsSavedThenEdited();
    window.localStorage.setItem('stagedEdits:cls', JSON.stringify({
      schemaVersion: 1,
      opLog: [
        { kind: 'retypeBlock', address: { block_idx: 0 }, newStrategy: 'pick', newSource: null, newFields: {} },
        { kind: 'patchBlock', address: { block_idx: 0 }, fields: { allowlist_sources: ['B'] } },
      ],
      updatedAtSnapshot: 'ts-1',
    }));
    confirmDialogMock.mockReset();
    confirmDialogMock.mockResolvedValue(true);
    applyBatchMock.mockRejectedValueOnce(new SidecarBatchFailure(400, { error: 'bad type', failed_at_idx: 1, kind: 'retypeBlock' }));
    render(Page);
    await fireEvent.click(await screen.findByTestId('song-edit-save'));
    const drop = await screen.findByTestId('song-edit-drop-failed');
    const calls = applyBatchMock.mock.calls.length;
    await fireEvent.click(drop);
    await vi.waitFor(() => expect(confirmDialogMock).toHaveBeenCalledTimes(1));
    expect(confirmDialogMock.mock.calls[0][0].title).toBe('Drop a later change too?');
    await vi.waitFor(() => expect(window.localStorage.getItem('stagedEdits:cls')).toBeNull());
    // Nothing left to save: the settings were for the type it didn't get.
    expect(applyBatchMock.mock.calls.length).toBe(calls);
  });

  it('changes left from an earlier visit show on arrival, without clicking a song', async () => {
    previewsSavedThenEdited();
    window.localStorage.setItem('stagedEdits:cls', JSON.stringify({
      schemaVersion: 1,
      opLog: [{ kind: 'retypeBlock', address: { block_idx: 0 }, newStrategy: 'pick', newSource: null, newFields: {} }],
      updatedAtSnapshot: 'ts-1',
    }));
    render(Page);
    expect(await screen.findByTestId('song-edit-count')).toHaveTextContent('1 change');
    expect(await screen.findByTestId('song-change')).toHaveTextContent('changed');
  });
});


describe('trace viewer — moving songs', () => {
  // A station of three rules in a list (not a repeating row): Morning
  // has rules 1 and 2, Evening rule 3. The week: a song from each.
  const template = (strategy = 'pick') => ({
    station: 'cls', name: 'Classics',
    sources: [{ name: 'A', colour: '#c0392b', role: 'content' }],
    layout: {
      unit: 'slot', unit_labels: [], row_start_offsets: [],
      flat_slots: [1, 2, 3].map(() => ({ source: 'A', strategy, size: 1 })),
    },
  });
  const sidecar = () => ({
    schema_version: 1,
    sections: [
      { id: 'morning', name: 'Morning', blocks: [{ kind: 'atomic', slot_index: 0 }, { kind: 'atomic', slot_index: 1 }] },
      { id: 'evening', name: 'Evening', blocks: [{ kind: 'atomic', slot_index: 2 }] },
    ],
    macros: [],
    layout_order: ['morning', 'evening'],
  });
  const flatWeek = (order = [1, 2, 3], strategy = 'pick') => stubTrace({
    slots: order.map((n, i) => ({
      slot: i + 1, unit: 'slot', row: 1, block: i + 1, source: 'A', strategy, pool_index: i + 1,
      track_id: 100 + n, track_name: `Song ${n}`, track_artist: 'Artist', reason: 'picked',
    })),
  });
  const queue = () => JSON.parse(window.localStorage.getItem('stagedEdits:cls') ?? '{"opLog":[]}').opLog as unknown[];
  const note = () => screen.queryByTestId('song-move-note')?.textContent ?? '';
  const key = async (el: HTMLElement, k: string) => { await act(async () => { await fireEvent.keyDown(el, { key: k }); }); };
  /** Evening's song up: past the end of Morning (where it plays
   *  already), to before rule 2. */
  async function moveLastUp() {
    const grip = (await screen.findAllByTestId('song-grip'))[2];
    await key(grip, ' ');
    await key(grip, 'ArrowUp');
    await key(grip, 'ArrowUp');
    await key(grip, ' ');
  }

  beforeEach(() => {
    for (const m of [getPlaylistMock, getTraceMock, getStationMock, getStationPlaylistsMock, getVersionsMock,
      getStationSidecarMock, startPreviewJobMock, watchJobMock, applyBatchMock, startGenerateJobMock]) m.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 1, versions: [] });
    stationServerSettingsMock.mockResolvedValue(
      null as unknown as Awaited<ReturnType<typeof stationServerSettingsMock>>,
    );
    getStationSidecarMock.mockImplementation(async () => ({ template: template(), sidecar: sidecar(), sidecar_error: null, updated_at: 'ts-1' }));
    getTraceMock.mockImplementation(async () => flatWeek());
    getPlaylistMock.mockImplementation(async () => stubPlaylist());
    getStationMock.mockImplementation(async () => stubStation());
    // Previews: the saved week, then the week with the move (3 before 2).
    let n = 0;
    startPreviewJobMock.mockImplementation(async () => ({ job_id: `pv-${++n}`, status: 'running' }));
    watchJobMock.mockImplementation((id: unknown) => (async function* () {
      const trace = id === 'pv-1' ? flatWeek() : flatWeek([1, 3, 2]);
      yield { status: 'done', result: { trace, slot_count: 3 } };
    })());
    setId('7');
    setSearch('');
    try { window.localStorage.clear(); } catch { /* */ }
    window.localStorage.setItem('weekView', 'songs');
  });

  it('a song dragged is queued as a move for every week; the week shows it moved', async () => {
    render(Page);
    await moveLastUp();
    await vi.waitFor(() => expect(queue()).toEqual([{ kind: 'moveRules', from: 2, count: 1, to: 1, sectionId: 'morning' }]));
    expect(await screen.findByTestId('song-edit-count')).toHaveTextContent('1 change');
    await vi.waitFor(() => expect(screen.getAllByTestId('song-row').map((r) => r.querySelector('.track')?.textContent ?? r.textContent))
      .toEqual([expect.stringContaining('Song 1'), expect.stringContaining('Song 3'), expect.stringContaining('Song 2')]));
    expect(screen.getAllByTestId('song-change').map((c) => c.textContent)).toEqual(['moved']);
    // The preview is in: it can move again. Straight after, it waits
    // again (the list shown is of the queue before), till that's in.
    expect(note()).not.toContain('waits');
    const grip = screen.getAllByTestId('song-grip')[0];
    await key(grip, ' ');
    await key(grip, 'End');
    await key(grip, ' ');
    expect(queue()).toHaveLength(2);
    expect(note()).toContain('waits');
    await vi.waitFor(() => expect(note()).not.toContain('waits'));
  });

  it('while the station loads for the first move, a second waits; a failed preview says so', async () => {
    let loaded!: () => void;
    getStationSidecarMock
      .mockImplementationOnce(async () => ({ template: template(), sidecar: sidecar(), sidecar_error: null, updated_at: 'ts-1' }))
      .mockImplementationOnce(() => new Promise((r) => {
        loaded = () => r({ template: template(), sidecar: sidecar(), sidecar_error: null, updated_at: 'ts-1' });
      }));
    watchJobMock.mockImplementation(() => (async function* () {
      yield { status: 'error', error: { message: 'boom' } };
    })());
    render(Page);
    await moveLastUp();
    await vi.waitFor(() => expect(note()).toBe('Loading the station…'));
    loaded();
    await vi.waitFor(() => expect(queue()).toHaveLength(1));
    await vi.waitFor(() => expect(note()).toBe("Songs can't be moved until the preview works again."));
  });

  it("a week made before the rules last changed can't be moved here, and says what to do", async () => {
    getTraceMock.mockImplementation(async () => flatWeek([1, 2, 3], 'window'));
    render(Page);
    const grips = await screen.findAllByTestId('song-grip');
    expect(grips[0]).toHaveAttribute('aria-disabled', 'true');
    expect(note()).toContain("made before the station's rules last changed");
    await moveLastUp();
    expect(queue()).toEqual([]);
  });

  it('rules changed between showing the week and the move: nothing queued, and it says to try again', async () => {
    // The page's first look has the rules the week was made with; by the
    // time the move starts editing, they've changed.
    getStationSidecarMock
      .mockImplementationOnce(async () => ({ template: template(), sidecar: sidecar(), sidecar_error: null, updated_at: 'ts-1' }))
      .mockImplementation(async () => ({ template: template('window'), sidecar: sidecar(), sidecar_error: null, updated_at: 'ts-2' }));
    render(Page);
    await moveLastUp();
    await vi.waitFor(() => expect(note()).toContain('The rules changed while you moved that'));
    expect(queue()).toEqual([]);
  });

  it('an older version of the week has no grips', async () => {
    setSearch('?v=1');
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 2, versions: [
      { version_no: 1, trace_id: 'abc12345-trace-id', generated_at: 'a', seed: null, tags: [] },
      { version_no: 2, trace_id: 'newer', generated_at: 'b', seed: null, tags: [] },
    ] });
    render(Page);
    await screen.findByTestId('song-edit-older');
    expect(screen.queryByTestId('song-grip')).toBeNull();
  });
});

describe('trace viewer — moving songs, a station built from a repeating row', () => {
  // Two days of three blocks: Morning has blocks 0-1, Evening block 2.
  const template = () => ({
    station: 'cls', name: 'Classics',
    sources: [{ name: 'A', colour: '#c0392b', role: 'content' }, { name: 'B', colour: '#8e44ad', role: 'content' }],
    layout: {
      unit: 'day', unit_labels: ['Mon', 'Tue'], row_start_offsets: [0, 3],
      row_template: [{ source: 'A', strategy: 'pick', size: 1 }, { source: 'A', strategy: 'pick', size: 1 }, { source: 'B', strategy: 'pick', size: 1 }],
    },
  });
  const at = (r: number, b: number) => ({ kind: 'atomic', row_idx: r, block_idx: b });
  const sidecar = () => ({
    schema_version: 1,
    sections: [
      { id: 'morning', name: 'Morning', blocks: [at(0, 0), at(0, 1), at(1, 0), at(1, 1)] },
      { id: 'evening', name: 'Evening', blocks: [at(0, 2), at(1, 2)] },
    ],
    macros: [],
    layout_order: ['morning', 'evening'],
  });
  // Each day: the rules in `order` (by their place now), one song each.
  const days = (order = [0, 1, 2]) => stubTrace({
    slots: [1, 2].flatMap((row) => order.map((b, i) => ({
      slot: (row - 1) * 3 + i + 1, unit: row === 1 ? 'Mon' : 'Tue', row, block: i + 1,
      source: b === 2 ? 'B' : 'A', strategy: 'pick', pool_index: i, track_id: row * 10 + b,
      track_name: `${row === 1 ? 'Mon' : 'Tue'} ${b}`, track_artist: 'Artist', reason: 'picked',
    }))),
  });
  const queue = () => JSON.parse(window.localStorage.getItem('stagedEdits:cls') ?? '{"opLog":[]}').opLog as unknown[];
  const key = async (el: HTMLElement, k: string) => { await act(async () => { await fireEvent.keyDown(el, { key: k }); }); };

  beforeEach(() => {
    for (const m of [getPlaylistMock, getTraceMock, getStationMock, getStationPlaylistsMock, getVersionsMock,
      getStationSidecarMock, startPreviewJobMock, watchJobMock, applyBatchMock, startGenerateJobMock]) m.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 1, versions: [] });
    stationServerSettingsMock.mockResolvedValue(
      null as unknown as Awaited<ReturnType<typeof stationServerSettingsMock>>,
    );
    getStationSidecarMock.mockImplementation(async () => ({ template: template(), sidecar: sidecar(), sidecar_error: null, updated_at: 'ts-1' }));
    getTraceMock.mockImplementation(async () => days());
    getPlaylistMock.mockImplementation(async () => stubPlaylist());
    getStationMock.mockImplementation(async () => stubStation());
    let n = 0;
    startPreviewJobMock.mockImplementation(async () => ({ job_id: `pv-${++n}`, status: 'running' }));
    watchJobMock.mockImplementation((id: unknown) => (async function* () {
      const trace = id === 'pv-1' ? days() : days([2, 0, 1]);
      yield { status: 'done', result: { trace, slot_count: 6 } };
    })());
    setId('7');
    setSearch('');
    try { window.localStorage.clear(); } catch { /* */ }
    window.localStorage.setItem('weekView', 'songs');
  });

  it("one section repeated for every day (as Rebirth Classics'): the same move, the section kept", async () => {
    const cycle = () => ({
      schema_version: 1,
      sections: [{ id: 'cycle', name: 'Weekly cycle', repeat_with_offsets: [0, 3], blocks: [0, 1, 2].map((b) => at(0, b)) }],
      macros: [],
      layout_order: ['cycle'],
    });
    getStationSidecarMock.mockImplementation(async () => ({ template: template(), sidecar: cycle(), sidecar_error: null, updated_at: 'ts-1' }));
    render(Page);
    const grips = await screen.findAllByTestId('song-grip');
    await key(grips[5], ' ');
    await key(grips[5], 'Home');
    await key(grips[5], ' ');
    await vi.waitFor(() => expect(queue()).toEqual([{ kind: 'moveRules', from: 2, count: 1, to: 0, sectionId: 'cycle' }]));
    await vi.waitFor(() => expect(screen.getAllByTestId('song-change').map((c) => c.textContent)).toEqual(['moved', 'moved']));
    expect(screen.getAllByTestId('song-edit-item')[0]).toHaveTextContent('Moved Weekly cycle, rule 3 to rule 1, in every day');
  });

  it("Tue's evening song to the front of its day: queued for the row, so every day shows it moved", async () => {
    render(Page);
    const grips = await screen.findAllByTestId('song-grip');
    expect(screen.getByTestId('song-move-note')).toHaveTextContent('A move is made in every day');
    await key(grips[5], ' ');
    await key(grips[5], 'Home');
    await key(grips[5], ' ');
    await vi.waitFor(() => expect(queue()).toEqual([{ kind: 'moveRules', from: 2, count: 1, to: 0, sectionId: 'morning' }]));
    expect(await screen.findByTestId('song-edit-count')).toHaveTextContent('1 change');
    await vi.waitFor(() => expect(screen.getAllByTestId('song-change').map((c) => c.textContent)).toEqual(['moved', 'moved']));
    expect(screen.getAllByTestId('song-edit-item')[0]).toHaveTextContent('Moved Evening, rule 1 to Morning, rule 1, in every day');
  });
});


describe('trace viewer — just this week', () => {
  // A row station of one day whose week lines up with its rules: rule 1
  // (window A) plays songs 1 and 2, rule 2 (pick B) song 3.
  const template = () => ({
    station: 'cls', name: 'Classics',
    sources: [
      { name: 'A', colour: '#c0392b', role: 'content' },
      { name: 'B', colour: '#8e44ad', role: 'content' },
    ],
    layout: {
      unit: 'day', unit_labels: ['Mon'], row_start_offsets: [0],
      row_template: [{ source: 'A', strategy: 'window', size: 2 }, { source: 'B', strategy: 'pick', size: 1 }],
    },
  });
  const sidecar = () => ({
    schema_version: 1,
    sections: [{ id: 'day', name: 'The day', blocks: [
      { kind: 'atomic', row_idx: 0, block_idx: 0 },
      { kind: 'atomic', row_idx: 0, block_idx: 1 },
    ] }],
    macros: [],
    layout_order: ['day'],
  });
  const week = (overrides: Record<string, unknown> = {}) => {
    const base = stubTrace();
    return {
      ...base,
      version_no: 1,
      note: null,
      slots: [
        { ...base.slots[0], unit: 'Mon' },
        { ...base.slots[1], unit: 'Mon' },
        { ...base.slots[2], unit: 'Mon', row: 1, block: 2, source: 'B', strategy: 'pick' },
      ],
      ...overrides,
    };
  };
  const noChanges = { playlist_id: 7, version_no: 1, max_rule_changes: 20, changes: [] };
  const swapped = {
    id: 41, row: 1, block: 1, k: 1, kind: 'swap', track_id: 555, rule: null,
    rule_strategy: 'window', rule_source: 'A', position: 2, also_plays_at: [], created_at: null,
    track: { id: 555, name: 'Mine', artist: 'Me', album: null },
  };

  beforeEach(() => {
    for (const m of [getPlaylistMock, getTraceMock, getStationMock, getStationPlaylistsMock, getVersionsMock,
      getStationSidecarMock, startPreviewJobMock, watchJobMock, applyBatchMock, startGenerateJobMock,
      getWeekChangesMock, addWeekChangeMock, undoWeekChangeMock, searchStationSongsMock, gotoMock]) m.mockReset();
    getStationPlaylistsMock.mockResolvedValue([]);
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 1, versions: [] });
    stationServerSettingsMock.mockResolvedValue(
      null as unknown as Awaited<ReturnType<typeof stationServerSettingsMock>>,
    );
    getStationSidecarMock.mockImplementation(async () => ({
      template: template(), sidecar: sidecar(), sidecar_error: null, updated_at: 'ts-1',
    }));
    getTraceMock.mockImplementation(async () => week());
    getPlaylistMock.mockImplementation(async () => stubPlaylist());
    getStationMock.mockImplementation(async () => stubStation());
    getWeekChangesMock.mockImplementation(async () => noChanges);
    startPreviewJobMock.mockImplementation(async () => ({ job_id: 'pv', status: 'running' }));
    watchJobMock.mockImplementation(() => (async function* () {
      yield { status: 'done', result: { trace: week(), slot_count: 3 } };
    })());
    setId('7');
    setSearch('');
    try { window.localStorage.clear(); } catch { /* */ }
    window.localStorage.setItem('weekView', 'songs');
  });

  async function pick(n: number): Promise<void> {
    await fireEvent.click((await screen.findAllByTestId('song-row'))[n]);
    await screen.findByTestId('detail-panel');
  }
  async function retypeToPick(): Promise<void> {
    await fireEvent.click(screen.getByTestId('retype-toggle'));
    await fireEvent.change(screen.getByTestId('retype-strategy'), { target: { value: 'pick' } });
    await fireEvent.click(screen.getByTestId('retype-apply'));
  }
  /** The week after a change: version 2, with `slot` at `at`. */
  function afterChange(at: number, slot: Record<string, unknown>, changes: unknown[]) {
    const w = week({ version_no: 2, note: 'Just this week: …', trace_id: 'v2' });
    w.slots[at] = { ...w.slots[at], ...slot } as (typeof w.slots)[number];
    getTraceMock.mockImplementation(async () => w);
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 2, versions: [] });
    getWeekChangesMock.mockImplementation(async () => ({ ...noChanges, version_no: 2, changes }));
  }

  it('changing a rule asks: every week queues it, as before', async () => {
    render(Page);
    await pick(0);
    await retypeToPick();
    const ask = await screen.findByTestId('change-scope');
    expect(ask).toHaveTextContent('Song 1 (Mon): Track Mon-1 by Artist 1');
    // First time: "every week" has focus, so Enter takes it.
    expect(document.activeElement).toBe(screen.getByTestId('change-scope-every'));
    await fireEvent.click(screen.getByTestId('change-scope-every'));
    expect(screen.queryByTestId('change-scope')).toBeNull();
    expect(await screen.findByTestId('song-edit-count')).toHaveTextContent('for every week');
    expect(addWeekChangeMock).not.toHaveBeenCalled();
  });

  it('just this song saves the rule change for that song now, against the version shown', async () => {
    addWeekChangeMock.mockImplementation(async () => {
      afterChange(0, {
        track_id: 777, track_name: 'Picked Now', reason: 'Just this week: picked',
        change: { kind: 'rule', was: { track_id: 100, track_name: 'Track Mon-1', track_artist: 'Artist 1', pool_index: 1 },
          source: 'A', strategy: 'pick', pool_index: 4, rule: { source: 'A', strategy: 'pick', size: 2 }, fields: { strategy: 'pick' } },
      }, [{ ...swapped, id: 42, kind: 'rule', k: 0, position: 1, track_id: null, track: null,
        rule: { source: 'A', strategy: 'pick', size: 2 } }]);
      return { playlist_id: 7, version_no: 2, note: 'Just this week: song 1 (Mon) picked by its rule changed: strategy pick',
        change: {}, slot: {} };
    });
    render(Page);
    await pick(0);
    await retypeToPick();
    await fireEvent.click(await screen.findByTestId('change-scope-week'));
    await vi.waitFor(() => expect(addWeekChangeMock).toHaveBeenCalledTimes(1));
    expect(addWeekChangeMock).toHaveBeenCalledWith(7, 1, {
      row: 1, block: 1, k: 0, kind: 'rule', track_id: null,
      rule: { source: 'A', strategy: 'pick', size: 2 },
      rule_strategy: 'window', rule_source: 'A',
    });
    expect(await screen.findByTestId('week-change-status'))
      .toHaveTextContent('song 1 (Mon) picked by its rule changed: strategy pick, just this week (version 2)');
    // Shown as changed just this week, in the list and on the song.
    expect(screen.getByTestId('week-only')).toHaveTextContent('Song 1 (Mon)');
    expect(screen.getByTestId('week-only')).toHaveTextContent('Picked by its rule changed: strategy pick');
    expect(screen.getAllByTestId('song-row')[0]).toHaveTextContent('Picked Now');
    expect(screen.getByTestId('song-week-only')).toHaveTextContent('just this week');
    // Nothing queued for every week; next time, "just this song" is offered first.
    expect(screen.queryByTestId('song-edit-count')).toBeNull();
    expect(window.localStorage.getItem('changeScope')).toBe('week');
  });

  it("just this song isn't offered while rule changes are queued, nor for a size-only change", async () => {
    render(Page);
    await pick(0);
    await retypeToPick();
    await fireEvent.click(await screen.findByTestId('change-scope-every'));
    await screen.findByTestId('song-edit-count');
    // Another change to the rule: the week is the queue's preview now.
    await fireEvent.click(screen.getByTestId('retype-toggle'));
    await fireEvent.change(screen.getByTestId('retype-strategy'), { target: { value: 'window' } });
    await fireEvent.click(screen.getByTestId('retype-apply'));
    expect(await screen.findByTestId('change-scope-why')).toHaveTextContent('Save or discard the rule changes queued');
    // The panel's song box doesn't offer a swap from the queue's preview
    // either (its places aren't the week's).
    expect(screen.getByTestId('this-song')).toHaveTextContent('save or discard the rule changes queued');
    expect(screen.queryByTestId('this-song-swap')).toBeNull();
    await fireEvent.click(screen.getByTestId('change-scope-week'));
    expect(screen.getByTestId('change-scope')).toBeInTheDocument(); // not taken
    await fireEvent.keyDown(screen.getByTestId('change-scope'), { key: 'Escape' });
    expect(screen.queryByTestId('change-scope')).toBeNull();
    expect(addWeekChangeMock).not.toHaveBeenCalled();
  });

  it('swap a song: search the station, pick one, saved at once', async () => {
    searchStationSongsMock.mockImplementation(async () => ({
      songs: [
        { id: 100, name: 'Track Mon-1', artist: 'Artist 1', album: null, pools: ['A'] },
        { id: 200, name: 'Track Tue-1', artist: 'Artist 2', album: null, pools: ['B'] },
        { id: 555, name: 'Mine', artist: 'Me', album: null, pools: ['A', 'B'] },
      ],
      more: false,
    }));
    addWeekChangeMock.mockImplementation(async () => {
      afterChange(1, { track_id: 555, track_name: 'Mine', track_artist: 'Me',
        change: { kind: 'swap', was: { track_id: 101, track_name: 'Track Mon-2', track_artist: null, pool_index: 2 } } },
      [swapped]);
      return { playlist_id: 7, version_no: 2, note: 'Just this week: song 2 (Mon) swapped for Mine by Me', change: {}, slot: {} };
    });
    render(Page);
    await pick(1);
    expect(screen.getByTestId('this-song')).toHaveTextContent('Song 2 (Mon): Track Mon-2');
    await fireEvent.click(screen.getByTestId('this-song-swap'));
    const dialog = await screen.findByTestId('swap-song');
    expect(searchStationSongsMock).toHaveBeenCalledWith('cls', '', 'A');
    const results = await screen.findAllByTestId('swap-song-result');
    // Its rule's song isn't offered; songs in the week say where they play.
    expect(results.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Track Mon-1'), expect.stringContaining('Track Tue-1'), expect.stringContaining('Mine'),
    ]);
    expect(results[0]).toHaveTextContent('In this week already: song 1');
    expect(results[1]).toHaveTextContent('In this week already: song 3');
    await fireEvent.click(results[2]);
    await vi.waitFor(() => expect(addWeekChangeMock).toHaveBeenCalledWith(7, 1, {
      row: 1, block: 1, k: 1, kind: 'swap', track_id: 555, rule: null, rule_strategy: 'window', rule_source: 'A',
    }));
    await vi.waitFor(() => expect(dialog).not.toBeInTheDocument());
    expect(await screen.findByTestId('week-change-status')).toHaveTextContent('song 2 (Mon) swapped for Mine by Me');
  });

  it('a swap refused because the week changed meanwhile reloads it and says so', async () => {
    const { WeekChangeConflict } = await vi.importActual<typeof import('$lib/api')>('$lib/api');
    searchStationSongsMock.mockImplementation(async () => ({
      songs: [{ id: 555, name: 'Mine', artist: 'Me', album: null, pools: ['A'] }], more: false,
    }));
    addWeekChangeMock.mockImplementation(async () => {
      throw new WeekChangeConflict("the week has changed since (it's now version 2); reload it and try again", 2);
    });
    render(Page);
    await pick(1);
    await fireEvent.click(screen.getByTestId('this-song-swap'));
    const loads = getTraceMock.mock.calls.length;
    await fireEvent.click((await screen.findAllByTestId('swap-song-result'))[0]);
    expect(await screen.findByTestId('swap-song-error')).toHaveTextContent('The week has been reloaded');
    expect(getTraceMock.mock.calls.length).toBeGreaterThan(loads);
    expect(screen.getByTestId('week-change-error')).toHaveTextContent('reload it and try again');
  });

  it("the week's changes are listed, each with an Undo saved at once", async () => {
    getWeekChangesMock.mockImplementation(async () => ({ ...noChanges, changes: [{ ...swapped, also_plays_at: [3] }] }));
    getTraceMock.mockImplementation(async () => {
      const w = week();
      w.slots[1] = { ...w.slots[1], track_id: 555, track_name: 'Mine', track_artist: 'Me',
        change: { kind: 'swap', was: { track_id: 101, track_name: 'Track Mon-2', track_artist: null, pool_index: 2 } } } as never;
      return w;
    });
    undoWeekChangeMock.mockImplementation(async () => {
      afterChange(1, {}, []);
      return { playlist_id: 7, version_no: 2, note: "Just this week: song 2 (Mon) back to its rule's song" };
    });
    render(Page);
    const item = await screen.findByTestId('week-only-item');
    expect(item).toHaveTextContent('Song 2 (Mon)');
    expect(item).toHaveTextContent('Swapped for Mine by Me');
    expect(item).toHaveTextContent('Also plays as song 3');
    expect(screen.getByTestId('song-week-only')).toHaveAttribute('title', expect.stringContaining('its rule picked Track Mon-2'));
    await fireEvent.click(screen.getByTestId('week-only-undo'));
    await vi.waitFor(() => expect(undoWeekChangeMock).toHaveBeenCalledWith(7, 41, 1));
    await vi.waitFor(() => expect(screen.queryByTestId('week-only')).toBeNull());
    expect(screen.getByTestId('week-change-status')).toHaveTextContent("song 2 (Mon) back to its rule's song (version 2)");
  });

  it("the table and the version picker say what's just this week", async () => {
    window.localStorage.setItem('weekView', 'table');
    getTraceMock.mockImplementation(async () => {
      const w = week({ version_no: 2 });
      w.slots[1] = { ...w.slots[1], change: { kind: 'swap', was: { track_id: 101, track_name: 'Track Mon-2',
        track_artist: null, pool_index: 2 } } } as never;
      return w;
    });
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 2, versions: [
      { version_no: 1, trace_id: 'v1', generated_at: 'a', seed: null, tags: [], note: null },
      { version_no: 2, trace_id: 'abc12345-trace-id', generated_at: 'b', seed: null, tags: [],
        note: 'Just this week: song 2 (Mon) swapped for Mine by Me' },
    ] });
    render(Page);
    expect(await screen.findByTestId('table-week-only')).toHaveTextContent('just this week');
    const options = [...(screen.getByTestId('version-select') as HTMLSelectElement).options].map((o) => o.textContent);
    expect(options[1]).toContain('Just this week: song 2 (Mon) swapped for Mine by Me');
    expect(options[0]).not.toContain('Just this week');
  });

  it("previews of queued rule changes make the week's changes", async () => {
    getWeekChangesMock.mockImplementation(async () => ({ ...noChanges, changes: [swapped] }));
    render(Page);
    await pick(0);
    await vi.waitFor(() => expect(startPreviewJobMock).toHaveBeenCalled());
    // The saved week's preview: the change as it is.
    expect(startPreviewJobMock.mock.calls[0][6]).toEqual([{
      row: 1, block: 1, k: 1, kind: 'swap', track_id: 555, rule: null, rule_strategy: 'window', rule_source: 'A',
    }]);
    // A queued change to its rule: the edited week's preview makes the
    // change too (where saving would put it: the same place here; the
    // moves are covered by changesAfter's own tests).
    await fireEvent.click(screen.getByTestId('retype-toggle'));
    await fireEvent.change(screen.getByTestId('retype-strategy'), { target: { value: 'pick' } });
    await fireEvent.click(screen.getByTestId('retype-apply'));
    await fireEvent.click(await screen.findByTestId('change-scope-every'));
    await vi.waitFor(() => expect(startPreviewJobMock).toHaveBeenCalledTimes(2));
    expect(startPreviewJobMock.mock.calls[1][6]).toEqual([expect.objectContaining({ block: 1, k: 1 })]);
  });

  it("a queued change that would drop a week's change says so", async () => {
    getWeekChangesMock.mockImplementation(async () => ({ ...noChanges, changes: [swapped] }));
    watchJobMock.mockImplementation((id: unknown) => (async function* () {
      yield { status: 'done', result: { trace: week(), slot_count: 3,
        week_changes_dropped: id === 'pv' && startPreviewJobMock.mock.calls.length > 1
          ? [{ id: null, row: 1, block: 1, k: 1, kind: 'swap', reason: 'the rule for that song has changed since' }]
          : [] } };
    })());
    render(Page);
    await pick(0);
    await retypeToPick();
    await fireEvent.click(await screen.findByTestId('change-scope-every'));
    expect(await screen.findByTestId('song-edit-drops'))
      .toHaveTextContent('drops 1 just-this-week change: song 2 (Mon) (the rule for that song has changed since)');
  });

  it('regenerating says which changes it kept and which it dropped', async () => {
    getWeekChangesMock.mockImplementation(async () => ({ ...noChanges, changes: [swapped] }));
    render(Page);
    await pick(0);
    await retypeToPick();
    await fireEvent.click(await screen.findByTestId('change-scope-every'));
    applyBatchMock.mockResolvedValueOnce({ template: template(), sidecar: sidecar(), updated_at: 'ts-2' });
    await fireEvent.click(await screen.findByTestId('song-edit-save'));
    await screen.findByTestId('song-edit-saved');
    watchJobMock.mockImplementation(() => (async function* () {
      yield { status: 'done', result: { playlist_id: 7, trace_id: 'new', station: 'cls', week: 3, slot_count: 3,
        week_changes_applied: 1,
        week_changes_dropped: [{ id: 9, row: 1, block: 2, k: 0, kind: 'rule', reason: 'the rule for that song has changed since' }] } };
    })());
    await fireEvent.click(screen.getByTestId('song-edit-regenerate'));
    expect(await screen.findByTestId('song-regenerated')).toHaveTextContent(
      'Regenerated week 3 with the saved rules. Kept 1 just-this-week change. Dropped 1 just-this-week change that no longer fits: song 3 (Mon): the rule for that song has changed since.',
    );
  });

  it('an older version of the week offers no changes', async () => {
    setSearch('?v=1');
    getVersionsMock.mockResolvedValue({ playlist_id: 7, current_v: 2, versions: [] });
    getWeekChangesMock.mockImplementation(async () => ({ ...noChanges, version_no: 2, changes: [swapped] }));
    render(Page);
    await screen.findByTestId('song-edit-older');
    expect(screen.queryByTestId('week-only')).toBeNull();
  });
});
