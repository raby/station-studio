import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/svelte';
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
          slots: slotIndices.map((s) => ({
            slot: s, unit: 'slot', row: 0, block: s,
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
    const row = document.querySelector<HTMLElement>(
      `[data-testid="trace-row"][data-slot-index="${slot}"]`,
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
    // makeSidecar has atomic at slot_index=0; findChipRef maps
    // {slot:0, row:0, block:0} → atomic:slot=0.
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
