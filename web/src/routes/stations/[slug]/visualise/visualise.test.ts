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
  patchBlockMock,
  startPreviewJobMock,
  watchJobMock,
  cancelJobMock,
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
    // §16.2.6 c2: preview transport mocks. Default impls return
    // empty/no-op so the existing visualise tests (which don't
    // care about preview) don't have to set them up.
    startPreviewJobMock: vi.fn(async () => ({
      job_id: 'pv-default', status: 'running',
    })),
    watchJobMock: vi.fn(),
    cancelJobMock: vi.fn(async () => {}),
    // §16.2.6 c3 review #1: patchBlock spy for the staging-flow
    // integration test. Default no-op succeeds so a Save replay
    // through it doesn't 4xx.
    patchBlockMock: vi.fn(async () => undefined),
    FakeApiError,
    pageStore,
    setSlug,
  };
});


vi.mock('$lib/api', () => ({
  // Mock surface widened for §16.2.6 c2 preview transport. Pass
  // the hoisted vi.fn instances directly — per-test mock
  // configuration (mockReturnValueOnce, mockResolvedValueOnce)
  // flows through. The signature-mismatch errors that bit the
  // wrapped-args form (`(...args: unknown[]) => mock(...args)`)
  // don't apply because vi.fn's polymorphic type satisfies any
  // function position TS expects.
  getStationSidecar: getStationSidecarMock,
  startPreviewJob: startPreviewJobMock,
  watchJob: watchJobMock,
  cancelJob: cancelJobMock,
  // The other api exports the page imports (createSection,
  // patchBlock, etc.) aren't exercised by these tests; default
  // them to no-ops so the page mounts cleanly.
  createSection: vi.fn(),
  deleteSection: vi.fn(),
  exportStationTemplate: vi.fn(),
  moveBlock: vi.fn(),
  patchBlock: patchBlockMock,
  patchMacro: vi.fn(),
  renameSection: vi.fn(),
  reorderSections: vi.fn(),
  retypeBlock: vi.fn(),
  structuralBlockEdit: vi.fn(),
  ApiError: FakeApiError,
  BlockPatchConflict: class BlockPatchConflict extends FakeApiError {},
}));


vi.mock('$app/stores', () => ({
  page: pageStore,
}));

// §16.2.6 c3d — EditorCanvas stub. Closes c3c review #1 finding #4:
// the page's handler intercepts (handleSave / handleMacroSave / etc.)
// can be tested by replacing EditorCanvas with the stub + driving
// its `dispatch` via the test helper below.
vi.mock(
  '$lib/components/editor/EditorCanvas.svelte',
  () => import('./__test-stubs/EditorCanvasStub.svelte'),
);

/** Test helper — fires a Svelte event on the mocked EditorCanvas.
 *  Returns the same value `dispatch` returns (`true` if a listener
 *  consumed the event). Throws if the stub hasn't mounted yet. */
function fireEditorCanvasEvent(
  kind: string,
  detail: unknown,
): boolean {
  const w = window as unknown as {
    __editorCanvasStubDispatch?: (k: string, d: unknown) => boolean;
  };
  if (!w.__editorCanvasStubDispatch) {
    throw new Error(
      'EditorCanvas stub not mounted — render(Page) + await the stub element before firing',
    );
  }
  return w.__editorCanvasStubDispatch(kind, detail);
}


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
    // No warning banner on the happy path.
    expect(screen.queryByTestId('sidecar-warning')).toBeNull();
    // (Chip rendering inside EditorCanvas is no longer asserted
    // here because §16.2.6 c3d mocked EditorCanvas with a stub for
    // the handler-intercept tests below. EditorCanvas's internal
    // composition is covered by its own component test file.)
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


// §16.2.6 c2 review #1 batch B — page-level tests for the live-
// preview wiring: toggle + debounce + scheduler + localStorage.
//
// These pin the contracts the commit message claimed but didn't
// land: toggle-OFF baseline (preserves §16.2.5 behavior — no
// startPreviewJob ever called), toggle-ON triggers preview after
// debounce, localStorage round-trip per station.

function snapsAsAsyncIterable(snaps: unknown[]): AsyncIterable<unknown> {
  return {
    async *[Symbol.asyncIterator]() {
      for (const s of snaps) yield s;
    },
  };
}

function fakeDoneTrace(slotCount: number = 2) {
  return {
    job_id: 'pv-1',
    slug: 'viz-test',
    kind: 'preview',
    status: 'done',
    phase: 'done',
    current: slotCount,
    total: slotCount,
    label: null,
    error: null,
    started_at: '2026-06-15T12:00:00Z',
    finished_at: '2026-06-15T12:00:01Z',
    result: {
      trace: {
        trace_id: 'pv-trace',
        station: 'viz-test',
        week: 1,
        rng: 'off',
        seed: null,
        generated_at: '2026-06-15T12:00:00Z',
        slots: Array.from({ length: slotCount }, (_, i) => ({
          slot: i + 1, unit: 'slot', row: 1, block: i + 1,
          source: 'A', strategy: 'pick', pool_index: i + 1,
          track_id: 100 + i, track_name: `T${i + 1}`,
          track_artist: null, reason: '', excluded: [],
        })),
      },
      slot_count: slotCount,
    },
  };
}


describe('visualise +page.svelte — §16.2.6 c2 live-preview wiring', () => {
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    startPreviewJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    startPreviewJobMock.mockResolvedValue({
      job_id: 'pv-default', status: 'running',
    });
    cancelJobMock.mockResolvedValue(undefined);
    setSlug('viz-test');
    // Wipe localStorage between cases so the toggle state is
    // deterministic.
    try { window.localStorage.clear(); } catch { /* */ }
  });

  it('toggle defaults OFF — no preview POST fires on mount + no TracePane renders (preserves §16.2.5 behavior)', async () => {
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    render(Page);
    // Page mounts.
    expect(await screen.findByTestId('editor-canvas')).toBeInTheDocument();
    // Toggle is visible + unchecked.
    const cb = await screen.findByTestId('live-preview-checkbox') as HTMLInputElement;
    expect(cb.checked).toBe(false);
    // No preview pane.
    expect(screen.queryByTestId('trace-pane')).not.toBeInTheDocument();
    // No preview POST.
    expect(startPreviewJobMock).not.toHaveBeenCalled();
  });

  it('hydrates toggle from localStorage on mount (per-station scoped key)', async () => {
    // Pre-seed the toggle's localStorage as "on" for this slug.
    window.localStorage.setItem('livePreview:viz-test', 'on');
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    // Provide a watchJob response so the auto-mount preview can
    // resolve cleanly without timing out.
    watchJobMock.mockReturnValue(snapsAsAsyncIterable([fakeDoneTrace()]));

    render(Page);
    const cb = await screen.findByTestId('live-preview-checkbox') as HTMLInputElement;
    expect(cb.checked).toBe(true);
    // The trace pane renders even before any preview has resolved
    // (in the empty-state branch). Confirms the layout flipped.
    expect(await screen.findByTestId('trace-pane')).toBeInTheDocument();
  });

  it('toggle ON writes "on" to localStorage for the current slug', async () => {
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    watchJobMock.mockReturnValue(snapsAsAsyncIterable([fakeDoneTrace()]));
    render(Page);

    const cb = await screen.findByTestId('live-preview-checkbox') as HTMLInputElement;
    expect(window.localStorage.getItem('livePreview:viz-test')).toBeNull();
    await act(async () => {
      cb.click();
    });
    expect(window.localStorage.getItem('livePreview:viz-test')).toBe('on');
    expect(cb.checked).toBe(true);
  });

  it('toggle OFF clears the trace + writes "off" to localStorage', async () => {
    window.localStorage.setItem('livePreview:viz-test', 'on');
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    watchJobMock.mockReturnValue(snapsAsAsyncIterable([fakeDoneTrace()]));
    render(Page);

    const cb = await screen.findByTestId('live-preview-checkbox') as HTMLInputElement;
    expect(cb.checked).toBe(true);
    await act(async () => {
      cb.click();
    });
    expect(cb.checked).toBe(false);
    expect(window.localStorage.getItem('livePreview:viz-test')).toBe('off');
    // Pane disappears.
    expect(screen.queryByTestId('trace-pane')).not.toBeInTheDocument();
  });
});


// §16.2.6 c3 — staging mode: toggle ON intercepts chip edits into
// the opLog instead of immediately PATCHing. Save / Discard
// affordances appear when opLog is non-empty. These tests cover
// the toggle-OFF passthrough (existing §16.2.5 behaviour) + the
// toggle-ON interception + Save/Discard visibility.

describe('visualise +page.svelte — §16.2.6 c3 staging mode', () => {
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    startPreviewJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    startPreviewJobMock.mockResolvedValue({
      job_id: 'pv-default', status: 'running',
    });
    cancelJobMock.mockResolvedValue(undefined);
    setSlug('viz-test');
    try { window.localStorage.clear(); } catch { /* */ }
  });

  it('Save / Discard buttons are hidden when opLog is empty (toggle ON, no edits)', async () => {
    window.localStorage.setItem('livePreview:viz-test', 'on');
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    watchJobMock.mockReturnValue(snapsAsAsyncIterable([fakeDoneTrace()]));
    render(Page);

    // Toggle is on but no staged edits exist yet — buttons absent.
    await screen.findByTestId('live-preview-checkbox');
    expect(screen.queryByTestId('staged-save')).not.toBeInTheDocument();
    expect(screen.queryByTestId('staged-discard')).not.toBeInTheDocument();
  });

  it('Save / Discard buttons are hidden when toggle is OFF (preserves §16.2.5 baseline)', async () => {
    // Toggle defaults OFF — no staging UX visible regardless.
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    render(Page);

    await screen.findByTestId('editor-canvas');
    expect(screen.queryByTestId('staged-save')).not.toBeInTheDocument();
    expect(screen.queryByTestId('staged-discard')).not.toBeInTheDocument();
  });

  // Full staging-flow integration test (toggle ON + simulated chip
  // save → opLog grows → Save replays → opLog clears) requires
  // mocking EditorCanvas as a test stub that exposes a way to fire
  // its `save` event programmatically. Svelte 4's
  // createEventDispatcher delivers events through component-instance
  // callbacks ($on/$dispatch), NOT through native DOM dispatch — so
  // a synthetic `el.dispatchEvent(new CustomEvent('save'))` on the
  // editor-canvas root does NOT reach `on:save={handleSave}`.
  //
  // Two options for c3b:
  //   (a) `vi.mock` EditorCanvas with a stub that renders a
  //       test-only "fire save" button + lets the test set the
  //       payload via a hoisted store.
  //   (b) Render a tiny <EditorCanvasFake> wrapper test that drives
  //       the page via the actual DetailPanel render path (real
  //       components, real interaction — but requires more setup +
  //       Section/Chip rendering).
  //
  // c3b's coverage extension will pick one. Until then the saveError
  // bug fix (c3 review #1 batch A) is verified manually + the type-
  // guard / button-visibility tests pin everything observable from
  // outside the page's event handlers.
});

// Re-import `act` for the cases above. Done at the bottom so the
// existing tests' imports (line 2) stay untouched.
import { act } from '@testing-library/svelte';


// §16.2.6 c3d — handler-intercept coverage. Closes c3c review #1
// finding #4: the staging-mode intercepts in handleSave /
// handleMacroSave / handleSectionRename / handleSectionCreate /
// handleSectionDelete / handleSectionsReorder / handleBlockMove /
// handleRetype had zero unit-test coverage because Svelte 4's
// createEventDispatcher delivers component events through
// component-instance callbacks ($on/$dispatch), not native DOM
// dispatch — so a synthetic CustomEvent on the editor-canvas root
// didn't reach the on:save={handleSave} listener. The
// EditorCanvasStub (mocked at module-mock time at top of file)
// gives tests a direct handle to the dispatcher.
describe('visualise +page.svelte — §16.2.6 c3d handler intercepts (toggle ON → opLog queues)', () => {
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    patchBlockMock.mockReset();
    startPreviewJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    startPreviewJobMock.mockResolvedValue({
      job_id: 'pv-default', status: 'running',
    });
    cancelJobMock.mockResolvedValue(undefined);
    setSlug('viz-test');
    try { window.localStorage.clear(); } catch { /* */ }
    // Seed the toggle ON so every test's intercept path fires.
    window.localStorage.setItem('livePreview:viz-test', 'on');
    // Default sidecar response.
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    // Stub a successful preview reply so the rAF cancel doesn't
    // blow up the test (the page schedules preview after each
    // intercept).
    watchJobMock.mockReturnValue({
      [Symbol.asyncIterator]() {
        return { async next() { return { done: true, value: undefined }; } };
      },
    });
  });

  /** Mount the page + wait until the EditorCanvas stub has registered
   *  the global dispatch hook. */
  async function mountAndAwaitStub() {
    render(Page);
    await screen.findByTestId('editor-canvas');
    // Tick once so the onMount completes + dispatch is registered.
    await act(async () => {});
  }

  it('patchBlock — save event → opLog grows, staged-save shows "1 edit", no immediate PATCH', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    expect(patchBlockMock).not.toHaveBeenCalled();
  });

  it('patchMacro — macroSave event → opLog grows', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('macroSave', {
        macroId: 'm1',
        fields: { strategy: 'window' },
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('renameSection — sectionRename event → opLog grows', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('sectionRename', {
        sectionId: 'all-slots',
        name: 'Morning',
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('sectionCreate — sectionCreate event → opLog grows', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('sectionCreate', {
        id: 'evening',
        name: 'Evening',
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('sectionDelete — sectionDelete event → opLog grows', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('sectionDelete', {
        sectionId: 'all-slots',
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('reorderSections — sectionsReorder event → opLog grows', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('sectionsReorder', {
        layoutOrder: ['all-slots'],
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('moveBlock — blockMove event → opLog grows (§16.2.6 c3d)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('blockMove', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        targetSectionId: 'all-slots',
        targetIndex: 0,
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('retypeBlock — retype event → opLog grows (§16.2.6 c3d)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('retype', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        newStrategy: 'window',
        newSource: null,
        newFields: { window: 4 },
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('structuralBlockEdit — structuralEdit event → opLog grows (§16.2.6 c3d-follow-up; 9th + final kind)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('structuralEdit', {
        edit: { op: 'size', slot_index: 0, new_size: 5 },
      });
    });
    expect(await screen.findByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('multiple intercepts accumulate in opLog (count rises with each event)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    await act(async () => {
      fireEditorCanvasEvent('sectionRename', {
        sectionId: 'all-slots',
        name: 'Morning',
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 2 edits/);
  });
});


// §16.2.6 c3e c1 — guard against silent loss of staged edits:
//   (a) Toggling live-preview OFF with staged edits prompts the
//       operator; confirm clears opLog, cancel keeps the toggle ON.
//   (b) `beforeunload` calls preventDefault when staged edits exist
//       so the browser shows its native "leave site?" prompt.
describe('visualise +page.svelte — §16.2.6 c3e c1 staged-edit loss guards', () => {
  let confirmSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    patchBlockMock.mockReset();
    startPreviewJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    startPreviewJobMock.mockResolvedValue({
      job_id: 'pv-default', status: 'running',
    });
    cancelJobMock.mockResolvedValue(undefined);
    setSlug('viz-test');
    try { window.localStorage.clear(); } catch { /* */ }
    window.localStorage.setItem('livePreview:viz-test', 'on');
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    watchJobMock.mockReturnValue({
      [Symbol.asyncIterator]() {
        return { async next() { return { done: true, value: undefined }; } };
      },
    });
    confirmSpy = vi.spyOn(window, 'confirm');
  });

  async function mountAndAwaitStub() {
    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});
  }

  it('toggle OFF with no staged edits: no prompt, toggle flips silently', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      screen.getByTestId('live-preview-checkbox').click();
    });
    // Toggle went OFF without ever calling confirm — there's nothing
    // to warn about.
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it('toggle OFF with staged edits: confirm() called; on accept, opLog cleared + toggle flips', async () => {
    await mountAndAwaitStub();
    // Queue one staged edit.
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    // Accept the discard prompt.
    confirmSpy.mockReturnValue(true);
    await act(async () => {
      screen.getByTestId('live-preview-checkbox').click();
    });
    // Prompt fired with a copy that mentions the count.
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0][0]).toMatch(/1 staged edit/);
    // opLog cleared (staged-save chip gone).
    expect(screen.queryByTestId('staged-save')).toBeNull();
  });

  it('toggle OFF with staged edits: on cancel, toggle stays ON + opLog preserved', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    // Decline.
    confirmSpy.mockReturnValue(false);
    await act(async () => {
      screen.getByTestId('live-preview-checkbox').click();
    });
    // confirm() fired on the click (may fire again as Svelte's
    // `checked={livePreviewOn}` reverts the DOM after our cancel
    // path skipped updating the reactive var — the count is jsdom-
    // dependent, the user-visible behavior is just "prompted, said
    // no, nothing happened").
    expect(confirmSpy).toHaveBeenCalled();
    // opLog still there.
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('c3e c1 review G1: toggle OFF with staged edits + accept fetches a fresh sidecar (mirrors Discard)', async () => {
    // The shared `_resetStagedState` helper reloads the sidecar after
    // clearing opLog so the displayed canvas reflects fresh server
    // state. Pin that the toggle-off-confirm path calls
    // getStationSidecar a second time (initial mount + reset).
    await mountAndAwaitStub();
    expect(getStationSidecarMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    confirmSpy.mockReturnValue(true);
    await act(async () => {
      screen.getByTestId('live-preview-checkbox').click();
    });
    // Second fetch fired by _resetStagedState's loadSidecar call.
    expect(getStationSidecarMock).toHaveBeenCalledTimes(2);
  });

  it('c3e c2 D2(b): TracePane visible when livePreviewOn + DetailPanel closed', async () => {
    await mountAndAwaitStub();
    const trace = screen.getByTestId('trace-col');
    expect(trace).toBeInTheDocument();
    expect(trace.className).not.toContain('trace-col--hidden');
  });

  it('c3e c2 D2(b): detailOpen=true hides TracePane via CSS (DetailPanel claims the right-side real estate; review H4 — no remount)', async () => {
    await mountAndAwaitStub();
    expect(screen.getByTestId('trace-col')).toBeInTheDocument();
    await act(async () => {
      fireEditorCanvasEvent('detailOpen', { open: true });
    });
    // Element stays in DOM (H4 — no remount on every open/close);
    // hidden via class instead.
    const trace = screen.getByTestId('trace-col');
    expect(trace).toBeInTheDocument();
    expect(trace.className).toContain('trace-col--hidden');
  });

  it('c3e c2 D2(b): detailOpen=false restores TracePane (close DetailPanel → trace reappears)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('detailOpen', { open: true });
    });
    expect(screen.getByTestId('trace-col').className).toContain('trace-col--hidden');
    await act(async () => {
      fireEditorCanvasEvent('detailOpen', { open: false });
    });
    expect(screen.getByTestId('trace-col').className).not.toContain('trace-col--hidden');
  });

  it('c3e c2 review H2: livePreviewOn=false → TracePane absent regardless of detailOpen (toggle hide overrides)', async () => {
    // Pin the `livePreviewOn && !detailPanelOpen` gate's livePreviewOn
    // half. A refactor that dropped the toggle check would silently
    // start showing TracePane when live-preview is OFF.
    window.localStorage.setItem('livePreview:viz-test', 'off');
    await mountAndAwaitStub();
    expect(screen.queryByTestId('trace-col')).toBeNull();
    // Even if EditorCanvas fires detailOpen events, no trace appears.
    await act(async () => {
      fireEditorCanvasEvent('detailOpen', { open: false });
    });
    expect(screen.queryByTestId('trace-col')).toBeNull();
  });

  it('c3e c2 review H5: opening DetailPanel does NOT cancel the in-flight preview job', async () => {
    // The H4 comment promises TracePane reactively updates while
    // hidden so the closing transition shows a fresh trace. That
    // depends on _cancelPreview NOT firing on detailOpen=true. Pin
    // it by spying on cancelJobMock across the open transition.
    await mountAndAwaitStub();
    // Trigger an edit so a preview job actually starts (the
    // _schedulePreview path runs after every intercept).
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    cancelJobMock.mockClear();
    // Open DetailPanel.
    await act(async () => {
      fireEditorCanvasEvent('detailOpen', { open: true });
    });
    // No cancel call.
    expect(cancelJobMock).not.toHaveBeenCalled();
  });

  it('beforeunload with no staged edits: preventDefault NOT called (no warning)', async () => {
    await mountAndAwaitStub();
    const e = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent;
    const pd = vi.spyOn(e, 'preventDefault');
    window.dispatchEvent(e);
    expect(pd).not.toHaveBeenCalled();
  });

  it('beforeunload with staged edits: handler sets BOTH preventDefault (modern) AND returnValue (legacy cross-browser belt)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    const e = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent;
    const pd = vi.spyOn(e, 'preventDefault');
    // c3e c1 review G4: pin BOTH signals.
    // - `preventDefault` is the modern signal (covered by `pd` spy).
    // - `e.returnValue = ''` is the legacy historical signal old
    //   Safari + Firefox still need for the leave-site dialog.
    //   jsdom maps generic Event's `returnValue` to the
    //   !defaultPrevented legacy semantic — once `preventDefault()`
    //   runs, jsdom sets returnValue to `false` (boolean), so
    //   asserting `e.returnValue === ''` is unreliable. Spy on the
    //   property SETTER instead so we pin that the handler tried
    //   to write `''`, regardless of how jsdom stores it.
    const returnValueSetter = vi.fn();
    Object.defineProperty(e, 'returnValue', {
      get: () => '',
      set: returnValueSetter,
      configurable: true,
    });
    window.dispatchEvent(e);
    expect(pd).toHaveBeenCalled();
    expect(returnValueSetter).toHaveBeenCalledWith('');
  });
});
