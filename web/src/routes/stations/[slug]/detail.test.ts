import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

// $app/stores.page is a Svelte readable store; tests need to provide one
// with a `subscribe(fn)` that emits a value synchronously. We use a
// hoisted holder so tests can swap the slug per case.
const {
  getStationMock,
  getStationPlaylistsMock,
  getStationOverviewMock,
  startGenerateJobMock,
  watchJobMock,
  cancelJobMock,
  deletePlaylistMock,
  deleteStationMock,
  forkStationMock,
  gotoMock,
  panelListSourcesMock,
  panelUploadSourceMock,
  panelImportByPathMock,
  panelDeleteSourceMock,
  confirmDialogMock,
  FakeApiError,
  FakeJobStartConflict,
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
  class FakeJobStartConflict extends FakeApiError {
    existing_job_id: string;
    constructor(message: string, existing_job_id: string) {
      super(409, message);
      this.name = 'JobStartConflict';
      this.existing_job_id = existing_job_id;
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
    getStationOverviewMock: vi.fn(),
    startGenerateJobMock: vi.fn(),
    watchJobMock: vi.fn(),
    cancelJobMock: vi.fn(),
    deletePlaylistMock: vi.fn(),
    deleteStationMock: vi.fn(),
    forkStationMock: vi.fn(),
    panelListSourcesMock: vi.fn(),
    panelUploadSourceMock: vi.fn(),
    panelImportByPathMock: vi.fn(),
    panelDeleteSourceMock: vi.fn(),
    // §16.25.t4: replaces `vi.spyOn(window, 'confirm')` across this
    // page's 4 destructive ops (regen, delete week, replace week,
    // nuke station). Default is set in `beforeEach` (= Confirm
    // clicked); per-test overrides call mockResolvedValueOnce(false)
    // for the Cancel path. Untyped `vi.fn()` so the vi.mock factory's
    // `(...args: unknown[])` spread typechecks.
    confirmDialogMock: vi.fn(),
    gotoMock: vi.fn(),
    FakeApiError,
    FakeJobStartConflict,
    pageStore,
    setSlug,
  };
});

vi.mock('$lib/api', () => ({
  getStation: (...args: unknown[]) => getStationMock(...args),
  getStationPlaylists: (...args: unknown[]) => getStationPlaylistsMock(...args),
  getStationOverview: (...args: unknown[]) => getStationOverviewMock(...args),
  startGenerateJob: (...args: unknown[]) => startGenerateJobMock(...args),
  watchJob: (...args: unknown[]) => watchJobMock(...args),
  cancelJob: (...args: unknown[]) => cancelJobMock(...args),
  deletePlaylist: (...args: unknown[]) => deletePlaylistMock(...args),
  deleteStation: (...args: unknown[]) => deleteStationMock(...args),
  forkStation: (...args: unknown[]) => forkStationMock(...args),
  ApiError: FakeApiError,
  JobStartConflict: FakeJobStartConflict,
  // §16.2.6 c3 review #1 batch C: the page migrated from the
  // inline `'playlist_id' in r` narrow to `isGenerateResult`.
  // Mock as the same predicate so tests that drive snap.result
  // through the done branch still navigate.
  isGenerateResult: (r: unknown) =>
    r !== null && typeof r === 'object' && 'playlist_id' in r,
  // §16.25 c5: the page embeds <PoolImportPanel> which calls into
  // these helpers. Defaults are no-op resolutions (the panel mounts
  // cleanly without polluting station-detail assertions). Individual
  // tests that need to drive panel behaviour — e.g. verifying the
  // c5-review 🟡-1 `on:changed` → refreshOverview wiring — override
  // these via the `setPanelHelpers` escape hatch below.
  listSources: (...args: unknown[]) => panelListSourcesMock(...args),
  uploadSource: (...args: unknown[]) => panelUploadSourceMock(...args),
  importSourceByPath: (...args: unknown[]) => panelImportByPathMock(...args),
  deleteSource: (...args: unknown[]) => panelDeleteSourceMock(...args),
}));
vi.mock('$lib/confirm', () => ({
  // §16.25.t4: every confirm() call in /stations/[slug] (regen,
  // delete week, replace week, nuke station) routes through this.
  confirmDialog: (...args: unknown[]) => confirmDialogMock(...args),
}));

/**
 * §16.11 c5 — helper: wraps a list of snapshots as an AsyncIterable.
 * watchJob is now an async generator that yields snapshots until
 * a terminal status; tests mock it via `watchJobMock.mockReturnValueOnce(
 * snapsAsAsyncIterable([...]))`.
 *
 * Yielding the array entries one-by-one matches the real generator's
 * one-snapshot-per-poll shape — the page's `for await` consumes them
 * sequentially, updating currentJob each tick.
 */
function snapsAsAsyncIterable(snaps: unknown[]): AsyncIterable<unknown> {
  return {
    async *[Symbol.asyncIterator]() {
      for (const s of snaps) yield s;
    },
  };
}

/** Build a minimal JobSnapshot-shaped object for tests. */
function fakeSnap(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    job_id: 'j-1',
    slug: 'cls',
    kind: 'generate',
    status: 'running',
    phase: 'queued',
    current: 0,
    total: 0,
    label: null,
    result: null,
    error: null,
    started_at: '2026-05-27T10:00:00Z',
    finished_at: null,
    ...over,
  };
}

/**
 * Convenience: most tests expect "start a job, poll once, see done +
 * a result, navigate". Wires both mocks in one call.
 */
function mockJobThatCompletes(
  job_id: string,
  result: { playlist_id: number; trace_id: string; station: string; week: number; slot_count: number },
) {
  startGenerateJobMock.mockResolvedValueOnce({ job_id, status: 'running' });
  watchJobMock.mockReturnValueOnce(
    snapsAsAsyncIterable([
      fakeSnap({ job_id, status: 'done', result, finished_at: '2026-05-27T10:00:01Z' }),
    ]),
  );
}

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
    startGenerateJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    deletePlaylistMock.mockReset();
    deleteStationMock.mockReset();
    gotoMock.mockReset();
    confirmDialogMock.mockReset();
    confirmDialogMock.mockResolvedValue(true);
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
    startGenerateJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    deletePlaylistMock.mockReset();
    deleteStationMock.mockReset();
    gotoMock.mockReset();
    confirmDialogMock.mockReset();
    confirmDialogMock.mockResolvedValue(true);
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

  it('clicking the empty-state CTA starts a job and navigates on done', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    mockJobThatCompletes('job-1', {
      playlist_id: 99,
      trace_id: 'abcdef1234567890',
      station: 'cls',
      week: 1,
      slot_count: 3010,
    });
    render(Page);

    const cta = await screen.findByTestId('generate-empty-cta');
    await fireEvent.click(cta);

    expect(startGenerateJobMock).toHaveBeenCalledWith('cls', 1, undefined);
    // act() flush — startGenerateJob resolves, watchJob's async
    // iterator yields the done snapshot, then goto fires.
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
    confirmDialogMock.mockResolvedValueOnce(false);

    render(Page);
    const weekInput = await screen.findByTestId('advanced-week');
    await fireEvent.input(weekInput, { target: { value: '1' } });
    await fireEvent.click(screen.getByTestId('advanced-submit'));

    // §16.4 — confirm title changed from "Replace" to "Regenerate"
    // since the prior trace is now PRESERVED as a prior version
    // (not deleted). Match the new copy.
    expect(confirmDialogMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: expect.stringMatching(/regenerate week 1/i) }),
    );
    expect(startGenerateJobMock).not.toHaveBeenCalled();
  });

  it('per-row regen confirms then starts a job with no seed for rng:off', async () => {
    getStationMock.mockResolvedValueOnce(stubStation('off'));
    getStationPlaylistsMock.mockResolvedValueOnce([
      { id: 5, week: 5, trace_id: 'feedface1234', generated_at: null, entry_count: 3010 },
    ]);
    mockJobThatCompletes('job-50', {
      playlist_id: 50,
      trace_id: 'newnewnewnew',
      station: 'cls',
      week: 5,
      slot_count: 3010,
    });
    confirmDialogMock.mockResolvedValueOnce(true);

    render(Page);
    const regen = await screen.findByTestId('regen-row-5');
    await fireEvent.click(regen);

    expect(confirmDialogMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: expect.stringMatching(/regenerate week 5/i) }),
    );
    expect(startGenerateJobMock).toHaveBeenCalledWith('cls', 5, undefined);
    await act();
    expect(gotoMock).toHaveBeenCalledWith('/playlists/50');
  });

  it('per-row regen sends a fresh random seed for rng:on stations', async () => {
    getStationMock.mockResolvedValueOnce(stubStation('on'));
    getStationPlaylistsMock.mockResolvedValueOnce([
      { id: 5, week: 5, trace_id: 'feedface1234', generated_at: null, entry_count: 3010 },
    ]);
    mockJobThatCompletes('job-50', {
      playlist_id: 50,
      trace_id: 'x',
      station: 'cls',
      week: 5,
      slot_count: 3010,
    });
    confirmDialogMock.mockResolvedValueOnce(true);

    render(Page);
    await fireEvent.click(await screen.findByTestId('regen-row-5'));

    const [slug, week, seed] = startGenerateJobMock.mock.calls[0];
    expect(slug).toBe('cls');
    expect(week).toBe(5);
    // Per D3: rng:on regen sends *some* integer seed (the form-input one
    // would only be used if the user opened the advanced form first).
    expect(typeof seed).toBe('number');
    expect(seed).toBeGreaterThanOrEqual(0);
  });

  it('renders the API error inline and does not navigate on failure', async () => {
    // §16.11 c5: start-time API errors (400 bad pool, 404 station)
    // still surface as inline messages — startGenerateJob's rejection
    // path matches the old generatePlaylist behaviour.
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    startGenerateJobMock.mockRejectedValueOnce(
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

  // ----- §16.11 c5 — async-job flow paths --------------------------------

  it('shows the <ProgressIndicator> while the job is running', async () => {
    // Synthesise a watchJob iterator that emits a running snapshot
    // and then suspends forever — the test never resolves the
    // generator. Inspects the in-progress UI.
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    startGenerateJobMock.mockResolvedValueOnce({
      job_id: 'job-x', status: 'running',
    });
    watchJobMock.mockReturnValueOnce({
      async *[Symbol.asyncIterator]() {
        yield fakeSnap({
          job_id: 'job-x', status: 'running', phase: 'picks',
          current: 30, total: 100, label: 'Generating week 1 (30 / 100)',
        });
        // Then suspend on a never-resolving promise so the consumer
        // stays in the loop and the indicator stays mounted.
        await new Promise(() => {});
      },
    });

    render(Page);
    await fireEvent.click(await screen.findByTestId('generate-empty-cta'));
    await act();

    const indicator = await screen.findByTestId('progress-indicator');
    expect(indicator).toHaveAttribute('data-status', 'running');
    expect(screen.getByTestId('progress-label')).toHaveTextContent(
      /generating week 1 \(30 \/ 100\)/i,
    );
    expect(screen.getByTestId('progress-cancel')).toBeInTheDocument();
    // Did NOT navigate — job's still running.
    expect(gotoMock).not.toHaveBeenCalled();
  });

  it('renders status=error with kind + message and stays on page', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    startGenerateJobMock.mockResolvedValueOnce({
      job_id: 'job-err', status: 'running',
    });
    watchJobMock.mockReturnValueOnce(
      snapsAsAsyncIterable([
        fakeSnap({
          job_id: 'job-err', status: 'error',
          error: { kind: 'RuntimeError', message: 'engine crashed' },
          finished_at: '2026-05-27T10:00:01Z',
        }),
      ]),
    );

    render(Page);
    await fireEvent.click(await screen.findByTestId('generate-empty-cta'));
    await act();

    expect(await screen.findByTestId('generate-error')).toHaveTextContent(
      /RuntimeError.*engine crashed/i,
    );
    expect(gotoMock).not.toHaveBeenCalled();
    // Indicator cleared after terminal state surfaces the error.
    expect(screen.queryByTestId('progress-indicator')).not.toBeInTheDocument();
  });

  it('adopts the existing job on 409 JobStartConflict', async () => {
    // Decision F + design-doc intent (and mid-slice review): the
    // frontend polls the in-flight job rather than failing the click.
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    startGenerateJobMock.mockRejectedValueOnce(
      new FakeJobStartConflict('already running', 'older-job-7'),
    );
    watchJobMock.mockReturnValueOnce(
      snapsAsAsyncIterable([
        fakeSnap({
          job_id: 'older-job-7', status: 'done',
          result: {
            playlist_id: 7, trace_id: 't', station: 'cls',
            week: 1, slot_count: 100,
          },
          finished_at: '2026-05-27T10:00:01Z',
        }),
      ]),
    );

    render(Page);
    await fireEvent.click(await screen.findByTestId('generate-empty-cta'));
    await act();

    // watchJob was called with the EXISTING job_id, not a fresh one.
    expect(watchJobMock).toHaveBeenCalledWith('older-job-7');
    // Done snapshot → navigate to the existing job's playlist.
    expect(gotoMock).toHaveBeenCalledWith('/playlists/7');
  });

  it('clicking Cancel inside the indicator calls cancelJob', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([]);
    startGenerateJobMock.mockResolvedValueOnce({
      job_id: 'job-c', status: 'running',
    });
    watchJobMock.mockReturnValueOnce({
      async *[Symbol.asyncIterator]() {
        yield fakeSnap({ job_id: 'job-c', status: 'running', phase: 'picks',
                         current: 10, total: 100 });
        // Suspend until cancel is fired; test isn't asserting on the
        // post-cancel snapshot so the never-resolving promise is fine.
        await new Promise(() => {});
      },
    });
    cancelJobMock.mockResolvedValueOnce(undefined);

    render(Page);
    await fireEvent.click(await screen.findByTestId('generate-empty-cta'));
    await act();
    await fireEvent.click(await screen.findByTestId('progress-cancel'));

    expect(cancelJobMock).toHaveBeenCalledWith('job-c');
  });
});

/**
 * §16.3 Delete affordances — both per-row and station-nuke flows.
 *
 * Vitest covers the wiring: confirm() yes/no paths, refetch on success
 * (per-row), goto('/') on success (station), inline error rendering.
 * Playwright covers integration in delete.spec.ts.
 */
describe('station detail — §16.3 delete affordances', () => {
  beforeEach(() => {
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    startGenerateJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    deletePlaylistMock.mockReset();
    deleteStationMock.mockReset();
    gotoMock.mockReset();
    confirmDialogMock.mockReset();
    confirmDialogMock.mockResolvedValue(true);
    setSlug('cls');
  });

  const stubStation = () => ({
    id: 1,
    slug: 'cls',
    name: 'Classics',
    template: { rng: 'off' },
  });

  const playlistFixture = () => [
    { id: 5, week: 5, trace_id: 'feedface1234', generated_at: null, entry_count: 3010 },
    { id: 6, week: 6, trace_id: 'baadf00dabcd', generated_at: null, entry_count: 3010 },
  ];

  // ---- per-row delete -------------------------------------------------

  it('per-row delete: confirms with the trace fragment then DELETEs', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(playlistFixture());
    // After delete the page refetches; return the shrunken list.
    getStationPlaylistsMock.mockResolvedValueOnce([playlistFixture()[1]]);
    deletePlaylistMock.mockResolvedValueOnce(undefined);
    confirmDialogMock.mockResolvedValueOnce(true);

    render(Page);
    const btn = await screen.findByTestId('delete-row-5');
    await fireEvent.click(btn);

    // Title carries the week; body carries the trace prefix. Both
    // were one string under window.confirm(); now split across the
    // structured options (§16.25.t4).
    const delOpts = confirmDialogMock.mock.calls[0][0] as unknown as { title: string; body: string };
    expect(delOpts.title).toMatch(/delete week 5/i);
    expect(delOpts.body).toMatch(/feedface/i);
    expect(deletePlaylistMock).toHaveBeenCalledWith(5);
  });

  it('per-row delete: cancel skips the API call entirely', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(playlistFixture());
    confirmDialogMock.mockResolvedValueOnce(false);

    render(Page);
    await fireEvent.click(await screen.findByTestId('delete-row-5'));

    expect(deletePlaylistMock).not.toHaveBeenCalled();
  });

  it('per-row delete: 404 renders inline, no navigation', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(playlistFixture());
    deletePlaylistMock.mockRejectedValueOnce(
      new FakeApiError(404, '404 Not Found: playlist not found'),
    );
    confirmDialogMock.mockResolvedValueOnce(true);

    render(Page);
    await fireEvent.click(await screen.findByTestId('delete-row-5'));
    await act();

    expect(await screen.findByTestId('delete-error')).toHaveTextContent(/playlist not found/i);
    expect(gotoMock).not.toHaveBeenCalled();
  });

  // ---- station nuke ---------------------------------------------------

  it('station nuke: confirm copy includes computed counts', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(playlistFixture()); // 2 × 3010 = 6020
    confirmDialogMock.mockResolvedValueOnce(false);

    render(Page);
    await fireEvent.click(await screen.findByTestId('delete-station'));

    // The dialog text mentions both the playlist count (2) and the entry
    // total (6,020 with locale separator). Loose regex so a future copy
    // tweak doesn't break this. Title carries the slug; body carries
    // counts (§16.25.t4 split the single string into structured options).
    const nukeOpts = confirmDialogMock.mock.calls[0][0] as unknown as { title: string; body: string };
    expect(nukeOpts.title).toMatch(/'cls'/i);
    expect(nukeOpts.body).toMatch(/2 playlists.*6,?020/i);
    expect(deleteStationMock).not.toHaveBeenCalled();
  });

  it('station nuke: pluralisation handles the single-playlist case', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce([playlistFixture()[0]]);
    confirmDialogMock.mockResolvedValueOnce(false);

    render(Page);
    await fireEvent.click(await screen.findByTestId('delete-station'));

    // "1 playlist" — singular — not "1 playlists". Lives in body
    // since the title only carries the slug.
    const singOpts = confirmDialogMock.mock.calls[0][0] as unknown as { title: string; body: string };
    expect(singOpts.body).toMatch(/1 playlist[^s]/i);
  });

  it('station nuke: success navigates to landing', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(playlistFixture());
    deleteStationMock.mockResolvedValueOnce(undefined);
    confirmDialogMock.mockResolvedValueOnce(true);

    render(Page);
    await fireEvent.click(await screen.findByTestId('delete-station'));
    await act();

    expect(deleteStationMock).toHaveBeenCalledWith('cls');
    expect(gotoMock).toHaveBeenCalledWith('/');
  });

  it('generate buttons disable while a delete is in flight (S1 race fix)', async () => {
    // The §16.3 review caught a window where a user could click "Delete
    // station" then quickly click "Generate next week" before the delete
    // returned, racing the backend. The fix: every generate-related button
    // also disables on `deleting`. This test pins that contract — a
    // deferred-promise on deletePlaylist holds `deleting = true`, and we
    // assert generate-next is disabled before resolving.
    getStationMock.mockResolvedValueOnce(stubStation());
    // First call populates the page; second handles the post-delete refetch
    // once the deferred promise resolves below. Without the second value
    // the refetch returns undefined, the `playlists` array reactive crashes
    // on `playlists.length` and the test produces an unhandled rejection.
    getStationPlaylistsMock
      .mockResolvedValueOnce(playlistFixture())
      .mockResolvedValueOnce([playlistFixture()[1]]);

    let resolveDelete!: () => void;
    deletePlaylistMock.mockImplementationOnce(
      () => new Promise<void>((res) => { resolveDelete = res; }),
    );
    confirmDialogMock.mockResolvedValueOnce(true);

    render(Page);
    await fireEvent.click(await screen.findByTestId('delete-row-5'));
    // `deleting = true` is set synchronously in deleteRow() before the await.
    // Generate-next should be disabled now even though the DELETE is still
    // pending. Same for the advanced-form submit.
    expect(screen.getByTestId('generate-next')).toBeDisabled();
    expect(screen.getByTestId('advanced-submit')).toBeDisabled();

    // Drain the pending delete so afterEach's act() flush doesn't hang.
    resolveDelete();
  });

  it('station nuke: 5xx renders inline, no navigation', async () => {
    getStationMock.mockResolvedValueOnce(stubStation());
    getStationPlaylistsMock.mockResolvedValueOnce(playlistFixture());
    deleteStationMock.mockRejectedValueOnce(
      new FakeApiError(500, '500 Internal Server Error: db locked'),
    );
    confirmDialogMock.mockResolvedValueOnce(true);

    render(Page);
    await fireEvent.click(await screen.findByTestId('delete-station'));
    await act();

    expect(await screen.findByTestId('delete-error')).toHaveTextContent(/db locked/i);
    expect(gotoMock).not.toHaveBeenCalled();
  });
});


// ---- §16.19 station overview sections -----------------------------------

describe('station detail — §16.19 overview', () => {
  beforeEach(() => {
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationOverviewMock.mockReset();
    panelListSourcesMock.mockReset();
    panelUploadSourceMock.mockReset();
    panelImportByPathMock.mockReset();
    panelDeleteSourceMock.mockReset();
    gotoMock.mockReset();
    confirmDialogMock.mockReset();
    confirmDialogMock.mockResolvedValue(true);
    setSlug('cls');
    // Core page deps — the overview is additive on top.
    getStationMock.mockResolvedValue({
      id: 1, slug: 'cls', name: 'Classics', template: { rng: 'off' },
    });
    getStationPlaylistsMock.mockResolvedValue([]);
    // Defaults for the embedded <PoolImportPanel> — no-op resolutions
    // keep the panel from polluting unrelated tests; individual tests
    // override these when they need to drive panel behaviour.
    panelListSourcesMock.mockResolvedValue([]);
    panelDeleteSourceMock.mockResolvedValue(undefined);
  });

  const overview = (over = {}) => ({
    slug: 'cls', name: 'Classics', cadence: 'weekly', rng: 'off',
    updated_at: '2026-05-29T12:00:00+00:00',
    template_summary: {
      slot_count: 3010, layout_form: 'row_template',
      strategy_counts: { window: 2 }, cursor_persist: false, source_count: 3,
    },
    pools: [
      { name: 'RG_RADIO', role: 'content', imported: true, track_count: 500, imported_at: '2026-05-01T00:00:00+00:00' },
      { name: 'RG_MISSING', role: 'content', imported: false, track_count: null, imported_at: null },
    ],
    cursors: [],
    generations: { count: 5, last_generated_week: 5, last_generated_at: '2026-05-20T00:00:00+00:00', stale_count: 0 },
    ...over,
  });

  it('renders template summary + pools with the not-imported gap flagged', async () => {
    getStationOverviewMock.mockResolvedValue(overview());
    render(Page);

    const ts = await screen.findByTestId('template-summary');
    expect(ts).toHaveTextContent('3,010');
    expect(ts).toHaveTextContent('row_template');
    expect(ts).toHaveTextContent(/window ×2/);

    expect(screen.getByTestId('pools-summary')).toHaveTextContent('RG_RADIO');
    expect(screen.getByTestId('pools-summary')).toHaveTextContent('500 tracks');
    // The un-imported source surfaces the setup gap.
    expect(screen.getByTestId('pool-missing')).toHaveTextContent('not imported');
  });

  it('§16.25 c5: "not imported" badge is a deep-link to /sources?pool=…&role=…', async () => {
    getStationOverviewMock.mockResolvedValue(overview());
    render(Page);

    const badge = await screen.findByTestId('pool-missing');
    // It's an anchor (not a span) — so the user can click through.
    expect(badge.tagName).toBe('A');
    const href = badge.getAttribute('href');
    expect(href).toBe('/sources?pool=RG_MISSING&role=content');
  });

  it('§16.25 c5: embeds <PoolImportPanel> scoped to this station', async () => {
    getStationOverviewMock.mockResolvedValue(overview());
    render(Page);

    // The embed section appears below the overview.
    expect(await screen.findByTestId('overview-pool-import')).toBeInTheDocument();
    // The panel itself renders inside.
    expect(screen.getByTestId('pool-import-panel')).toBeInTheDocument();
  });

  it('§16.25 c5 review 🟡-1: panel-driven delete re-fetches the station overview', async () => {
    // Initial overview returns 1 imported pool, second returns 0 (after
    // the user deletes via the embedded panel). The bug this locks:
    // before the fix, the §16.19 pools card would stay stale showing
    // the deleted pool until a hard reload.
    getStationOverviewMock
      .mockResolvedValueOnce(overview({
        pools: [
          { name: 'RG_RADIO', role: 'content', imported: true, track_count: 500, imported_at: '2026-05-01T00:00:00+00:00' },
        ],
      }))
      .mockResolvedValueOnce(overview({
        pools: [
          { name: 'RG_RADIO', role: 'content', imported: false, track_count: null, imported_at: null },
        ],
      }));
    // The panel's listSources returns the same pool so it has a row
    // to delete. After the delete, an empty list is fine — we're not
    // asserting list state, only that the overview re-fetches.
    panelListSourcesMock
      .mockResolvedValueOnce([
        { id: 1, name: 'RG_RADIO', role: 'content', track_count: 500 },
      ])
      .mockResolvedValueOnce([]);
    panelDeleteSourceMock.mockResolvedValue(undefined);
    confirmDialogMock.mockResolvedValueOnce(true);

    render(Page);

    // Wait for both initial fetches (overview + panel.listSources).
    await screen.findByTestId('overview-pool-import');
    await screen.findByTestId('pool-row');
    expect(getStationOverviewMock).toHaveBeenCalledTimes(1);

    // Click delete inside the panel, confirm.
    const deleteBtn = screen.getByTestId('pool-row-delete');
    await fireEvent.click(deleteBtn);

    // The panel's deleteSource fires → refresh fires → dispatch('changed')
    // → station page's refreshOverview fires → getStationOverview called again.
    await vi.waitFor(() => {
      expect(getStationOverviewMock).toHaveBeenCalledTimes(2);
    });
    expect(panelDeleteSourceMock).toHaveBeenCalledWith(1);

  });

  it('shows the cursors card only for persistent templates', async () => {
    // Stateless → no cursors card.
    getStationOverviewMock.mockResolvedValue(overview());
    const { unmount } = render(Page);
    await screen.findByTestId('template-summary');
    expect(screen.queryByTestId('cursors-summary')).toBeNull();
    unmount();

    // Persistent → cursors card with the row.
    getStationOverviewMock.mockResolvedValue(overview({
      template_summary: {
        slot_count: 100, layout_form: 'row_template',
        strategy_counts: { sequential: 1 }, cursor_persist: true, source_count: 1,
      },
      cursors: [{ name: 'NIGHT_1', position: 42 }],
    }));
    render(Page);
    const card = await screen.findByTestId('cursors-summary');
    expect(card).toHaveTextContent('NIGHT_1');
    expect(card).toHaveTextContent('42');
  });

  it('renders the staleness hint only when stale_count > 0', async () => {
    getStationOverviewMock.mockResolvedValue(overview({
      generations: { count: 5, last_generated_week: 5, last_generated_at: 'x', stale_count: 3 },
    }));
    render(Page);
    const hint = await screen.findByTestId('stale-hint');
    expect(hint).toHaveTextContent(/3 playlists were generated under a previous template/i);
  });

  it('omits the staleness hint when stale_count is 0', async () => {
    getStationOverviewMock.mockResolvedValue(overview());
    render(Page);
    await screen.findByTestId('generations-summary');
    expect(screen.queryByTestId('stale-hint')).toBeNull();
  });

  it('omits the whole overview when the fetch fails (page core survives)', async () => {
    getStationOverviewMock.mockRejectedValue(new FakeApiError(500, 'boom'));
    render(Page);
    // Core page still renders (the Playlists heading is always present).
    await screen.findByText('Classics');
    expect(screen.queryByTestId('overview')).toBeNull();
  });
});


// §16.2.8 — Fork action.
describe('station detail +page.svelte — §16.2.8 fork action', () => {
  beforeEach(() => {
    getStationMock.mockReset();
    getStationPlaylistsMock.mockReset();
    getStationOverviewMock.mockReset();
    startGenerateJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    deletePlaylistMock.mockReset();
    deleteStationMock.mockReset();
    forkStationMock.mockReset();
    gotoMock.mockReset();
    confirmDialogMock.mockReset();
    confirmDialogMock.mockResolvedValue(true);
    setSlug('cls');
    getStationMock.mockResolvedValue({
      id: 1, slug: 'cls', name: 'Classics', template: { station: 'cls' },
    });
    getStationPlaylistsMock.mockResolvedValue([]);
    getStationOverviewMock.mockRejectedValue(new FakeApiError(500, 'overview unused in this block'));
  });

  it('Fork button is present + opens the inline form on click; form defaults derive from the source', async () => {
    render(Page);
    await screen.findByText('Classics');

    const forkBtn = screen.getByTestId('fork-station');
    expect(forkBtn).toBeInTheDocument();
    // Form not rendered until clicked.
    expect(screen.queryByTestId('fork-form')).toBeNull();

    await act(async () => { await fireEvent.click(forkBtn); });

    expect(screen.getByTestId('fork-form')).toBeInTheDocument();
    expect((screen.getByTestId('fork-new-slug') as HTMLInputElement).value).toBe('cls-fork');
    expect((screen.getByTestId('fork-new-name') as HTMLInputElement).value).toBe('Classics (fork)');
  });

  it('Cancel hides the form without firing forkStation', async () => {
    render(Page);
    await screen.findByText('Classics');
    await act(async () => { await fireEvent.click(screen.getByTestId('fork-station')); });
    expect(screen.getByTestId('fork-form')).toBeInTheDocument();

    await act(async () => { await fireEvent.click(screen.getByTestId('fork-cancel')); });
    expect(screen.queryByTestId('fork-form')).toBeNull();
    expect(forkStationMock).not.toHaveBeenCalled();
  });

  it('Submit calls forkStation + navigates to the new station on success', async () => {
    forkStationMock.mockResolvedValue({
      id: 2, slug: 'cls-fork', name: 'Classics (fork)', template: { station: 'cls' },
    });
    render(Page);
    await screen.findByText('Classics');
    await act(async () => { await fireEvent.click(screen.getByTestId('fork-station')); });

    await act(async () => { await fireEvent.click(screen.getByTestId('fork-submit')); });

    expect(forkStationMock).toHaveBeenCalledWith('cls', {
      new_slug: 'cls-fork', new_name: 'Classics (fork)',
    });
    expect(gotoMock).toHaveBeenCalledWith('/stations/cls-fork');
  });

  it('Surfaces ApiError (e.g. 409) inline in the fork-error banner; form stays open', async () => {
    forkStationMock.mockRejectedValue(new FakeApiError(409, "station 'cls-fork' already exists"));
    render(Page);
    await screen.findByText('Classics');
    await act(async () => { await fireEvent.click(screen.getByTestId('fork-station')); });
    await act(async () => { await fireEvent.click(screen.getByTestId('fork-submit')); });

    expect(screen.getByTestId('fork-error')).toHaveTextContent('already exists');
    // Form stays open so operator can edit the slug + retry.
    expect(screen.getByTestId('fork-form')).toBeInTheDocument();
    expect(gotoMock).not.toHaveBeenCalled();
  });
});
