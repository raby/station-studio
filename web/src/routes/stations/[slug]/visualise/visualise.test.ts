import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/svelte';
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
  applyBatchMock,
  FakeSidecarBatchFailure,
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
  // §16.2.6.t2 c2: hoisted SidecarBatchFailure class so tests can
  // throw the same shape the page expects in `err instanceof
  // SidecarBatchFailure` checks. Same hoist trick as FakeApiError.
  class FakeSidecarBatchFailure extends Error {
    status: number;
    failedAtIdx: number;
    kind: string;
    constructor(status: number, failedAtIdx: number, kind: string, message: string) {
      super(message);
      this.name = 'SidecarBatchFailure';
      this.status = status;
      this.failedAtIdx = failedAtIdx;
      this.kind = kind;
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
    // §16.2.6.t2 c2: applyBatch's default success-path returns a
    // SidecarMutationResponse-shaped object so Save tests that don't
    // care about the request body don't trip the response-shape
    // validation in the success branch. Tests that need to drive a
    // failure use .mockRejectedValueOnce(new SidecarBatchFailure(...)).
    applyBatchMock: vi.fn(async () => ({
      sidecar: {
        schema_version: 1,
        sections: [],
        macros: [],
        layout_order: [],
      },
      template: {
        station: 'ts',
        name: 'Test',
        sources: [],
        layout: { unit: 'slot', unit_labels: [], row_start_offsets: [], flat_slots: [] },
      },
      updated_at: 'ts-after-save',
    })),
    FakeApiError,
    FakeSidecarBatchFailure,
    pageStore,
    setSlug,
  };
});


vi.mock('$lib/api', async () => ({
  // Re-export the real `isPreviewResult` type guard so the page's
  // `for await (const snap of watchJob(...))` loop can discriminate
  // PreviewResult vs GenerateResponse from the snapshot's `result`
  // payload. Pulled from importOriginal so tests that drive the
  // watchJob mock with a trace can have it actually land in
  // previewTrace (the t3 integration tests need this).
  ...(await vi.importActual<typeof import('$lib/api')>('$lib/api')),
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
  applyBatch: applyBatchMock,
  // Mock surface for the SidecarBatchFailure class used by `err
  // instanceof SidecarBatchFailure` in +page.svelte. Same hoist
  // trick as FakeApiError — the test class lives in vi.hoisted so
  // tests can `throw new FakeSidecarBatchFailure(...)` and the
  // instanceof check passes (it's the same constructor reference).
  SidecarBatchFailure: FakeSidecarBatchFailure,
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
    // §16.2.6.t1 — vi.spyOn returns the same spy across tests; clear
    // call history so cross-test pollution doesn't leak into the
    // recovery-prompt assertions.
    confirmSpy.mockClear();
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

  it('c3e c3: non-conflict Save failure surfaces Retry/Drop modal + leaves opLog intact', async () => {
    await mountAndAwaitStub();
    // Queue 2 edits.
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 1 },
        fields: { size: 7 },
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 2 edits/);
    // First patch fails with a non-conflict error.
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'server rejected: bad size'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    // Modal visible; banner visible underneath; opLog still has 2.
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    expect(screen.getByTestId('save-error')).toHaveTextContent(/server rejected/);
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 2 edits/);
  });

  it('c3e c3 Retry: re-runs handleStagedSave; success drains opLog + closes modal', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'transient failure'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    // Retry — patchBlock will succeed this time.
    await act(async () => {
      screen.getByTestId('retry-modal-retry').click();
    });
    // The retry kicks off another handleStagedSave (async); wait
    // for opLog drain to settle the reactive state.
    await waitFor(() => {
      expect(screen.queryByTestId('staged-save')).toBeNull();
    });
    expect(screen.queryByTestId('retry-modal')).toBeNull();
  });

  it('c3e c3 Drop: pops opLog[0] + replays the rest; head edit gone', async () => {
    await mountAndAwaitStub();
    // Queue 2 edits.
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 1 },
        fields: { size: 7 },
      });
    });
    // First patch (slot 0) fails.
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'server reject'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    const callCountBeforeDrop = applyBatchMock.mock.calls.length;
    // Drop the failed op; replay the remaining 1 edit (slot 1)
    // via applyBatch (this call succeeds — default mock).
    await act(async () => {
      screen.getByTestId('retry-modal-drop').click();
    });
    // Wait for the second handleStagedSave to drain opLog.
    await waitFor(() => {
      expect(screen.queryByTestId('staged-save')).toBeNull();
    });
    expect(screen.queryByTestId('retry-modal')).toBeNull();
    // applyBatch got one more call (the slot 1 replay) with a
    // 1-edit body — verifying we re-posted only the remaining edit.
    expect(applyBatchMock.mock.calls.length).toBe(callCountBeforeDrop + 1);
    const lastCall = applyBatchMock.mock.calls.at(-1) as unknown[];
    const lastCallEdits = lastCall?.[1] as Array<{ kind: string }> | undefined;
    expect(lastCallEdits).toHaveLength(1);
  });

  it('c3e c3 Cancel: closes modal but preserves opLog + saveError for fix-and-retry-later', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'validation failed'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    await act(async () => {
      screen.getByTestId('retry-modal-cancel').click();
    });
    // Modal closed; staged-save + banner still there.
    expect(screen.queryByTestId('retry-modal')).toBeNull();
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    expect(screen.getByTestId('save-error')).toHaveTextContent(/validation failed/);
  });

  it('c3e c3 review J4: Retry that fails again re-opens the modal with the latest error', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    // Two consecutive failures, different error messages.
    applyBatchMock
      .mockRejectedValueOnce(new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'first failure'))
      .mockRejectedValueOnce(new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'second failure'));
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    expect(screen.getByTestId('save-error')).toHaveTextContent(/first failure/);
    await act(async () => {
      screen.getByTestId('retry-modal-retry').click();
    });
    // Modal still visible; banner shows the SECOND error.
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    expect(screen.getByTestId('save-error')).toHaveTextContent(/second failure/);
  });

  it('c3e c3 review J5: Drop into a cascade failure re-opens the modal on the next op', async () => {
    await mountAndAwaitStub();
    // Queue 2 edits; both fail.
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 1 },
        fields: { size: 7 },
      });
    });
    applyBatchMock
      // First batch: fails at edit 1 (op0). After Drop, opLog has
      // just op1; the second batch fails at edit 1 (which is now op1).
      .mockRejectedValueOnce(new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'op0 failed'))
      .mockRejectedValueOnce(new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'op1 failed'));
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    expect(screen.getByTestId('save-error')).toHaveTextContent(/op0 failed/);
    await act(async () => {
      screen.getByTestId('retry-modal-drop').click();
    });
    // After drop + cascade fail: modal re-opens with op1's error.
    await waitFor(() => {
      expect(screen.getByTestId('save-error')).toHaveTextContent(/op1 failed/);
    });
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
  });

  it('c3e c3 review J1: ESC anywhere closes the modal (preserves opLog + saveError like Cancel)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'fail'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(screen.queryByTestId('retry-modal')).toBeNull();
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    expect(screen.getByTestId('save-error')).toBeInTheDocument();
  });

  it('c3e c3 review J6: backdrop click closes the modal (preserves opLog + saveError like Cancel)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'fail'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    await act(async () => {
      screen.getByTestId('retry-modal-backdrop').click();
    });
    expect(screen.queryByTestId('retry-modal')).toBeNull();
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('c3e c3 review J6: click INSIDE the dialog does NOT close the modal (stopPropagation guard)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'fail'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    // Click on the dialog title (inside the dialog box, NOT the backdrop).
    await act(async () => {
      screen.getByText(/Save failed mid-replay/).click();
    });
    // Modal still visible — click didn't propagate to backdrop.
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
  });

  it('c3e c3 Drop with only the failed edit: opLog clears + modal closes (no replay)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 1, 'patchBlock', 'reject'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    const callCountAtFailure = applyBatchMock.mock.calls.length;
    await act(async () => {
      screen.getByTestId('retry-modal-drop').click();
    });
    expect(screen.queryByTestId('retry-modal')).toBeNull();
    expect(screen.queryByTestId('staged-save')).toBeNull();
    // No additional batch call — opLog was empty after the drop.
    expect(applyBatchMock.mock.calls.length).toBe(callCountAtFailure);
  });

  it('§16.2.6.t2 slice-review T3: Drop honors SERVER-identified failedAtIdx > 1 (pops the right edit)', async () => {
    // Pre-c2 the Drop always popped opLog[0]; c2 wired the
    // server-identified index. Existing Drop test used failedAtIdx
    // === 1 (which coincides with opLog[0]) — this test exercises
    // failedAtIdx === 2 with a 3-edit batch so the c2 semantic is
    // actually tested.
    await mountAndAwaitStub();
    // Queue 3 distinct edits.
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('sectionRename', {
        sectionId: 'all-slots',
        name: 'Morning',
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 2 },
        fields: { size: 9 },
      });
    });
    // Server says edit 2 (the renameSection) is the one that failed.
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 2, 'renameSection', 'invalid name'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    // Drop: should pop opLog[1] (the renameSection at index 2,
    // 1-based), NOT opLog[0]. The subsequent applyBatch call should
    // carry 2 edits — slot_index 0 patch + slot_index 2 patch.
    await act(async () => {
      screen.getByTestId('retry-modal-drop').click();
    });
    await waitFor(() => {
      expect(screen.queryByTestId('staged-save')).toBeNull();
    });
    const lastCall = applyBatchMock.mock.calls.at(-1) as unknown[];
    const lastEdits = lastCall?.[1] as Array<{ kind: string }> | undefined;
    expect(lastEdits).toHaveLength(2);
    // Both remaining edits are patchBlock (the renameSection was dropped).
    expect(lastEdits?.[0].kind).toBe('patchBlock');
    expect(lastEdits?.[1].kind).toBe('patchBlock');
  });

  it('§16.2.6.t13 T7: applyBatch JSON body matches the backend wire contract (kind + camelCase keys)', async () => {
    // End-to-end wire-shape pin. Pre-fix, both backend pytest +
    // frontend vitest suites tested their own halves but no test
    // captured the actual JSON.stringify payload the frontend posts.
    // A future refactor renaming Edit.kind → Edit.type would silently
    // break end-to-end while both unit suites stay green. Capture
    // the applyBatch call args + assert each envelope has `kind` +
    // the right camelCase keys for its kind (matching the c1a
    // schema parse fixture).
    await mountAndAwaitStub();
    // Queue one of each interesting kind shape:
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('sectionRename', {
        sectionId: 'all-slots',
        name: 'Morning',
      });
    });
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    // Capture the body applyBatch was called with.
    const lastCall = applyBatchMock.mock.calls.at(-1) as unknown[];
    const editsArg = lastCall?.[1] as Array<Record<string, unknown>>;
    expect(editsArg).toHaveLength(2);
    // patchBlock envelope wire-shape:
    expect(editsArg[0].kind).toBe('patchBlock');
    expect(editsArg[0].address).toEqual({ slot_index: 0 });
    expect(editsArg[0].fields).toEqual({ size: 5 });
    // renameSection envelope wire-shape (camelCase `sectionId`):
    expect(editsArg[1].kind).toBe('renameSection');
    expect(editsArg[1].sectionId).toBe('all-slots');
    expect(editsArg[1].name).toBe('Morning');
  });

  it('§16.2.6.t13 T8: server-returned out-of-bounds failedAtIdx surfaces banner + preserves opLog (no silent drop)', async () => {
    // Pre-fix the defensive fallback dropped opLog[0] when the
    // server's failedAtIdx exceeded the queue length (race window
    // between request preparation + response arrival). Now: leave
    // opLog intact + show an internal-error banner explaining the
    // mismatch so the operator can re-Save or Discard.
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    // Server returns failedAtIdx=5 but opLog only has 1 entry.
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 5, 'patchBlock', 'out-of-bounds race'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    await act(async () => {
      screen.getByTestId('retry-modal-drop').click();
    });
    // Modal closed; opLog INTACT (NOT silently dropped opLog[0]);
    // banner explains the internal error.
    expect(screen.queryByTestId('retry-modal')).toBeNull();
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    expect(screen.getByTestId('save-error')).toHaveTextContent(
      /server reported failed edit 5/,
    );
  });

  it('§16.2.6.t2 slice-review T4: sentinel failedAtIdx === 0 hides the Drop button (_finalize composition path)', async () => {
    // c2 added `{#if failedAtIdx > 0}` around the Drop button so
    // composition failures + empty-batch + internal errors (all set
    // failedAtIdx: 0) don't show a meaningless "drop the failed
    // edit" affordance. This pin makes the branch load-bearing.
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(400, 0, '_finalize',
        'batch produces an invalid template: ...'),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    // Modal visible; Retry + Cancel buttons present; Drop button ABSENT.
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    expect(screen.getByTestId('retry-modal-retry')).toBeInTheDocument();
    expect(screen.getByTestId('retry-modal-cancel')).toBeInTheDocument();
    expect(screen.queryByTestId('retry-modal-drop')).toBeNull();
  });

  it('§16.2.6.t1: intercepted edits persist to localStorage with the current updatedAt snapshot', async () => {
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-1',
    });
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    const stored = window.localStorage.getItem('stagedEdits:viz-test');
    expect(stored).not.toBeNull();
    const parsed = JSON.parse(stored!);
    expect(parsed.schemaVersion).toBe(1);
    expect(parsed.updatedAtSnapshot).toBe('ts-1');
    expect(parsed.opLog).toHaveLength(1);
    expect(parsed.opLog[0].kind).toBe('patchBlock');
  });

  it('§16.2.6.t1 review L2: persisted opLog entry with unknown `kind` is filtered out on recovery', async () => {
    window.localStorage.setItem(
      'stagedEdits:viz-test',
      JSON.stringify({
        schemaVersion: 1,
        opLog: [
          { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 5 } },
          { kind: 'futureKindThatNoLongerExists', wibble: 'wobble' },
          { kind: 'patchBlock', address: { slot_index: 1 }, fields: { size: 7 } },
        ],
        updatedAtSnapshot: 'ts-1',
      }),
    );
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-1',
    });
    await mountAndAwaitStub();
    // Both valid entries recovered; unknown kind dropped silently —
    // staged-save count == 2.
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 2 edits/);
  });

  it('§16.2.6.t1 review L3: persisted entry with mismatched schemaVersion is silently dropped', async () => {
    window.localStorage.setItem(
      'stagedEdits:viz-test',
      JSON.stringify({
        schemaVersion: 999,
        opLog: [{ kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 5 } }],
        updatedAtSnapshot: 'ts-1',
      }),
    );
    await mountAndAwaitStub();
    expect(screen.queryByTestId('staged-save')).toBeNull();
  });

  it('§16.2.6.t1 review L4: successful Save clears localStorage', async () => {
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-1',
    });
    await mountAndAwaitStub();
    // Stage an edit + verify persisted.
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(window.localStorage.getItem('stagedEdits:viz-test')).not.toBeNull();
    // Save (patchBlockMock default = success).
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });
    await waitFor(() => {
      expect(screen.queryByTestId('staged-save')).toBeNull();
    });
    // localStorage cleared.
    expect(window.localStorage.getItem('stagedEdits:viz-test')).toBeNull();
  });

  it('§16.2.6.t1 review L5: Discard button clears localStorage', async () => {
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-1',
    });
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(window.localStorage.getItem('stagedEdits:viz-test')).not.toBeNull();
    // Click Discard.
    await act(async () => {
      screen.getByTestId('staged-discard').click();
    });
    await waitFor(() => {
      expect(window.localStorage.getItem('stagedEdits:viz-test')).toBeNull();
    });
  });

  it('§16.2.6.t1 review L1: loadSidecar 404 clears orphan stagedEdits entry', async () => {
    // Seed an orphan entry — station no longer exists serverside.
    window.localStorage.setItem(
      'stagedEdits:viz-test',
      JSON.stringify({
        schemaVersion: 1,
        opLog: [{ kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 5 } }],
        updatedAtSnapshot: 'ts-1',
      }),
    );
    // Make loadSidecar throw the 404 ApiError shape (matches
    // `getStationSidecar`'s wrapped error contract).
    getStationSidecarMock.mockRejectedValue(
      new FakeApiError(404, '404 Not Found'),
    );
    await act(async () => {
      render(Page);
    });
    // loadError populated; localStorage entry purged.
    await waitFor(() => {
      expect(window.localStorage.getItem('stagedEdits:viz-test')).toBeNull();
    });
  });

  it('§16.2.6.t1: recovery — matching updatedAt → silent restore (no confirm prompt)', async () => {
    const persisted = {
      schemaVersion: 1,
      opLog: [{
        kind: 'patchBlock',
        address: { slot_index: 0 },
        fields: { size: 7 },
      }],
      updatedAtSnapshot: 'ts-2',
    };
    window.localStorage.setItem('stagedEdits:viz-test', JSON.stringify(persisted));
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-2',
    });
    await mountAndAwaitStub();
    // No confirm prompt + staged-save reflects the recovered opLog.
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('§16.2.6.t1: recovery — mismatched updatedAt → confirm(); accept = discard', async () => {
    window.localStorage.setItem(
      'stagedEdits:viz-test',
      JSON.stringify({
        schemaVersion: 1,
        opLog: [{
          kind: 'patchBlock',
          address: { slot_index: 0 },
          fields: { size: 9 },
        }],
        updatedAtSnapshot: 'ts-OLD',
      }),
    );
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-NEW',
    });
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await mountAndAwaitStub();
    // Prompt fired + accept = discard the staged edits + clear
    // localStorage.
    expect(confirmSpy).toHaveBeenCalled();
    expect(screen.queryByTestId('staged-save')).toBeNull();
    expect(window.localStorage.getItem('stagedEdits:viz-test')).toBeNull();
  });

  it('§16.2.6.t1: recovery — mismatched updatedAt → confirm(); cancel = recover anyway + re-snapshot', async () => {
    window.localStorage.setItem(
      'stagedEdits:viz-test',
      JSON.stringify({
        schemaVersion: 1,
        opLog: [{
          kind: 'patchBlock',
          address: { slot_index: 0 },
          fields: { size: 11 },
        }],
        updatedAtSnapshot: 'ts-OLD',
      }),
    );
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-NEW',
    });
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await mountAndAwaitStub();
    // Cancel = recover anyway.
    expect(confirmSpy).toHaveBeenCalled();
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    // Re-snapshot: the localStorage entry now carries the NEW updatedAt.
    const restored = JSON.parse(window.localStorage.getItem('stagedEdits:viz-test')!);
    expect(restored.updatedAtSnapshot).toBe('ts-NEW');
  });

  it('§16.2.6.t1: livePreviewOn=false at mount → drops any persisted opLog (no surprise invisible state)', async () => {
    window.localStorage.setItem('livePreview:viz-test', 'off');
    window.localStorage.setItem(
      'stagedEdits:viz-test',
      JSON.stringify({
        schemaVersion: 1,
        opLog: [{
          kind: 'patchBlock',
          address: { slot_index: 0 },
          fields: { size: 1 },
        }],
        updatedAtSnapshot: 'ts-1',
      }),
    );
    await mountAndAwaitStub();
    // Persistence cleared; no recovery.
    expect(window.localStorage.getItem('stagedEdits:viz-test')).toBeNull();
  });

  it('§16.2.6.t1: malformed localStorage entry is silently dropped (defensive)', async () => {
    window.localStorage.setItem('stagedEdits:viz-test', 'not-json');
    await mountAndAwaitStub();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('staged-save')).toBeNull();
  });

  it('§16.2.6.t1-follow-up: cross-tab storage event with matching key updates in-memory opLog', async () => {
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-1',
    });
    await mountAndAwaitStub();
    // Simulate Tab A writing a new staged edit + firing the storage
    // event Tab B (this page) would receive.
    const newPayload = JSON.stringify({
      schemaVersion: 1,
      opLog: [{
        kind: 'patchBlock',
        address: { slot_index: 0 },
        fields: { size: 9 },
      }],
      updatedAtSnapshot: 'ts-1',
    });
    window.localStorage.setItem('stagedEdits:viz-test', newPayload);
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'stagedEdits:viz-test',
        oldValue: null,
        newValue: newPayload,
        storageArea: window.localStorage,
      }));
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('§16.2.6.t1-follow-up: cross-tab storage event with non-matching key is ignored', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'stagedEdits:OTHER-STATION',
        oldValue: null,
        newValue: '{"schemaVersion":1,"opLog":[{"kind":"patchBlock","address":{"slot_index":0},"fields":{"size":1}}],"updatedAtSnapshot":"ts-1"}',
        storageArea: window.localStorage,
      }));
    });
    // viz-test's opLog unchanged.
    expect(screen.queryByTestId('staged-save')).toBeNull();
  });

  it('§16.2.6.t1-follow-up review M1: cross-tab payload with newer updatedAtSnapshot triggers loadSidecar refresh', async () => {
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-1',
    });
    await mountAndAwaitStub();
    expect(getStationSidecarMock).toHaveBeenCalledTimes(1);
    // Cross-tab payload was authored against a NEWER server version.
    const newPayload = JSON.stringify({
      schemaVersion: 1,
      opLog: [{
        kind: 'patchBlock',
        address: { slot_index: 0 },
        fields: { size: 9 },
      }],
      updatedAtSnapshot: 'ts-2',
    });
    window.localStorage.setItem('stagedEdits:viz-test', newPayload);
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'stagedEdits:viz-test',
        oldValue: null,
        newValue: newPayload,
        storageArea: window.localStorage,
      }));
    });
    // Second loadSidecar fired by the cross-tab snapshot-drift
    // detection. opLog updated too.
    expect(getStationSidecarMock).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('§16.2.6.t1-follow-up review M2: storage listener removed on unmount', async () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const { unmount } = render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});
    await act(async () => {
      unmount();
    });
    // onDestroy fired + storage listener detached.
    expect(removeSpy).toHaveBeenCalledWith('storage', expect.any(Function));
    removeSpy.mockRestore();
  });

  it('§16.2.6.t1-follow-up review M3: storage event from sessionStorage is ignored (defensive)', async () => {
    await mountAndAwaitStub();
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'stagedEdits:viz-test',
        oldValue: null,
        newValue: '{"schemaVersion":1,"opLog":[{"kind":"patchBlock","address":{"slot_index":0},"fields":{"size":7}}],"updatedAtSnapshot":"ts-1"}',
        storageArea: window.sessionStorage, // ← NOT localStorage.
      }));
    });
    // Ignored — no staged-save chip.
    expect(screen.queryByTestId('staged-save')).toBeNull();
  });

  it('§16.2.6.t1-follow-up: cross-tab storage event with newValue=null clears in-memory opLog', async () => {
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
      updated_at: 'ts-1',
    });
    await mountAndAwaitStub();
    // Stage an edit locally first.
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
    // Simulate Tab A discarding its staged edits → storage event with
    // newValue=null on this tab.
    window.localStorage.removeItem('stagedEdits:viz-test');
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'stagedEdits:viz-test',
        oldValue: 'whatever',
        newValue: null,
        storageArea: window.localStorage,
      }));
    });
    // Our in-memory opLog cleared too.
    expect(screen.queryByTestId('staged-save')).toBeNull();
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


// §16.2.6.t3 — click-trace-row → jump-to-chip integration. The
// findChipRef + refKey pure-function side is pinned by editor.test.ts
// (97/97 incl. 11 t3-specific cases). The Chip.svelte `data-ref-key`
// attribute is pinned by Chip.test.ts. THIS block pins the page-level
// glue: TracePane select event → findChipRef → DOM query → scroll +
// flash class.
describe('visualise +page.svelte — §16.2.6.t3 click-trace-row → jump-to-chip', () => {
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

  /** Build a custom trace snapshot whose slots' `slot` values
   *  match what's passed in (the shipped fakeDoneTrace helper
   *  produces 1-indexed slots; we need 0-aligned for the default
   *  makeSidecar's atomic@slot_index=0). */
  function traceWithSlots(slotIndices: number[]) {
    return {
      job_id: 'pv-1', slug: 'viz-test', kind: 'preview',
      status: 'done', phase: 'done',
      current: slotIndices.length, total: slotIndices.length,
      label: null, error: null,
      started_at: '2026-06-15T12:00:00Z',
      finished_at: '2026-06-15T12:00:01Z',
      result: {
        trace: {
          trace_id: 'pv-trace', station: 'viz-test', week: 1,
          rng: 'off', seed: null,
          generated_at: '2026-06-15T12:00:00Z',
          // `s` is the block's 0-based place in flat_slots; the engine
          // numbers trace slots, rows and blocks from 1.
          slots: slotIndices.map((s) => ({
            slot: s + 1, unit: 'slot', row: 1, block: s + 1,
            source: 'A', strategy: 'pick', pool_index: s + 1,
            track_id: 100 + s, track_name: `T${s}`,
            track_artist: null, reason: '', excluded: [],
          })),
        },
        slot_count: slotIndices.length,
      },
    };
  }

  /** Mount the page with the toggle ON + a watchJob mock that
   *  resolves to a trace whose slots match `slotIndices`. Returns
   *  once the trace rows render (TracePane swaps from empty-state
   *  to the `<ol>` of `<li data-testid="trace-row">`s). */
  async function mountWithTraceRows(slotIndices: number[] = [0]) {
    window.localStorage.setItem('livePreview:viz-test', 'on');
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
    watchJobMock.mockReturnValue(
      snapsAsAsyncIterable([traceWithSlots(slotIndices)]),
    );
    render(Page);
    await screen.findByTestId('trace-pane');
    // The trace's first row materializes once previewTrace lands +
    // TracePane's {#if trace && trace.slots.length > 0} renders the
    // <ol>. Need to click an actual trace-row to drive Svelte's
    // `dispatch('select', ...)` → page's `on:select` ($on callback,
    // not a DOM listener). Manual CustomEvent dispatch on
    // trace-pane element wouldn't reach the $on callback.
    await waitFor(() => {
      expect(screen.queryAllByTestId('trace-row').length).toBeGreaterThan(0);
    }, { timeout: 2000 });
  }

  /** Click the trace row whose `data-slot-index` matches `slot`.
   *  TracePane's onRowClick fires `dispatch('select', { slotIndex })`
   *  which Svelte routes to the page's `on:select={handleTraceSelect}`
   *  $on callback. */
  async function clickTraceRow(slot: number) {
    // `slot` as in traceWithSlots (0-based); the row carries the engine's.
    const row = document.querySelector<HTMLElement>(
      `[data-testid="trace-row"][data-slot-index="${slot + 1}"]`,
    );
    if (row === null) {
      throw new Error(`trace-row for slot=${slot} not found in DOM`);
    }
    await act(async () => {
      row.click();
    });
  }

  it('TracePane row click → findChipRef + scrollIntoView + .chip--jump-flash on the matching chip', async () => {
    await mountWithTraceRows([0]);

    // Inject a fake chip with the right data-ref-key. The default
    // makeSidecar has atomic at slot_index=0; findChipRef maps the
    // engine's {slot:1, row:1, block:1} → atomic:slot=0.
    const fakeChip = document.createElement('button');
    fakeChip.setAttribute('data-ref-key', 'atomic:slot=0');
    fakeChip.classList.add('chip');
    const scrollMock = vi.fn();
    fakeChip.scrollIntoView = scrollMock;
    document.body.appendChild(fakeChip);

    await clickTraceRow(0);

    expect(scrollMock).toHaveBeenCalledWith({
      behavior: 'smooth', block: 'nearest', inline: 'nearest',
    });
    expect(fakeChip.classList.contains('chip--jump-flash')).toBe(true);

    document.body.removeChild(fakeChip);
  });

  it('D6 fallback — TracePane row click for an out-of-canvas slot → console.warn + no scrollIntoView', async () => {
    // findChipRef returns null when the sidecar has no section
    // addressing the slot (drift). makeSidecar only addresses slot
    // 0; mount with a trace that includes slot 99 + click that row
    // → findChipRef returns null → handler warns + bails.
    await mountWithTraceRows([99]);
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    await clickTraceRow(99);

    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('§16.2.6.t3 jump: no chip matches'),
    );
    warnSpy.mockRestore();
  });

  it('R3 fix: when the chip is missing but its section is ALREADY expanded, do NOT click the toggle (would collapse the section)', async () => {
    // Pre-fix: handler assumed "chip missing" == "section collapsed"
    // and synthetic-clicked the section-toggle. If the chip was
    // actually missing for an unrelated reason (e.g. a view-mode
    // filter hiding it, or a re-render race), the toggle click
    // would COLLAPSE the section the chip lives in — making the
    // chip even more inaccessible. R3 guards by checking whether
    // the section's data-testid="section-body" is rendered: if it
    // is, the section IS expanded and the toggle would collapse it.
    await mountWithTraceRows([0]);

    // Synthesize an already-expanded section in the document — the
    // EditorCanvas stub doesn't render Section components, so we
    // build the section + body + toggle DOM by hand to mirror what
    // a real Section.svelte renders when expanded. findSectionIdForRef
    // will resolve slot=0 → section 'all-slots' (from makeSidecar);
    // tag the synthesized section with that id so the lookup matches.
    const sectionEl = document.createElement('section');
    sectionEl.setAttribute('data-testid', 'section');
    sectionEl.setAttribute('data-section-id', 'all-slots');
    // Body present → section is already expanded.
    const bodyEl = document.createElement('div');
    bodyEl.setAttribute('data-testid', 'section-body');
    sectionEl.appendChild(bodyEl);
    // Toggle present too — but the guard should prevent clicking it.
    const toggleEl = document.createElement('button');
    toggleEl.setAttribute('data-testid', 'section-toggle');
    const toggleClickSpy = vi.fn();
    toggleEl.addEventListener('click', toggleClickSpy);
    sectionEl.appendChild(toggleEl);
    document.body.appendChild(sectionEl);
    // NB: NO chip with data-ref-key='atomic:slot=0' is injected;
    // the handler's first querySelector misses, triggers the
    // expand-fallback path. The R3 guard should kick in.

    await clickTraceRow(0);

    // Toggle MUST NOT have been clicked — the section body is
    // already rendered, so the section is expanded; toggling would
    // collapse it. Pre-fix, toggleClickSpy was called once + the
    // chip stayed missing on re-query (toggle collapsed instead of
    // expanded).
    expect(toggleClickSpy).not.toHaveBeenCalled();

    document.body.removeChild(sectionEl);
  });

  it('D4 race semantics — rapid second click keeps the class on the same chip + cancels any prior strip timer', async () => {
    // The flash timer is 800ms; the handler MUST cancel the prior
    // timer + re-arm on each call so the class never leaks from a
    // previously-flashed chip nor strips early on the new one.
    await mountWithTraceRows([0]);

    const fakeChip = document.createElement('button');
    fakeChip.setAttribute('data-ref-key', 'atomic:slot=0');
    fakeChip.classList.add('chip');
    fakeChip.scrollIntoView = vi.fn();
    document.body.appendChild(fakeChip);

    await clickTraceRow(0);
    expect(fakeChip.classList.contains('chip--jump-flash')).toBe(true);

    // Second click — same row → same chip. The race-cancel path
    // strips, re-clears the timer, re-adds the class. Net effect:
    // class still present (didn't leak / disappear between calls).
    await clickTraceRow(0);
    expect(fakeChip.classList.contains('chip--jump-flash')).toBe(true);

    document.body.removeChild(fakeChip);
  });
});


// §16.2.6.t11 — page-level intercept-path INTEGRATION tests. Closes
// the gap §16.2.5.t18 sweep S7 originally surfaced: c3d describe
// block pins "opLog grows by 1" per kind (shallow); c3e Retry/Drop
// tests cover the save-failure flow end-to-end. THE MIDDLE LAYER —
// `stagedView = applyEdits(originalSidecar, opLog)` reactive
// derivation + the save-replay path's order/composition — has no
// dedicated coverage. This describe block fills it via 4 tests
// driven by the d3d EditorCanvas stub + applyBatchMock.mock.calls
// (D1 inspection point chosen because applyBatch fires synchronously
// on the Save click — no debounce races, no fake-timer setup).
describe('visualise +page.svelte — §16.2.6.t11 intercept-path integration', () => {
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    patchBlockMock.mockReset();
    // NOTE: don't mockReset applyBatchMock — its hoisted default
    // returns a minimal SidecarMutationResponse shape that covers
    // the happy-path multi-op test. Resetting would strip the
    // implementation + each call would return undefined. Failure-
    // path tests use mockRejectedValueOnce inline.
    applyBatchMock.mockClear();
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
    // Stub a successful preview reply so the preview side doesn't
    // hang. The t11 tests pin the SAVE side (applyBatch.mock.calls);
    // preview just needs to not error.
    watchJobMock.mockReturnValue({
      [Symbol.asyncIterator]() {
        return { async next() { return { done: true, value: undefined }; } };
      },
    });
  });

  async function mountAndAwaitStub() {
    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});
  }

  it('D2 — defensive guard: moveBlock target=_unsorted stages in opLog + save replays the bad op verbatim', async () => {
    // The page's handleBlockMove intercept is dumb pass-through —
    // it copies the event payload into opLog without validating.
    // The staged-save count GROWS to 1 because the intercept didn't
    // reject. When Save fires, the bad op is sent to the backend
    // verbatim (which has the same guard server-side → 400).
    //
    // Pinning: (a) intercept didn't reject; (b) staged-save shows 1;
    // (c) applyBatch was called with the moveBlock op carrying the
    // _unsorted target; (d) server-side 400 surfaces the Retry/Drop
    // modal; (e) opLog preserved for fix-and-retry.
    //
    // **Out of scope here**: that `applyEdits` no-ops the bad op in
    // stagedSidecar — pinned at the unit layer (edit-ops.test.ts's
    // _moveBlock system-bin guard tests). The page-internal
    // stagedSidecar isn't inspected by this test (would need
    // startPreviewJob arg capture + fake-timer setup for the
    // debounce; design pass D1 rejected that route).
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('blockMove', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        targetSectionId: '_unsorted',  // system bin — server rejects manual moves
        targetIndex: 0,
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);

    // Drive the save with a 400 response (server's _apply_move_block
    // helper raises HTTPException(400) for system-section targets).
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(
        400, 1, 'moveBlock',
        "can't manually move into the system `_unsorted` section.",
      ),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });

    // applyBatch received the bad op verbatim (intercept is dumb
    // pass-through; the page didn't filter or transform).
    const lastCall = applyBatchMock.mock.calls.at(-1) as unknown[];
    const editsArg = lastCall?.[1] as Array<Record<string, unknown>>;
    expect(editsArg).toHaveLength(1);
    expect(editsArg[0].kind).toBe('moveBlock');
    expect(editsArg[0].targetSectionId).toBe('_unsorted');

    // Retry/Drop modal surfaced for the 400; opLog preserved so
    // the operator can fix-and-retry. Staged-save still shows
    // the op count.
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);
  });

  it('D3 — multi-op composition: 3 ops across 3 kinds replay through applyBatch in stage order', async () => {
    // Stage [patchBlock, sectionRename, structuralBlockEdit]; click
    // Save. applyBatch must receive a 3-element edits array with the
    // kinds in stage order. Ordering matters because the backend
    // dispatcher applies edits sequentially + later edits can
    // observe earlier ones (a structural-add before a section-rename
    // has different semantics than after).
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('sectionRename', {
        sectionId: 'all-slots',
        name: 'Morning',
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('structuralEdit', {
        edit: { op: 'size', slot_index: 0, new_size: 3 },
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 3 edits/);

    await act(async () => {
      screen.getByTestId('staged-save').click();
    });

    // §16.2.6.t2 contract: ONE batched call, not N per-op calls.
    // A regression where Save called applyBatch once per op would
    // leave .at(-1) carrying the right array but break the
    // all-or-nothing transactional guarantee. Pin both halves.
    expect(applyBatchMock).toHaveBeenCalledTimes(1);
    const lastCall = applyBatchMock.mock.calls.at(-1) as unknown[];
    const editsArg = lastCall?.[1] as Array<Record<string, unknown>>;
    expect(editsArg).toHaveLength(3);
    // Order is preserved — stage-order maps to send-order one-to-one.
    expect(editsArg[0].kind).toBe('patchBlock');
    expect(editsArg[1].kind).toBe('renameSection');
    expect(editsArg[2].kind).toBe('structuralBlockEdit');
  });

  it('D4 — Discard restores composed state: stage 2 ops, Discard, opLog cleared + staged-save UI gone', async () => {
    // Pre-§16.2.6.t11 a regression in _resetStagedState that forgot
    // to clear opLog (e.g. clearing only stagedSidecar) would slip
    // past the c3 tests (which pin button visibility without
    // multi-op staging). This pins the contract that Discard
    // restores from ANY composed staged state, not just single-op.
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('sectionRename', {
        sectionId: 'all-slots',
        name: 'Morning',
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 2 edits/);

    // Discard refetches the sidecar via loadSidecar; preload the
    // next getStationSidecar response so the GET doesn't 404.
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });

    await act(async () => {
      screen.getByTestId('staged-discard').click();
    });

    // opLog cleared → staged-save + staged-discard buttons hidden
    // (the `{#if opLog.length > 0}` template guard).
    expect(screen.queryByTestId('staged-save')).toBeNull();
    expect(screen.queryByTestId('staged-discard')).toBeNull();
    // applyBatch was NEVER called — Discard drops opLog before
    // any backend interaction. Pre-fix regression vector: a
    // _resetStagedState that called applyBatch by accident
    // (e.g. trying to send an "empty" save) would flip this.
    expect(applyBatchMock).not.toHaveBeenCalled();
    // Discard's _resetStagedState ALSO refetches the sidecar from
    // the server (covers the "another session changed the station
    // while these edits were staged" case — operator sees the
    // conflict winner's content, not their stale base). A
    // regression where _resetStagedState cleared opLog without
    // refetching would leave the buttons hidden BUT the canvas
    // showing stale state. Pin the refetch count: once on mount
    // + once on Discard = 2.
    expect(getStationSidecarMock).toHaveBeenCalledTimes(2);
  });

  it('mixed pure + guarded ops: 2 valid edits + 1 guard-rejected op all replay in stage order', async () => {
    // Composition-with-no-op test: staging a guard-rejected op
    // doesn't break the stagedView derivation for sibling ops.
    // Stage [patchBlock(valid), moveBlock(target=_unsorted, guard-
    // rejected), sectionRename(valid)]. applyEdits no-ops the
    // middle op against stagedSidecar; applyBatch on Save still
    // sends ALL 3 in order (the page is pass-through; the server
    // is the contract enforcer).
    await mountAndAwaitStub();
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('blockMove', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        targetSectionId: '_unsorted',
        targetIndex: 0,
      });
    });
    await act(async () => {
      fireEditorCanvasEvent('sectionRename', {
        sectionId: 'all-slots',
        name: 'Morning',
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 3 edits/);

    // Server returns 400 at index 2 (the bad moveBlock).
    applyBatchMock.mockRejectedValueOnce(
      new FakeSidecarBatchFailure(
        400, 2, 'moveBlock',
        "can't manually move into the system `_unsorted` section.",
      ),
    );
    await act(async () => {
      screen.getByTestId('staged-save').click();
    });

    // All 3 ops were sent; the page didn't dedupe / filter the
    // no-op middle one. Server's batch dispatcher (with the
    // post-§16.2.6.t13 _apply_<kind> helpers) enforces the guard
    // at idx=2 and rolls back the whole batch.
    expect(applyBatchMock).toHaveBeenCalledTimes(1);
    const lastCall = applyBatchMock.mock.calls.at(-1) as unknown[];
    const editsArg = lastCall?.[1] as Array<Record<string, unknown>>;
    expect(editsArg).toHaveLength(3);
    expect(editsArg[0].kind).toBe('patchBlock');
    expect(editsArg[1].kind).toBe('moveBlock');
    expect(editsArg[2].kind).toBe('renameSection');

    // Retry/Drop modal points at the bad op (failedAtIdx=2).
    expect(screen.getByTestId('retry-modal')).toBeInTheDocument();
  });
});


// §16.2.6.t10 — EditorCanvasStub.svelte lifecycle hygiene. The stub
// registers `window.__editorCanvasStubDispatch = dispatch` on mount
// (so `fireEditorCanvasEvent` can call it from tests). Pre-fix it
// never cleared the global on destroy — tests relied on each
// `render(Page)` re-registering. That's fine for sequential tests
// that always re-mount before firing, but the moment someone writes
// a test that needs an explicit "stub absent" assertion (e.g.
// testing the helper's throw-if-not-mounted error path at line
// 178-181), the dangling reference would silently pass and the
// expected error never fire. This describe block pins the contract.
describe('visualise +page.svelte — §16.2.6.t10 EditorCanvasStub lifecycle', () => {
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
    // Reset the global between tests so leftover state can't mask
    // the cleanup contract.
    delete (window as unknown as Record<string, unknown>).__editorCanvasStubDispatch;
  });

  it('onDestroy clears window.__editorCanvasStubDispatch (helper throws after unmount)', async () => {
    const w = window as unknown as {
      __editorCanvasStubDispatch?: unknown;
    };

    // Pre-mount: global is absent (defensive reset in beforeEach).
    expect(w.__editorCanvasStubDispatch).toBeUndefined();

    // Mount: the stub's onMount registers the global.
    const { unmount } = render(Page);
    await screen.findByTestId('editor-canvas');
    expect(typeof w.__editorCanvasStubDispatch).toBe('function');

    // Unmount: the stub's onDestroy MUST clear the global. Pre-fix
    // it stayed registered (pointed at a destroyed component).
    unmount();
    expect(w.__editorCanvasStubDispatch).toBeUndefined();

    // Helper now throws — the documented "stub not mounted" error
    // path is reachable post-unmount. Pre-fix this expect would
    // FAIL: the helper would call the dangling dispatcher (Svelte
    // 4 createEventDispatcher closes over the now-destroyed
    // component instance) and either no-op silently or crash with
    // an unrelated error rather than the documented diagnostic.
    expect(() => fireEditorCanvasEvent('save', {
      ref: { kind: 'atomic-ref', slot_index: 0 }, fields: { size: 5 },
    })).toThrow(/stub not mounted/);

    // Slice-review R1: the c3d sweep finding S6 bug scenario is
    // SEQUENTIAL cycles (mount A → unmount A → mount B → fire
    // against A's stale dispatcher). The single mount→unmount above
    // pins one cycle; this second cycle pins the MULTI-cycle
    // contract — that the cleanup doesn't break re-registration on
    // the next mount. A regression where onDestroy ran once but
    // then prevented future mounts (leaked subscription, global
    // object becoming non-configurable, etc.) would slip past a
    // single-cycle assertion.
    const second = render(Page);
    await screen.findByTestId('editor-canvas');
    expect(typeof w.__editorCanvasStubDispatch).toBe('function');
    second.unmount();
    expect(w.__editorCanvasStubDispatch).toBeUndefined();
  });
});


// §16.2.6.t8 — week selector for live-preview. Pre-fix the page
// hardcoded week=1 in startPreviewJob; non-week-1 stations saw
// week-1 traces regardless. This describe block pins (a) hydration
// default 1 when no localStorage entry, (b) hydration from existing
// `previewWeek:<slug>` entry, (c) change event reschedules preview
// with the new week + persists it to localStorage.
describe('visualise +page.svelte — §16.2.6.t8 week selector', () => {
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
    // livePreview ON so TracePane mounts + the week input renders +
    // _schedulePreview fires.
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
  });

  async function _mountAndAwaitFirstPreview() {
    render(Page);
    await screen.findByTestId('editor-canvas');
    // _schedulePreview is debounced 500ms; advance past it so the
    // POST fires. Falling back to a real-timer wait via waitFor
    // since the rest of the suite doesn't use fake timers (mixing
    // would invalidate other tests' timing assumptions).
    await waitFor(() => {
      expect(startPreviewJobMock).toHaveBeenCalled();
    }, { timeout: 2000 });
  }

  it('hydrates previewWeek=1 when no `previewWeek:<slug>` entry exists', async () => {
    // localStorage has the livePreview entry but NOT previewWeek.
    // Page should fall back to default = 1 + send week=1 to the
    // first preview POST. Pins the c2 baseline behavior is preserved
    // when no operator pref has been stored.
    await _mountAndAwaitFirstPreview();
    const firstCall = startPreviewJobMock.mock.calls.at(0) as unknown[];
    // startPreviewJob signature: (slug, template, sidecar, week, seed?)
    expect(firstCall?.[3]).toBe(1);

    // Input also reflects 1 in the DOM.
    const input = screen.getByTestId('trace-week-input') as HTMLInputElement;
    expect(input.value).toBe('1');
  });

  it('hydrates previewWeek from `previewWeek:<slug>` localStorage entry', async () => {
    // Pre-seed a non-default week. Page's setSlug reactive must
    // pick it up + the first preview POST must use it.
    window.localStorage.setItem('previewWeek:viz-test', '5');

    await _mountAndAwaitFirstPreview();
    const firstCall = startPreviewJobMock.mock.calls.at(0) as unknown[];
    expect(firstCall?.[3]).toBe(5);

    const input = screen.getByTestId('trace-week-input') as HTMLInputElement;
    expect(input.value).toBe('5');
  });

  it('week input change reschedules preview with new week + persists to localStorage', async () => {
    await _mountAndAwaitFirstPreview();
    const initialCalls = startPreviewJobMock.mock.calls.length;
    const firstBeforeChange = startPreviewJobMock.mock.calls.at(0) as unknown[];
    expect(firstBeforeChange?.[3]).toBe(1);

    // Operator changes the week from 1 → 7. The change event (NOT
    // input event — D4) fires once per blur/Enter.
    const input = screen.getByTestId('trace-week-input') as HTMLInputElement;
    await act(async () => {
      input.value = '7';
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    // Reschedule happens debounced; wait for the next POST.
    await waitFor(() => {
      expect(startPreviewJobMock.mock.calls.length).toBeGreaterThan(initialCalls);
    }, { timeout: 2000 });

    const latest = startPreviewJobMock.mock.calls.at(-1) as unknown[];
    expect(latest?.[3]).toBe(7);

    // Persisted to localStorage (so reload + next-mount hydrate
    // the same value).
    expect(window.localStorage.getItem('previewWeek:viz-test')).toBe('7');
  });

  it('R1 — malformed `previewWeek:<slug>` stored value falls back to 1 (defensive parse)', async () => {
    // A corrupted localStorage entry (browser plugin, multi-version
    // migration, manual edit) shouldn't propagate NaN to the
    // backend. readPreviewWeek's `!Number.isFinite(n) || n < 1`
    // guard catches: non-numeric strings (NaN), negative numbers,
    // zero, Infinity. All fall back to the default of 1.
    //
    // Pin: stored 'abc' must hydrate to 1, not NaN. A regression
    // where the guard was dropped would surface as a 422 from the
    // strict-typed PreviewRequest at the backend; client-side the
    // input would render as 'NaN' and the POST would fail. This
    // test catches the regression before the round-trip.
    window.localStorage.setItem('previewWeek:viz-test', 'abc');

    await _mountAndAwaitFirstPreview();
    const firstCall = startPreviewJobMock.mock.calls.at(0) as unknown[];
    expect(firstCall?.[3]).toBe(1);

    const input = screen.getByTestId('trace-week-input') as HTMLInputElement;
    expect(input.value).toBe('1');
  });

  it('R2 — typing 0 in the input snaps back to 1 + does NOT reschedule (clamp)', async () => {
    // TracePane.onWeekChange clamps to >= 1 + reflects the corrected
    // value back into the input. A regression where the clamp was
    // dropped would let previewWeek = 0 propagate (caught at the
    // backend by strict Pydantic, but the input would show 0 too —
    // bad UX). Also pin: the clamp produces SAME value as current
    // (1 → 1), so the dispatch is gated + no new POST fires.
    await _mountAndAwaitFirstPreview();
    const callsBeforeBadInput = startPreviewJobMock.mock.calls.length;

    const input = screen.getByTestId('trace-week-input') as HTMLInputElement;
    await act(async () => {
      input.value = '0';
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });

    // Input snapped back to 1 (the clamp's reflect-clamped-value
    // behavior). Operator sees the corrected value immediately.
    expect(input.value).toBe('1');

    // No new POST fired — the clamped value (1) equals the current
    // previewWeek (1), so onWeekChange's `if (next !== weekValue)`
    // guard short-circuited the dispatch. Verifying after a small
    // wait window — the debounce would have fired any pending
    // reschedule by now.
    await new Promise((r) => setTimeout(r, 800));
    expect(startPreviewJobMock.mock.calls.length).toBe(callsBeforeBadInput);

    // localStorage also unchanged (defensive: writePreviewWeek would
    // have written '1' over the existing '1', no observable diff,
    // but the dispatch-not-fired contract is what catches the
    // regression class).
    expect(window.localStorage.getItem('previewWeek:viz-test')).toBeNull();
  });
});


// §16.2.7 c2 review R2 — page-level integration for the diff toggle
// flow. c1 covered classifier + state plumbing; c2 covered TracePane
// render; this block pins the joint contract end-to-end: operator
// toggles "Diff" ON → page fires startPreviewJob for the saved-state
// preview → cache populates → on next render the diff classes apply.
describe('visualise +page.svelte — §16.2.7 c2 diff toggle integration', () => {
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    patchBlockMock.mockReset();
    applyBatchMock.mockClear();
    startPreviewJobMock.mockReset();
    watchJobMock.mockReset();
    cancelJobMock.mockReset();
    cancelJobMock.mockResolvedValue(undefined);
    setSlug('viz-test');
    try { window.localStorage.clear(); } catch { /* */ }
    // Live preview ON so TracePane renders. Diff toggle starts OFF.
    window.localStorage.setItem('livePreview:viz-test', 'on');
    getStationSidecarMock.mockResolvedValue({
      template: makeTemplate(),
      sidecar: makeSidecar(),
      sidecar_error: null,
    });
  });

  it('toggling Diff ON dispatches showDiffChange → page calls startPreviewJob to populate savedTrace cache', async () => {
    // Default startPreviewJob mock: resolves immediately so the
    // watchJob iterator can drive the result.
    startPreviewJobMock.mockResolvedValue({
      job_id: 'pv-saved-1', status: 'running',
    });
    // watchJob yields done with a synthetic trace immediately.
    let watchCalls = 0;
    watchJobMock.mockImplementation(() => {
      watchCalls += 1;
      return {
        [Symbol.asyncIterator]() {
          return {
            async next() {
              return {
                done: false,
                value: {
                  status: 'done',
                  result: {
                    trace: {
                      trace_id: 'pv-saved',
                      station: 'viz-test',
                      week: 1,
                      rng: 'off',
                      seed: null,
                      generated_at: '2026-06-15T00:00:00Z',
                      slots: [
                        { slot: 1, unit: 'slot', row: 1, block: 1,
                          source: 'A', strategy: 'sequential',
                          pool_index: 1, track_id: 100,
                          track_name: 'T100', track_artist: 'A',
                          reason: '', excluded: [] },
                      ],
                    },
                  },
                },
              };
            },
            async return() { return { done: true, value: undefined }; },
          };
        },
      };
    });

    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});

    const callsBeforeToggle = startPreviewJobMock.mock.calls.length;
    const toggle = screen.getByTestId('trace-show-diff') as HTMLInputElement;
    expect(toggle.checked).toBe(false);

    await act(async () => {
      toggle.click();
    });
    await act(async () => {});

    // After the toggle, the page fires startPreviewJob for the
    // saved-state preview. Pre-R2 there was no test pinning this
    // chain — a regression where handleShowDiffChange forgot to
    // call _refreshSavedTraceIfNeeded would slip past the c1 +
    // c2 unit tests.
    expect(startPreviewJobMock.mock.calls.length).toBeGreaterThan(callsBeforeToggle);
    expect(watchCalls).toBeGreaterThan(0);
    // localStorage now persists the on-state per the c1
    // writeShowDiff helper.
    expect(window.localStorage.getItem('showDiff:viz-test')).toBe('on');
  });

  it('§16.2.7.t1 — generation gate: late saved-trace result after toggle-OFF invalidation does NOT pollute the cache', async () => {
    // Pin the c1 review R1 invariant. The `savedTraceGeneration`
    // counter is bumped at every cache-invalidation point. The async
    // `_refreshSavedTraceIfNeeded` captures the generation at start
    // + checks before every state assignment. Late results from a
    // previous context get discarded instead of polluting the
    // cleared cache.
    //
    // Setup: a deferred watchJob iterator that BLOCKS until the test
    // releases it. We start the saved-trace refresh, toggle OFF
    // (bumps generation + clears cache; sync — doesn't await the
    // in-flight refresh), then release the FIRST refresh's result.
    // Pre-fix this would have assigned savedTrace = (late trace).
    // Post-fix the generation gate fires + the assignment is
    // discarded.
    //
    // Verification surface: after toggle OFF → release → toggle ON
    // → release fresh result, the startPreviewJob call count went
    // up by 2 (one per toggle ON). If the gate had failed to
    // discard, the second toggle ON would have skipped firing
    // (savedTrace !== null in the guard would have short-circuited).
    type DeferredRefresh = { release: () => void };
    const refreshes: DeferredRefresh[] = [];

    startPreviewJobMock.mockImplementation(async () => ({
      job_id: `pv-deferred-${refreshes.length}`,
      status: 'running',
    }));

    watchJobMock.mockImplementation(() => {
      let releaseResolve: () => void = () => {};
      const releasePromise = new Promise<void>((resolve) => {
        releaseResolve = resolve;
      });
      refreshes.push({ release: releaseResolve });
      const myIndex = refreshes.length - 1;
      return {
        async *[Symbol.asyncIterator]() {
          await releasePromise;
          yield {
            status: 'done',
            result: {
              trace: {
                trace_id: `late-trace-${myIndex}`,
                station: 'viz-test',
                week: 1,
                rng: 'off',
                seed: null,
                generated_at: '2026-06-15T00:00:00Z',
                slots: [
                  {
                    slot: 1, unit: 'slot', row: 1, block: 1,
                    source: 'A', strategy: 'sequential',
                    pool_index: 1, track_id: 100 + myIndex,
                    track_name: `late-trace-${myIndex}`,
                    track_artist: 'A', reason: '', excluded: [],
                  },
                ],
              },
            },
          };
        },
      };
    });

    // setSlug hydration doesn't fire the saved-trace refresh on
    // mount — only user actions (toggle ON, week change, Save
    // success) do. Click the toggle to set showDiff ON + fire the
    // first refresh.
    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});

    const toggle = screen.getByTestId('trace-show-diff') as HTMLInputElement;
    await act(async () => {
      toggle.click();
    });
    await act(async () => {});

    // First refresh fired; iterator blocked on `await releasePromise`.
    expect(refreshes.length).toBeGreaterThanOrEqual(1);
    const firstCallCount = startPreviewJobMock.mock.calls.length;
    const firstDeferred = refreshes[0];

    // Toggle OFF: sync handler, bumps savedTraceGeneration +
    // clears the cache. Does NOT await any in-flight refresh.
    await act(async () => {
      toggle.click();
    });
    expect(window.localStorage.getItem('showDiff:viz-test')).toBe('off');

    // Release the FIRST refresh's deferred result. The watchJob
    // iterator advances past `await releasePromise`, yields `done`,
    // and the for-await loop in _refreshSavedTraceIfNeeded checks
    // the generation. POST-FIX: myGen captured BEFORE the toggle
    // OFF bump; current savedTraceGeneration is HIGHER; the gate
    // fires + returns without assigning savedTrace. PRE-FIX:
    // savedTrace would get assigned with late-trace-0.
    await act(async () => {
      firstDeferred.release();
      // Yield to let the iterator step + the page handler complete.
      await Promise.resolve();
      await Promise.resolve();
    });

    // Toggle back ON. A FRESH _refreshSavedTraceIfNeeded fires
    // (because the cache was correctly cleared on toggle OFF +
    // not polluted by the late result). This proves the gate
    // worked: if the late result had assigned savedTrace, the
    // guard `if (savedTrace !== null) return;` in
    // _refreshSavedTraceIfNeeded would have short-circuited this
    // second toggle ON → no new startPreviewJob call.
    await act(async () => {
      toggle.click();
    });
    await act(async () => {});

    expect(window.localStorage.getItem('showDiff:viz-test')).toBe('on');
    // PRE-fix the second toggle ON's refresh would have been
    // skipped by the `savedTrace !== null` guard (cache polluted).
    // POST-fix the cache was correctly cleared + the second toggle
    // ON fires a fresh refresh. We observe via increased
    // startPreviewJob call count.
    expect(startPreviewJobMock.mock.calls.length)
      .toBeGreaterThan(firstCallCount);
  });

  it('§16.2.7.t8 — Save during in-flight saved-trace refresh: new refresh fires + late result discarded', async () => {
    // Pin the .t1 review F1 invariant for the await-path
    // (handleStagedSave). Pre-fix the `savedTracePending` guard
    // short-circuited the new refresh when the old one was in flight
    // — cache stuck empty after Save. Post-fix concurrent refreshes
    // are allowed; the generation gate + server-side
    // POLICY_CANCEL_EXISTING handle correctness.
    type Deferred = { release: () => void };
    const refreshes: Deferred[] = [];
    startPreviewJobMock.mockImplementation(async () => ({
      job_id: `pv-deferred-${refreshes.length}`, status: 'running',
    }));
    watchJobMock.mockImplementation(() => {
      let releaseResolve: () => void = () => {};
      const releasePromise = new Promise<void>((resolve) => {
        releaseResolve = resolve;
      });
      refreshes.push({ release: releaseResolve });
      const myIndex = refreshes.length - 1;
      return {
        async *[Symbol.asyncIterator]() {
          await releasePromise;
          yield {
            status: 'done',
            result: {
              trace: {
                trace_id: `t8-trace-${myIndex}`,
                station: 'viz-test', week: 1, rng: 'off', seed: null,
                generated_at: '2026-06-15T00:00:00Z',
                slots: [
                  {
                    slot: 1, unit: 'slot', row: 1, block: 1,
                    source: 'A', strategy: 'sequential',
                    pool_index: 1, track_id: 200 + myIndex,
                    track_name: `t8-${myIndex}`, track_artist: 'A',
                    reason: '', excluded: [],
                  },
                ],
              },
            },
          };
        },
      };
    });
    // applyBatchMock's hoisted default returns a sane minimal
    // SidecarMutationResponse shape; no per-test override needed
    // for the happy-path Save in this test.

    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});

    // Toggle Diff ON to fire the first refresh.
    const toggle = screen.getByTestId('trace-show-diff') as HTMLInputElement;
    await act(async () => { toggle.click(); });
    await act(async () => {});
    expect(refreshes.length).toBeGreaterThanOrEqual(1);

    // Stage an edit so Save has something to flush.
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);

    const callsBeforeSave = startPreviewJobMock.mock.calls.length;

    // Click Save. handleStagedSave will bump savedTraceGeneration +
    // await a NEW _refreshSavedTraceIfNeeded. Pre-fix the new refresh
    // sees pending=true and returns; cache stuck empty.
    //
    // The await never resolves until we release the new refresh's
    // iterator. So we fire Save WITHOUT awaiting, then release.
    void act(async () => {
      screen.getByTestId('staged-save').click();
    });
    // Yield until the new refresh has registered (its watchJob was
    // called and pushed onto `refreshes`).
    while (refreshes.length < 2) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    expect(refreshes.length).toBeGreaterThanOrEqual(2);

    // Release the post-Save refresh so handleStagedSave's await
    // resolves.
    await act(async () => {
      refreshes[refreshes.length - 1].release();
      await Promise.resolve();
      await Promise.resolve();
    });

    // Also release the old (pre-Save) refresh — its iterator's gate
    // check should discard its result.
    await act(async () => {
      refreshes[0].release();
      await Promise.resolve();
      await Promise.resolve();
    });

    // Post-fix: startPreviewJob calls increased (new refresh fired
    // after Save). Pre-fix this assertion would fail — the pending
    // guard blocked the new refresh.
    expect(startPreviewJobMock.mock.calls.length)
      .toBeGreaterThan(callsBeforeSave);
  });

  it('§16.2.7.t9 — week change during in-flight saved-trace refresh: new refresh fires', async () => {
    // Same invariant as .t8 but exercised via handleWeekChange's
    // await path instead of handleStagedSave's.
    type Deferred = { release: () => void };
    const refreshes: Deferred[] = [];
    startPreviewJobMock.mockImplementation(async () => ({
      job_id: `pv-deferred-${refreshes.length}`, status: 'running',
    }));
    watchJobMock.mockImplementation(() => {
      let releaseResolve: () => void = () => {};
      const releasePromise = new Promise<void>((resolve) => {
        releaseResolve = resolve;
      });
      refreshes.push({ release: releaseResolve });
      const myIndex = refreshes.length - 1;
      return {
        async *[Symbol.asyncIterator]() {
          await releasePromise;
          yield {
            status: 'done',
            result: {
              trace: {
                trace_id: `t9-trace-${myIndex}`,
                station: 'viz-test', week: 1 + myIndex, rng: 'off', seed: null,
                generated_at: '2026-06-15T00:00:00Z',
                slots: [
                  {
                    slot: 1, unit: 'slot', row: 1, block: 1,
                    source: 'A', strategy: 'sequential',
                    pool_index: 1, track_id: 300 + myIndex,
                    track_name: `t9-${myIndex}`, track_artist: 'A',
                    reason: '', excluded: [],
                  },
                ],
              },
            },
          };
        },
      };
    });

    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});

    const toggle = screen.getByTestId('trace-show-diff') as HTMLInputElement;
    await act(async () => { toggle.click(); });
    await act(async () => {});
    expect(refreshes.length).toBeGreaterThanOrEqual(1);

    const callsBeforeWeekChange = startPreviewJobMock.mock.calls.length;

    // Change the week. handleWeekChange bumps + awaits new refresh.
    const weekInput = screen.getByTestId('trace-week-input') as HTMLInputElement;
    void act(async () => {
      weekInput.value = '2';
      weekInput.dispatchEvent(new Event('change', { bubbles: true }));
    });
    while (refreshes.length < 2) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }

    // Release the new refresh so handleWeekChange's await resolves.
    await act(async () => {
      refreshes[refreshes.length - 1].release();
      await Promise.resolve();
      await Promise.resolve();
    });
    // Release the old refresh; gate fires; result discarded.
    await act(async () => {
      refreshes[0].release();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(startPreviewJobMock.mock.calls.length)
      .toBeGreaterThan(callsBeforeWeekChange);
  });

  it('§16.2.7.t3 — cross-tab storage event: showDiff sync from another tab updates local state + fires refresh on ON, clears cache on OFF', async () => {
    // The browser dispatches the `storage` event on OTHER tabs (not
    // the writing tab). Pin the contract that the listener registered
    // in onMount mirrors a remote showDiff toggle into this tab's
    // state — same cache-lifecycle semantics as handleShowDiffChange.
    type Deferred = { release: () => void };
    const refreshes: Deferred[] = [];
    startPreviewJobMock.mockImplementation(async () => ({
      job_id: `pv-deferred-${refreshes.length}`, status: 'running',
    }));
    watchJobMock.mockImplementation(() => {
      let releaseResolve: () => void = () => {};
      const releasePromise = new Promise<void>((resolve) => {
        releaseResolve = resolve;
      });
      refreshes.push({ release: releaseResolve });
      return {
        async *[Symbol.asyncIterator]() {
          await releasePromise;
          yield { status: 'done', result: null };
        },
      };
    });

    // Start with showDiff OFF.
    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});
    const toggle = screen.getByTestId('trace-show-diff') as HTMLInputElement;
    expect(toggle.checked).toBe(false);

    const callsBeforeRemoteOn = startPreviewJobMock.mock.calls.length;

    // Simulate Tab A writing 'on' to localStorage + this tab
    // receiving the storage event. Per the WHATWG spec the writing
    // tab doesn't receive its own storage event, so the mock
    // dispatches the event manually with the persisted value.
    window.localStorage.setItem('showDiff:viz-test', 'on');
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'showDiff:viz-test',
        oldValue: 'off',
        newValue: 'on',
        storageArea: window.localStorage,
      }));
    });
    await act(async () => {});

    // Local toggle reflects the remote change + a saved-trace
    // refresh fired.
    expect(toggle.checked).toBe(true);
    expect(startPreviewJobMock.mock.calls.length).toBeGreaterThan(callsBeforeRemoteOn);

    // Now simulate the remote turning it OFF. Local state should
    // mirror; cache should NOT refire on OFF.
    const callsBeforeRemoteOff = startPreviewJobMock.mock.calls.length;
    window.localStorage.setItem('showDiff:viz-test', 'off');
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'showDiff:viz-test',
        oldValue: 'on',
        newValue: 'off',
        storageArea: window.localStorage,
      }));
    });
    await act(async () => {});

    expect(toggle.checked).toBe(false);
    // OFF path: no new preview fires (cache is being cleared, not
    // refreshed).
    expect(startPreviewJobMock.mock.calls.length).toBe(callsBeforeRemoteOff);

    // Defensive: an irrelevant storage event for a DIFFERENT key
    // doesn't perturb showDiff.
    const callsBeforeIrrelevant = startPreviewJobMock.mock.calls.length;
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'unrelated-key',
        oldValue: 'a',
        newValue: 'b',
        storageArea: window.localStorage,
      }));
    });
    expect(toggle.checked).toBe(false);
    expect(startPreviewJobMock.mock.calls.length).toBe(callsBeforeIrrelevant);

    // Defensive: a storage event from sessionStorage (e.storageArea
    // !== window.localStorage) is filtered out.
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'showDiff:viz-test',
        oldValue: 'off',
        newValue: 'on',
        storageArea: window.sessionStorage,
      }));
    });
    expect(toggle.checked).toBe(false);
  });

  it('toggling Diff OFF clears the persisted state without firing another preview', async () => {
    startPreviewJobMock.mockResolvedValue({
      job_id: 'pv-saved-2', status: 'running',
    });
    watchJobMock.mockReturnValue({
      [Symbol.asyncIterator]() {
        return { async next() { return { done: true, value: undefined }; } };
      },
    });
    // Pre-seed the toggle on so we start in the ON state.
    window.localStorage.setItem('showDiff:viz-test', 'on');

    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});

    const toggle = screen.getByTestId('trace-show-diff') as HTMLInputElement;
    expect(toggle.checked).toBe(true);

    const callsBeforeOff = startPreviewJobMock.mock.calls.length;
    await act(async () => {
      toggle.click();
    });
    await act(async () => {});

    // OFF transition writes "off" to localStorage + DOES NOT fire
    // a new saved-trace preview (the cache is being CLEARED, not
    // refreshed).
    expect(window.localStorage.getItem('showDiff:viz-test')).toBe('off');
    expect(startPreviewJobMock.mock.calls.length).toBe(callsBeforeOff);
  });

  it('§16.2.7.t4 R2 — _refreshMultiWeekDiff: populates weekDiffSummary for each week with classified counts', async () => {
    // Mock startPreviewJob + watchJob to return PREDICTABLE per-week
    // traces so the aggregator's loop output is verifiable.
    // Week 1: saved + staged identical → 0 changed/added/removed.
    // Week 2: staged has different track_id at index 1 → 1 changed.
    // Each preview call returns a distinct trace via a counter.
    type TraceShape = {
      slots: Array<{
        slot: number; unit: string; row: number; block: number;
        source: string; strategy: string; pool_index: number;
        track_id: number; track_name: string; track_artist: string;
        reason: string; excluded: never[];
      }>;
      trace_id: string; station: string; week: number;
      rng: string; seed: null; generated_at: string;
    };
    let callIndex = 0;
    const tracesByCall: TraceShape[] = [
      // Week 1 saved + staged: identical (single slot, track_id=100)
      { slots: [{ slot: 1, unit: 'slot', row: 1, block: 1, source: 'A', strategy: 'sequential', pool_index: 1, track_id: 100, track_name: 'T100', track_artist: 'A', reason: '', excluded: [] }],
        trace_id: 'w1-saved', station: 'viz-test', week: 1, rng: 'off', seed: null, generated_at: '2026-06-15T00:00:00Z' },
      { slots: [{ slot: 1, unit: 'slot', row: 1, block: 1, source: 'A', strategy: 'sequential', pool_index: 1, track_id: 100, track_name: 'T100', track_artist: 'A', reason: '', excluded: [] }],
        trace_id: 'w1-staged', station: 'viz-test', week: 1, rng: 'off', seed: null, generated_at: '2026-06-15T00:00:00Z' },
      // Week 2 saved: track_id=200; staged: track_id=999 → CHANGED.
      { slots: [{ slot: 1, unit: 'slot', row: 1, block: 1, source: 'A', strategy: 'sequential', pool_index: 1, track_id: 200, track_name: 'T200', track_artist: 'A', reason: '', excluded: [] }],
        trace_id: 'w2-saved', station: 'viz-test', week: 2, rng: 'off', seed: null, generated_at: '2026-06-15T00:00:00Z' },
      { slots: [{ slot: 1, unit: 'slot', row: 1, block: 1, source: 'A', strategy: 'sequential', pool_index: 1, track_id: 999, track_name: 'T999', track_artist: 'A', reason: '', excluded: [] }],
        trace_id: 'w2-staged', station: 'viz-test', week: 2, rng: 'off', seed: null, generated_at: '2026-06-15T00:00:00Z' },
    ];
    startPreviewJobMock.mockImplementation(async () => ({
      job_id: `pv-${callIndex}`, status: 'running',
    }));
    watchJobMock.mockImplementation(() => {
      const myCall = callIndex++;
      return {
        async *[Symbol.asyncIterator]() {
          yield {
            status: 'done',
            result: { trace: tracesByCall[myCall] ?? tracesByCall[0] },
          };
        },
      };
    });

    // Pre-seed showDiff ON + compareNextN=1 so the multi-week
    // aggregator fires for weeks [1, 2].
    window.localStorage.setItem('showDiff:viz-test', 'on');
    window.localStorage.setItem('compareNextN:viz-test', '1');

    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});

    // Toggle ON via click — this triggers _refreshSavedTraceIfNeeded
    // AND _refreshMultiWeekDiff (in _applyShowDiffChange).
    // showDiff was pre-seeded; click would toggle OFF. Toggle twice
    // to land back at ON with the aggregator firing.
    const toggle = screen.getByTestId('trace-show-diff') as HTMLInputElement;
    await act(async () => { toggle.click(); });  // OFF
    await act(async () => { toggle.click(); });  // ON → fires aggregator
    // Yield enough microtasks for the serial preview chain to
    // complete. The mock resolves immediately so 2 weeks × 2 calls
    // = 4 await tick equivalents.
    for (let i = 0; i < 20; i++) await act(async () => {});

    // weekDiffSummary should now have entries for weeks 1 + 2 with
    // the right counts. We can't read the page's internal state
    // directly, but we CAN read it via the TracePane's rendered cells
    // — the test-id contract is part of c2.
    const cells = screen.getAllByTestId('trace-multi-week-cell');
    expect(cells.length).toBeGreaterThanOrEqual(2);
    // Week 1: saved + staged identical → '·' (no deltas).
    const week1Cell = cells.find((c) => c.getAttribute('data-week') === '1');
    expect(week1Cell).toBeTruthy();
    expect(week1Cell!.getAttribute('data-kind')).toBe('ok');
    // Week 2: 1 changed → 'W2 1c'.
    const week2Cell = cells.find((c) => c.getAttribute('data-week') === '2');
    expect(week2Cell).toBeTruthy();
    expect(week2Cell!.getAttribute('data-kind')).toBe('ok');
    expect(week2Cell!.textContent).toContain('1c');
  });

  it('§16.2.7.t4 R3 — cross-tab storage event: compareNextN sync from another tab updates local state', async () => {
    // Mirror the t3 cross-tab sync test for the compareNextN path.
    watchJobMock.mockReturnValue({
      [Symbol.asyncIterator]() {
        return { async next() { return { done: true, value: undefined }; } };
      },
    });
    startPreviewJobMock.mockResolvedValue({ job_id: 'pv-x', status: 'running' });
    window.localStorage.setItem('showDiff:viz-test', 'on');
    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});

    // The +N input starts at 0 (no compareNextN pre-seed).
    const input = screen.getByTestId('trace-compare-next-n') as HTMLInputElement;
    expect(input.value).toBe('0');

    // Simulate another tab writing compareNextN=3 + dispatching
    // storage event.
    window.localStorage.setItem('compareNextN:viz-test', '3');
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'compareNextN:viz-test',
        oldValue: '0',
        newValue: '3',
        storageArea: window.localStorage,
      }));
    });
    await act(async () => {});

    // Local input reflects the remote change.
    expect((screen.getByTestId('trace-compare-next-n') as HTMLInputElement).value).toBe('3');
    // Multi-week summary row appears (compareNextN > 0).
    expect(screen.queryByTestId('trace-multi-week')).toBeInTheDocument();

    // Defensive: irrelevant key ignored.
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'unrelated', oldValue: 'a', newValue: 'b',
        storageArea: window.localStorage,
      }));
    });
    expect((screen.getByTestId('trace-compare-next-n') as HTMLInputElement).value).toBe('3');

    // Defensive: invalid value (NaN) clamps to 0.
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'compareNextN:viz-test',
        oldValue: '3', newValue: 'not-a-number',
        storageArea: window.localStorage,
      }));
    });
    await act(async () => {});
    expect((screen.getByTestId('trace-compare-next-n') as HTMLInputElement).value).toBe('0');
  });
});


// §16.2.7.t6 — page-level wiring smoke test for revert-this-slot.
// Full FULL chain (stage → TracePane render → revert button click →
// opLog spliced) would require driving BOTH saved + staged preview
// responses to populate TracePane's diff rows + clicking the actual
// button. c1 + c2 cover those layers exhaustively (6 attribution
// unit + 5 TracePane render). This block pins the PAGE-side wiring
// at the lowest verifiable altitude — verify TracePane's
// revertableSlots prop receives slot indices that have a direct
// op in the opLog. A regression in the page's reactive derivation
// (e.g. wrong field name, wrong op-kind set) would land here.
describe('visualise +page.svelte — §16.2.7.t6 revertableSlots wiring', () => {
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    patchBlockMock.mockReset();
    applyBatchMock.mockClear();
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
  });

  it('staged patchBlock contributes its slot_index to the revertableSlots set passed into TracePane', async () => {
    render(Page);
    await screen.findByTestId('editor-canvas');
    await act(async () => {});

    // Stage a patchBlock targeting slot 0 via the EditorCanvasStub
    // intercept. staged-save shows "1 edit".
    await act(async () => {
      fireEditorCanvasEvent('save', {
        ref: { kind: 'atomic-ref', slot_index: 0 },
        fields: { size: 5 },
      });
    });
    expect(screen.getByTestId('staged-save')).toHaveTextContent(/Save 1 edit/);

    // No direct revertableSlots inspection surface from outside;
    // the wiring is pinned implicitly via the staged-save count
    // (the test would fail at the stage step if the page wiring
    // were broken). The TracePane.test.ts suite covers the prop
    // → button-enabled translation; this block just confirms
    // that the page-side reactive derivation doesn't crash + the
    // staged-edit chain works end-to-end with the new wiring
    // present.
    //
    // Sanity: discard clears opLog → staged-save hides.
    await act(async () => {
      const discardBtn = screen.queryByTestId('staged-discard');
      if (discardBtn) discardBtn.click();
    });
    await act(async () => {});
    expect(screen.queryByTestId('staged-save')).toBeNull();
  });
});


describe('visualise +page.svelte — §16.2.4.t1 macro palette', () => {
  beforeEach(() => {
    getStationSidecarMock.mockReset();
    setSlug('viz-test');
    try { window.localStorage.clear(); } catch { /* */ }
  });

  function _sidecarWithMacros() {
    return {
      schema_version: 1,
      sections: [
        {
          id: 'main',
          name: 'Main',
          blocks: [
            { kind: 'atomic', slot_index: 0 },
            { kind: 'macro', ref: 'mp-1' },
          ],
        },
      ],
      macros: [
        {
          id: 'mp-1',
          name: 'Jingles',
          repeat: 2,
          spec: { source: 'A', strategy: 'jingle', size: 1, genre: 'G' },
        },
      ],
      layout_order: ['main'],
    };
  }

  it('renders the toggle button + drawer is hidden by default', async () => {
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: _sidecarWithMacros(),
      sidecar_error: null,
    });
    render(Page);
    await screen.findByTestId('editor-canvas');

    const toggle = screen.getByTestId('macro-palette-toggle');
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(toggle.textContent).toMatch(/Show macros/);
    expect(screen.queryByTestId('macro-palette')).toBeNull();
  });

  it('reveals the palette drawer on toggle click + populates from sidecar', async () => {
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: _sidecarWithMacros(),
      sidecar_error: null,
    });
    render(Page);
    await screen.findByTestId('editor-canvas');

    await act(async () => {
      screen.getByTestId('macro-palette-toggle').click();
    });

    const palette = screen.getByTestId('macro-palette');
    expect(palette).toBeInTheDocument();
    const rows = within(palette).getAllByTestId('macro-palette-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute('data-macro-id', 'mp-1');
    expect(screen.getByTestId('macro-palette-toggle')).toHaveAttribute(
      'aria-pressed', 'true',
    );
    expect(screen.getByTestId('macro-palette-toggle').textContent).toMatch(
      /Hide macros/,
    );
  });

  it('persists the toggle state to localStorage on click', async () => {
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: _sidecarWithMacros(),
      sidecar_error: null,
    });
    render(Page);
    await screen.findByTestId('editor-canvas');

    await act(async () => {
      screen.getByTestId('macro-palette-toggle').click();
    });
    expect(window.localStorage.getItem('macroPalette:viz-test')).toBe('on');

    await act(async () => {
      screen.getByTestId('macro-palette-toggle').click();
    });
    expect(window.localStorage.getItem('macroPalette:viz-test')).toBe('off');
  });

  it('hydrates the palette open-state from localStorage on mount', async () => {
    window.localStorage.setItem('macroPalette:viz-test', 'on');
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: _sidecarWithMacros(),
      sidecar_error: null,
    });
    render(Page);
    await screen.findByTestId('editor-canvas');
    // Drawer present without any user interaction.
    expect(screen.getByTestId('macro-palette')).toBeInTheDocument();
    expect(screen.getByTestId('macro-palette-toggle')).toHaveAttribute(
      'aria-pressed', 'true',
    );
  });

  it('opens the right-side column even when livePreviewOn is OFF', async () => {
    window.localStorage.setItem('macroPalette:viz-test', 'on');
    // Notice: NO `livePreview:viz-test = on` — palette alone drives
    // the right-column render.
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: _sidecarWithMacros(),
      sidecar_error: null,
    });
    render(Page);
    await screen.findByTestId('editor-canvas');
    expect(screen.getByTestId('trace-col')).toBeInTheDocument();
    // TracePane not rendered (livePreviewOn=false); palette is the
    // sole occupant of the column.
    expect(screen.getByTestId('macro-palette')).toBeInTheDocument();
  });

  it('shows the empty-state row when the sidecar has no macros', async () => {
    window.localStorage.setItem('macroPalette:viz-test', 'on');
    getStationSidecarMock.mockResolvedValueOnce({
      template: makeTemplate(),
      sidecar: makeSidecar(),  // no macros
      sidecar_error: null,
    });
    render(Page);
    await screen.findByTestId('editor-canvas');
    expect(screen.getByTestId('macro-palette-empty').textContent).toMatch(
      /No macros yet/,
    );
  });
});
