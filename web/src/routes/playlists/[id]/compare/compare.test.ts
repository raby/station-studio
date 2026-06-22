import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

/**
 * §16.4.t1 — side-by-side trace-version compare view.
 *
 * Page-level integration tests covering:
 *   * Loading branch + populated render.
 *   * Default-derivation when ?from / ?to are absent (replaces URL).
 *   * Honouring explicit ?from / ?to.
 *   * Single-version playlist → "compare requires two versions".
 *   * `from == to` → friendly inline error.
 *   * "Show unchanged" toggle flips filter.
 *   * Picker change calls goto.
 */

const {
  getVersionsMock,
  getTraceDiffMock,
  gotoMock,
  FakeApiError,
  pageStore,
  setId,
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
  let currentId: string | undefined = '7';
  let currentSearch = '';
  interface PageValue {
    params: { id: string | undefined };
    url: URL;
  }
  const buildValue = (): PageValue => ({
    params: { id: currentId },
    url: new URL(
      `http://localhost/playlists/${currentId ?? ''}/compare${currentSearch}`,
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
  const setId = (s: string | undefined) => { currentId = s; notify(); };
  const setSearch = (s: string) => { currentSearch = s; notify(); };
  return {
    getVersionsMock: vi.fn(),
    getTraceDiffMock: vi.fn(),
    gotoMock: vi.fn(),
    FakeApiError,
    pageStore,
    setId,
    setSearch,
  };
});

vi.mock('$lib/api', () => ({
  getVersions: (...args: unknown[]) => getVersionsMock(...args),
  getTraceDiff: (...args: unknown[]) => getTraceDiffMock(...args),
  ApiError: FakeApiError,
}));

vi.mock('$app/stores', () => ({
  page: pageStore,
}));

vi.mock('$app/navigation', () => ({
  goto: (...args: unknown[]) => gotoMock(...args),
}));

import Page from './+page.svelte';

// ---- fixtures ------------------------------------------------------------

const threeVersions = () => ({
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

const singleVersion = () => ({
  playlist_id: 7,
  current_v: 1,
  versions: [
    {
      version_no: 1, trace_id: 'trace-v1',
      generated_at: '2026-05-01T12:00:00Z', seed: 100,
    },
  ],
});

function _side(
  slot: number, source: string, track: string, pool_index: number,
  track_id: number,
) {
  return {
    slot, unit: 'Mon', row: 1, block: 1, source,
    strategy: 'window',
    pool_index, track_id, track_name: track,
    track_artist: 'A', reason: 'r',
  };
}

const diffResponse = () => ({
  playlist_id: 7,
  from: { version_no: 2, trace_id: 'trace-v2', generated_at: '2026-05-02T12:00:00Z' },
  to: { version_no: 3, trace_id: 'trace-v3', generated_at: '2026-05-03T12:00:00Z' },
  summary: { total_slots: 3, changed: 2, unchanged: 1 },
  slots: [
    {
      slot: 1, unit: 'Mon', row: 1, block: 1, changed: true,
      from: _side(1, 'A', 'Old Track', 5, 100),
      to:   _side(1, 'A', 'New Track', 8, 200),
    },
    {
      slot: 2, unit: 'Mon', row: 1, block: 1, changed: false,
      from: _side(2, 'A', 'Same', 6, 101),
      to:   _side(2, 'A', 'Same', 6, 101),
    },
    {
      slot: 3, unit: 'Mon', row: 1, block: 1, changed: true,
      from: _side(3, 'B', 'Old B', 1, 999),
      to:   _side(3, 'B', 'New B', 2, 888),
    },
  ],
});

// ---- tests ---------------------------------------------------------------

describe('compare +page.svelte — §16.4.t1 trace diff', () => {
  beforeEach(() => {
    getVersionsMock.mockReset();
    getTraceDiffMock.mockReset();
    gotoMock.mockReset();
    setId('7');
    setSearch('');
  });

  it('shows "Loading…" while versions+diff fetches are in flight', () => {
    getVersionsMock.mockReturnValueOnce(new Promise(() => {}));
    render(Page);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('with explicit ?from + ?to, fetches the diff + renders the table', async () => {
    setSearch('?from=2&to=3');
    getVersionsMock.mockResolvedValueOnce(threeVersions());
    getTraceDiffMock.mockResolvedValueOnce(diffResponse());
    render(Page);
    await screen.findByTestId('diff-table');
    // Both picker values reflect the URL.
    expect((screen.getByTestId('picker-from') as HTMLSelectElement).value).toBe('2');
    expect((screen.getByTestId('picker-to') as HTMLSelectElement).value).toBe('3');
    // Summary count. Whitespace + newlines vary between renderers;
    // normalize before the regex.
    const summary = (screen.getByTestId('summary').textContent ?? '')
      .replace(/\s+/g, ' ');
    expect(summary).toMatch(/2 of 3 slots changed/);
    // The diff fetch was called with the URL params.
    expect(getTraceDiffMock).toHaveBeenCalledWith(7, 2, 3);
  });

  it('hides unchanged rows by default; only changed rows render', async () => {
    setSearch('?from=2&to=3');
    getVersionsMock.mockResolvedValueOnce(threeVersions());
    getTraceDiffMock.mockResolvedValueOnce(diffResponse());
    render(Page);
    await screen.findByTestId('diff-table');
    const rows = screen.getAllByTestId('diff-row');
    // 2 changed rows visible; 1 unchanged hidden.
    expect(rows.length).toBe(2);
    for (const r of rows) {
      expect(r.getAttribute('data-changed')).toBe('true');
    }
  });

  it('"Show unchanged" toggle reveals the unchanged row', async () => {
    setSearch('?from=2&to=3');
    getVersionsMock.mockResolvedValueOnce(threeVersions());
    getTraceDiffMock.mockResolvedValueOnce(diffResponse());
    render(Page);
    await screen.findByTestId('diff-table');
    const toggle = screen
      .getByTestId('show-all-toggle')
      .querySelector('input') as HTMLInputElement;
    await act(async () => {
      await fireEvent.click(toggle);
    });
    expect(screen.getAllByTestId('diff-row')).toHaveLength(3);
  });

  it('default-derives from=current-1, to=current when params are absent', async () => {
    // No ?from / ?to. Versions = 1,2,3 with current=3 → defaults to from=2, to=3.
    getVersionsMock.mockResolvedValueOnce(threeVersions());
    render(Page);
    // The page calls goto(?from=2&to=3) instead of fetching the diff
    // directly — the reactive watcher then re-fires with the URL
    // populated. We assert on the goto call.
    await act(async () => {});
    expect(gotoMock).toHaveBeenCalledTimes(1);
    const arg = gotoMock.mock.calls[0][0] as string;
    expect(arg).toContain('from=2');
    expect(arg).toContain('to=3');
  });

  it('shows a friendly error for a single-version playlist', async () => {
    getVersionsMock.mockResolvedValueOnce(singleVersion());
    render(Page);
    expect(
      (await screen.findByTestId('compare-error')).textContent,
    ).toMatch(/at least two versions/i);
    // No diff fetch attempted.
    expect(getTraceDiffMock).not.toHaveBeenCalled();
  });

  it('shows an inline error when from == to', async () => {
    setSearch('?from=2&to=2');
    getVersionsMock.mockResolvedValueOnce(threeVersions());
    render(Page);
    expect(
      (await screen.findByTestId('compare-error')).textContent,
    ).toMatch(/same version/i);
    expect(getTraceDiffMock).not.toHaveBeenCalled();
  });

  it('changing the From picker calls goto with the new ?from', async () => {
    setSearch('?from=2&to=3');
    getVersionsMock.mockResolvedValueOnce(threeVersions());
    getTraceDiffMock.mockResolvedValueOnce(diffResponse());
    render(Page);
    await screen.findByTestId('diff-table');
    const select = screen.getByTestId('picker-from') as HTMLSelectElement;
    await act(async () => {
      select.value = '1';
      await fireEvent.change(select);
    });
    expect(gotoMock).toHaveBeenCalled();
    const arg = gotoMock.mock.calls[0][0] as string;
    expect(arg).toContain('from=1');
    // `to` preserved.
    expect(arg).toContain('to=3');
  });

  it('shows "no changes" empty state when summary.changed === 0', async () => {
    setSearch('?from=2&to=3');
    getVersionsMock.mockResolvedValueOnce(threeVersions());
    const allSame = diffResponse();
    allSame.summary = { total_slots: 3, changed: 0, unchanged: 3 };
    for (const r of allSame.slots) r.changed = false;
    getTraceDiffMock.mockResolvedValueOnce(allSame);
    render(Page);
    await screen.findByTestId('empty-state');
    expect(screen.getByTestId('empty-state').textContent).toMatch(/no changes/i);
  });
});
