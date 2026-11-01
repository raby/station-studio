import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/svelte';
import { renderWithEvents } from '$lib/renderWithEvents';
import '@testing-library/jest-dom/vitest';
import TracePane from './TracePane.svelte';
import type { Trace } from '$lib/api';
import type { Sidecar, StationTemplate } from '$lib/editor';


function makeTrace(slotCount: number = 3): Trace {
  const slots = Array.from({ length: slotCount }, (_, i) => ({
    slot: i + 1,
    unit: 'slot',
    row: 1,
    block: i + 1,
    source: 'A',
    strategy: 'sequential',
    pool_index: i + 1,
    track_id: 100 + i,
    track_name: `Track ${i + 1}`,
    track_artist: `Artist ${i + 1}`,
    reason: '',
    excluded: [],
  }));
  return {
    trace_id: 'pv-test-trace',
    station: 'ts',
    week: 1,
    rng: 'off',
    seed: null,
    generated_at: '2026-06-15T12:00:00Z',
    slots,
  };
}


describe('<TracePane>', () => {
  beforeEach(() => cleanup());

  it('renders the empty-state nudge when no trace + not previewing + no error', () => {
    render(TracePane, { tab: 'slots', trace: null, previewing: false, errorMessage: null });
    expect(screen.getByTestId('trace-empty')).toHaveTextContent(
      /Edit a chip/,
    );
    // No slot rows.
    expect(screen.queryAllByTestId('trace-row')).toHaveLength(0);
  });

  it('renders the previewing indicator when previewing=true', () => {
    render(TracePane, { tab: 'slots', trace: null, previewing: true, errorMessage: null });
    expect(screen.getByTestId('trace-status-previewing')).toHaveTextContent(
      /Previewing/,
    );
    // Empty-state hidden while previewing.
    expect(screen.queryByTestId('trace-empty')).not.toBeInTheDocument();
  });

  it('renders the trace slots when a trace is supplied', () => {
    const trace = makeTrace(3);
    render(TracePane, { tab: 'slots', trace, previewing: false, errorMessage: null });

    const rows = screen.getAllByTestId('trace-row');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute('data-slot-index', '1');
    expect(rows[2]).toHaveAttribute('data-slot-index', '3');
    expect(screen.getByTestId('trace-status-ok')).toHaveTextContent(
      'Week 1 · 3 slots',
    );
    // Track names render.
    expect(screen.getByText('Track 1')).toBeInTheDocument();
    expect(screen.getByText('Track 3')).toBeInTheDocument();
  });

  it('renders the error banner ABOVE the last-good trace when errorMessage is set', () => {
    // §16.2.6 design: "The trace pane keeps showing the last-good
    // trace so the operator's frame of reference doesn't disappear
    // when they're mid-typing." Pin both: error banner visible AND
    // trace rows still rendered.
    const trace = makeTrace(2);
    render(TracePane, { tab: 'slots',
      trace,
      previewing: false,
      errorMessage: 'invalid sidecar (grammar): missing layout_order',
    });
    expect(screen.getByTestId('trace-error')).toHaveTextContent(
      /missing layout_order/,
    );
    expect(screen.getAllByTestId('trace-row')).toHaveLength(2);
    // Status header reflects the error.
    expect(screen.getByTestId('trace-status-error')).toHaveTextContent(
      'Preview failed',
    );
  });

  it('dispatches a select event with slotIndex on row click (§16.2.6.t3 hook)', async () => {
    const trace = makeTrace(3);
    const { on } = renderWithEvents(TracePane, { tab: 'slots',
      trace, previewing: false, errorMessage: null,
    });
    let selectedSlotIndex: number | null = null;
    on('select', (e: CustomEvent<{ slotIndex: number }>) => {
      selectedSlotIndex = e.detail.slotIndex;
    });
    await fireEvent.click(screen.getAllByTestId('trace-row')[1]);
    expect(selectedSlotIndex).toBe(2);  // 1-indexed slot
  });
});


// §16.2.7 c2 — compare-against-saved diff rendering.
describe('<TracePane> — §16.2.7 c2 diff mode', () => {
  beforeEach(() => cleanup());

  // Helper: build a trace with track_ids matching the supplied list
  // (so changed-vs-unchanged tracking is easy to construct).
  function traceFor(track_ids: Array<number | null>): Trace {
    const slots = track_ids.map((track_id, i) => ({
      slot: i + 1,
      unit: 'slot',
      row: 1,
      block: i + 1,
      source: 'A',
      strategy: 'sequential',
      pool_index: i + 1,
      track_id,
      track_name: `T${track_id ?? 'null'}`,
      track_artist: 'A',
      reason: '',
      excluded: [],
    }));
    return {
      trace_id: 'pv-diff-test',
      station: 'ts',
      week: 1,
      rng: 'off',
      seed: null,
      generated_at: '2026-06-15T12:00:00Z',
      slots,
    };
  }

  it('showDiff=false: no diff classes, summary, or toggle-checked state', () => {
    const saved = traceFor([1, 2, 3]);
    const staged = traceFor([1, 99, 3]);
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: saved,
      showDiff: false,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    // Toggle present but unchecked.
    const toggle = screen.getByTestId('trace-show-diff') as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    // No diff summary.
    expect(screen.queryByTestId('trace-diff-summary')).toBeNull();
    // Rows render with the existing single-trace look — no
    // data-diff-kind attribute (only set in diff mode).
    const rows = screen.getAllByTestId('trace-row');
    expect(rows.length).toBe(staged.slots.length);
    for (const row of rows) {
      expect(row).not.toHaveAttribute('data-diff-kind');
    }
  });

  it('showDiff=true with matching traces: all UNCHANGED + "No changes since save" summary', () => {
    const saved = traceFor([1, 2, 3]);
    const staged = traceFor([1, 2, 3]);
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: saved,
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    const summary = screen.getByTestId('trace-diff-summary');
    expect(summary).toHaveTextContent('No changes since save');
    const rows = screen.getAllByTestId('trace-row');
    expect(rows.length).toBe(3);
    for (const row of rows) {
      expect(row).toHaveAttribute('data-diff-kind', 'unchanged');
      expect(row.className).toContain('slot--unchanged');
    }
    // No "was: ..." subtext on unchanged rows.
    expect(screen.queryAllByTestId('trace-row-was')).toHaveLength(0);
  });

  it('showDiff=true with mid-slot change: CHANGED row + "was: X" subtext + count', () => {
    const saved = traceFor([1, 2, 3]);
    const staged = traceFor([1, 99, 3]);  // slot 2 changes 2 → 99
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: saved,
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    const summary = screen.getByTestId('trace-diff-summary');
    expect(summary).toHaveTextContent('1 changed');
    const rows = screen.getAllByTestId('trace-row');
    expect(rows[0]).toHaveAttribute('data-diff-kind', 'unchanged');
    expect(rows[1]).toHaveAttribute('data-diff-kind', 'changed');
    expect(rows[2]).toHaveAttribute('data-diff-kind', 'unchanged');
    // The CHANGED row shows the STAGED track_name (T99) + the
    // saved-side as "was: T2" subtext.
    const wasSubtexts = screen.getAllByTestId('trace-row-was');
    expect(wasSubtexts.length).toBe(1);
    expect(wasSubtexts[0]).toHaveTextContent('was: T2');
  });

  it('showDiff=true with staged longer: trailing ADDED rows + "+ added" marker', () => {
    const saved = traceFor([1, 2]);
    const staged = traceFor([1, 2, 3, 4]);  // structural add
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: saved,
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    expect(screen.getByTestId('trace-diff-summary')).toHaveTextContent('2 added');
    const rows = screen.getAllByTestId('trace-row');
    expect(rows[2]).toHaveAttribute('data-diff-kind', 'added');
    expect(rows[3]).toHaveAttribute('data-diff-kind', 'added');
    // Markers on the two ADDED rows.
    const markers = screen.getAllByTestId('trace-row-marker');
    expect(markers.length).toBe(2);
    for (const m of markers) expect(m).toHaveTextContent('+ added');
  });

  it('showDiff=true with saved longer: trailing REMOVED rows render SAVED content', () => {
    const saved = traceFor([1, 2, 3, 4]);
    const staged = traceFor([1, 2]);  // structural remove
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: saved,
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    expect(screen.getByTestId('trace-diff-summary')).toHaveTextContent('2 removed');
    const rows = screen.getAllByTestId('trace-row');
    expect(rows.length).toBe(4);
    expect(rows[2]).toHaveAttribute('data-diff-kind', 'removed');
    expect(rows[3]).toHaveAttribute('data-diff-kind', 'removed');
    // REMOVED rows render the saved-side content ("T3", "T4") so
    // the operator sees what's about to disappear.
    expect(rows[2]).toHaveTextContent('T3');
    expect(rows[3]).toHaveTextContent('T4');
  });

  it('mixed CHANGED + ADDED + REMOVED summary lists all three counts joined by " · "', () => {
    const saved = traceFor([1, 2, 5]);
    const staged = traceFor([1, 99, 3, 4]);  // slot1 ok, slot2 changed, slot3 was 5 now 3 (changed), slot4 added
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: saved,
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    // 2 changed (indices 1, 2) + 1 added (index 3) → no removed.
    expect(screen.getByTestId('trace-diff-summary'))
      .toHaveTextContent('2 changed · 1 added');
  });

  it('showDiff toggle dispatches showDiffChange { on } on click', async () => {
    const saved = traceFor([1, 2, 3]);
    const staged = traceFor([1, 2, 3]);
    const { on } = renderWithEvents(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: saved,
      showDiff: false,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    let dispatched: { on: boolean } | null = null;
    on('showDiffChange', (e: CustomEvent<{ on: boolean }>) => {
      dispatched = e.detail;
    });
    const toggle = screen.getByTestId('trace-show-diff');
    await fireEvent.click(toggle);
    expect(dispatched).toEqual({ on: true });
  });

  it('savedTraceError + showDiff=false: NO banner (don\'t leak the error when diff is off)', () => {
    const staged = traceFor([1, 2, 3]);
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: null,
      showDiff: false,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: 'job failed',
    });
    expect(screen.queryByTestId('trace-diff-error')).toBeNull();
  });

  it('savedTraceError + showDiff=true: "Diff unavailable: ..." banner with the documented wording', () => {
    const staged = traceFor([1, 2, 3]);
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: null,
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: 'job failed',
    });
    expect(screen.getByTestId('trace-diff-error'))
      .toHaveTextContent('Diff unavailable: job failed');
  });

  it('showDiff=true but savedTrace null (cache pending): NO diff classes (falls back to single-trace render)', () => {
    // The diffReady gate requires BOTH traces. While the saved-trace
    // fetch is in flight, savedTrace is null and the existing
    // single-trace render path takes over — no broken UI, just
    // delayed diff visualization.
    const staged = traceFor([1, 2, 3]);
    render(TracePane, { tab: 'slots',
      trace: staged,
      savedTrace: null,
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    expect(screen.queryByTestId('trace-diff-summary')).toBeNull();
    const rows = screen.getAllByTestId('trace-row');
    for (const row of rows) {
      expect(row).not.toHaveAttribute('data-diff-kind');
    }
  });
});


// §16.2.7.t4 — multi-week diff aggregator UI.
describe('<TracePane> — §16.2.7.t4 multi-week summary', () => {
  beforeEach(() => cleanup());

  function tr(track_ids: Array<number>) {
    const slots = track_ids.map((track_id, i) => ({
      slot: i + 1, unit: 'slot', row: 1, block: i + 1,
      source: 'A', strategy: 'sequential', pool_index: i + 1,
      track_id, track_name: `T${track_id}`, track_artist: 'A',
      reason: '', excluded: [],
    }));
    return {
      trace_id: 'pv', station: 'ts', week: 1, rng: 'off', seed: null,
      generated_at: '2026-06-15T00:00:00Z', slots,
    } as Trace;
  }

  it('compareNextN=0 → input rendered but NO multi-week summary row', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1, 2]), savedTrace: tr([1, 2]),
      showDiff: true, previewing: false, errorMessage: null,
      weekValue: 1, savedTraceError: null,
      compareNextN: 0,
      weekDiffSummary: new Map(),
      multiWeekFetching: false,
      compareNextNMax: 5,
    });
    // The +N input is present (so the operator can opt in) but the
    // summary row is absent at N=0.
    expect(screen.getByTestId('trace-compare-next-n')).toBeInTheDocument();
    expect(screen.queryByTestId('trace-multi-week')).toBeNull();
  });

  it('compareNextN=2 with cached summary: cells render with counts for weeks [w, w+1, w+2]', () => {
    const summary = new Map<number, { changed: number; added: number; removed: number } | { error: string }>();
    summary.set(3, { changed: 0, added: 0, removed: 0 });   // W3 ·
    summary.set(4, { changed: 2, added: 1, removed: 0 });   // W4 2c 1a
    summary.set(5, { changed: 0, added: 0, removed: 3 });   // W5 3r
    render(TracePane, { tab: 'slots',
      trace: tr([1, 2]), savedTrace: tr([1, 2]),
      showDiff: true, previewing: false, errorMessage: null,
      weekValue: 3, savedTraceError: null,
      compareNextN: 2,
      weekDiffSummary: summary,
      multiWeekFetching: false,
      compareNextNMax: 5,
    });
    const row = screen.getByTestId('trace-multi-week');
    expect(row).toHaveTextContent('Weeks 3-5:');
    const cells = screen.getAllByTestId('trace-multi-week-cell');
    expect(cells).toHaveLength(3);
    expect(cells[0]).toHaveAttribute('data-week', '3');
    expect(cells[0]).toHaveAttribute('data-kind', 'ok');
    expect(cells[0]).toHaveTextContent('W3 ·');
    expect(cells[1]).toHaveAttribute('data-week', '4');
    expect(cells[1]).toHaveTextContent('W4 2c 1a');
    expect(cells[2]).toHaveAttribute('data-week', '5');
    expect(cells[2]).toHaveTextContent('W5 3r');
  });

  it('compareNextN=1 while fetching: cells render "…" placeholders with fetching kind', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1]), savedTrace: tr([1]),
      showDiff: true, previewing: false, errorMessage: null,
      weekValue: 1, savedTraceError: null,
      compareNextN: 1,
      weekDiffSummary: new Map(),
      multiWeekFetching: true,
      compareNextNMax: 5,
    });
    const cells = screen.getAllByTestId('trace-multi-week-cell');
    expect(cells).toHaveLength(2);
    for (const cell of cells) {
      expect(cell).toHaveAttribute('data-kind', 'fetching');
      expect(cell).toHaveTextContent(/W\d+ …/);
    }
  });

  it('per-week failure → cell renders as error with title tooltip carrying the error', () => {
    const summary = new Map<number, { changed: number; added: number; removed: number } | { error: string }>();
    summary.set(1, { changed: 0, added: 0, removed: 0 });
    summary.set(2, { error: 'preview failed: pool empty' });
    render(TracePane, { tab: 'slots',
      trace: tr([1]), savedTrace: tr([1]),
      showDiff: true, previewing: false, errorMessage: null,
      weekValue: 1, savedTraceError: null,
      compareNextN: 1,
      weekDiffSummary: summary,
      multiWeekFetching: false,
      compareNextNMax: 5,
    });
    const cells = screen.getAllByTestId('trace-multi-week-cell');
    expect(cells[1]).toHaveAttribute('data-kind', 'error');
    expect(cells[1]).toHaveTextContent('W2 ⚠');
    expect(cells[1].getAttribute('title')).toContain('preview failed: pool empty');
  });

  it('compareNextN input change dispatches compareNextNChange { n }', async () => {
    const { on } = renderWithEvents(TracePane, { tab: 'slots',
      trace: tr([1]), savedTrace: tr([1]),
      showDiff: true, previewing: false, errorMessage: null,
      weekValue: 1, savedTraceError: null,
      compareNextN: 0,
      weekDiffSummary: new Map(),
      multiWeekFetching: false,
      compareNextNMax: 5,
    });
    let dispatched: { n: number } | null = null;
    on('compareNextNChange', (e: CustomEvent<{ n: number }>) => {
      dispatched = e.detail;
    });
    const input = screen.getByTestId('trace-compare-next-n') as HTMLInputElement;
    input.value = '3';
    await fireEvent.change(input);
    expect(dispatched).toEqual({ n: 3 });
  });

  it('compareNextN input clamps to compareNextNMax on dispatch', async () => {
    const { on } = renderWithEvents(TracePane, { tab: 'slots',
      trace: tr([1]), savedTrace: tr([1]),
      showDiff: true, previewing: false, errorMessage: null,
      weekValue: 1, savedTraceError: null,
      compareNextN: 0,
      weekDiffSummary: new Map(),
      multiWeekFetching: false,
      compareNextNMax: 5,
    });
    let dispatched: { n: number } | null = null;
    on('compareNextNChange', (e: CustomEvent<{ n: number }>) => {
      dispatched = e.detail;
    });
    const input = screen.getByTestId('trace-compare-next-n') as HTMLInputElement;
    input.value = '99';
    await fireEvent.change(input);
    expect(dispatched).toEqual({ n: 5 });
  });

  it('compareNextN input is HIDDEN when showDiff=false', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1]), savedTrace: null,
      showDiff: false, previewing: false, errorMessage: null,
      weekValue: 1, savedTraceError: null,
      compareNextN: 0,
      weekDiffSummary: new Map(),
      multiWeekFetching: false,
      compareNextNMax: 5,
    });
    expect(screen.queryByTestId('trace-compare-next-n')).toBeNull();
    expect(screen.queryByTestId('trace-multi-week')).toBeNull();
  });
});


// §16.2.7.t5 — per-section diff aggregate row.
describe('<TracePane> — §16.2.7.t5 per-section summary', () => {
  beforeEach(() => cleanup());

  // Helper: build a flat-slots template + sidecar with 2 sections
  // covering disjoint slot ranges (morning slots 0,1,2 / evening
  // slots 3,4,5). Used to drive aggregateBySection through render.
  function template(): StationTemplate {
    return {
      station: 'ts', name: 'T',
      sources: [{ name: 'A', role: 'content' }],
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [],
        flat_slots: Array.from({ length: 6 }, () =>
          ({ source: 'A', strategy: 'sequential' as const, size: 1, cursor_id: 'C' })),
      },
    };
  }
  function sidecar(): Sidecar {
    return {
      schema_version: 1,
      sections: [
        { id: 'morning', name: 'Morning', blocks: [
          { kind: 'atomic' as const, slot_index: 0 },
          { kind: 'atomic' as const, slot_index: 1 },
          { kind: 'atomic' as const, slot_index: 2 },
        ]},
        { id: 'evening', name: 'Evening', blocks: [
          { kind: 'atomic' as const, slot_index: 3 },
          { kind: 'atomic' as const, slot_index: 4 },
          { kind: 'atomic' as const, slot_index: 5 },
        ]},
      ],
      macros: [],
      layout_order: ['morning', 'evening'],
    };
  }
  function tr(track_ids: Array<number>): Trace {
    const slots = track_ids.map((track_id, i) => ({
      slot: i, unit: 'slot', row: 1, block: i,
      source: 'A', strategy: 'sequential', pool_index: i,
      track_id, track_name: `T${track_id}`, track_artist: 'A',
      reason: '', excluded: [],
    }));
    return {
      trace_id: 'pv', station: 'ts', week: 1, rng: 'off',
      seed: null, generated_at: '2026-06-15T00:00:00Z', slots,
    };
  }

  function renderProps(overrides: Record<string, unknown> = {}) {
    return render(TracePane, { tab: 'slots',
      trace: tr([1, 2, 3, 4, 5, 6]),
      savedTrace: tr([1, 2, 3, 4, 5, 6]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      compareNextN: 0,
      weekDiffSummary: new Map(),
      multiWeekFetching: false,
      compareNextNMax: 5,
      template: template(),
      savedSidecar: sidecar(),
      stagedSidecar: sidecar(),
      ...overrides,
    });
  }

  it('matching traces → NO per-section row (all-zero counts filtered)', () => {
    renderProps();
    expect(screen.queryByTestId('trace-section-summary')).toBeNull();
  });

  it('one slot CHANGED in morning → row shows ONLY morning (evening filtered)', () => {
    renderProps({
      savedTrace: tr([1, 2, 3, 4, 5, 6]),
      trace: tr([1, 99, 3, 4, 5, 6]),  // index 1 (morning) changed
    });
    const row = screen.getByTestId('trace-section-summary');
    expect(row).toBeInTheDocument();
    const cells = screen.getAllByTestId('trace-section-summary-cell');
    expect(cells).toHaveLength(1);
    expect(cells[0]).toHaveAttribute('data-section-id', 'morning');
    expect(cells[0]).toHaveTextContent('Morning 1c');
  });

  it('cross-section: CHANGED in both morning + evening → 2 cells in layout_order', () => {
    renderProps({
      savedTrace: tr([1, 2, 3, 4, 5, 6]),
      trace: tr([1, 99, 3, 4, 99, 6]),  // index 1 (morning) + index 4 (evening)
    });
    const cells = screen.getAllByTestId('trace-section-summary-cell');
    expect(cells).toHaveLength(2);
    expect(cells[0]).toHaveAttribute('data-section-id', 'morning');
    expect(cells[1]).toHaveAttribute('data-section-id', 'evening');
    expect(cells[0]).toHaveTextContent('Morning 1c');
    expect(cells[1]).toHaveTextContent('Evening 1c');
  });

  it('uses section name from sidecar, not id', () => {
    const sc = sidecar();
    sc.sections[0].name = 'Sunrise Block';
    renderProps({
      savedSidecar: sc, stagedSidecar: sc,
      savedTrace: tr([1, 2, 3, 4, 5, 6]),
      trace: tr([1, 99, 3, 4, 5, 6]),
    });
    const cells = screen.getAllByTestId('trace-section-summary-cell');
    expect(cells[0]).toHaveTextContent('Sunrise Block 1c');
  });

  it('unmapped slot → "(unmapped)" cell appended at the end', () => {
    // Sidecar only covers morning (slots 0-2); a CHANGED at slot 4
    // (no section contains it) falls to UNMAPPED.
    const morningOnly = {
      ...sidecar(),
      sections: [{
        id: 'morning', name: 'Morning', blocks: [
          { kind: 'atomic' as const, slot_index: 0 },
          { kind: 'atomic' as const, slot_index: 1 },
          { kind: 'atomic' as const, slot_index: 2 },
        ],
      }],
      layout_order: ['morning'],
    };
    renderProps({
      savedSidecar: morningOnly, stagedSidecar: morningOnly,
      savedTrace: tr([1, 2, 3, 4, 5, 6]),
      trace: tr([1, 2, 3, 4, 99, 6]),  // index 4 changed (unmapped)
    });
    const cells = screen.getAllByTestId('trace-section-summary-cell');
    expect(cells.length).toBe(1);
    expect(cells[0]).toHaveAttribute('data-section-id', '(unmapped)');
    expect(cells[0]).toHaveTextContent('(unmapped) 1c');
  });

  it('showDiff=false → NO per-section row even with diffs', () => {
    renderProps({
      showDiff: false,
      savedTrace: tr([1, 2, 3, 4, 5, 6]),
      trace: tr([1, 99, 3, 4, 99, 6]),
    });
    expect(screen.queryByTestId('trace-section-summary')).toBeNull();
  });

  it('R1 fix: section deleted in staged renders REMOVED counts at the tail (before UNMAPPED)', () => {
    // Saved has morning + evening (slot_index 0-2 and 3-5); staged
    // removed morning entirely. Aggregator buckets REMOVED-slot
    // lookups under their SAVED-side section id ('morning'). Pre-fix
    // the renderer iterated only stagedSidecar.layout_order +
    // dropped the morning entry. R1 fix: render aggregate entries
    // NOT in staged.layout_order at the tail (before UNMAPPED) so
    // the operator sees "morning ... r" cells for sections their
    // edit is about to delete.
    //
    // To trigger a REMOVED classification that maps to morning we
    // need savedTrace longer than stagedTrace + the REMOVED indices'
    // saved-side slot.slot value to fall inside morning's slot range
    // (0-2). Saved=2 slots, staged=1 slot → classifications
    // [UNCHANGED, REMOVED]. REMOVED's index=1 → savedTrace.slots[1]
    // .slot=1 → morning (slot_index 1 is morning).
    const savedSc = sidecar();
    const stagedSc: Sidecar = {
      ...sidecar(),
      sections: [sidecar().sections[1]],  // evening only
      layout_order: ['evening'],
    };
    const savedTrace = tr([1, 2]);   // 2 slots
    const stagedTrace = tr([1]);     // 1 slot — last one REMOVED
    renderProps({
      savedSidecar: savedSc, stagedSidecar: stagedSc,
      savedTrace, trace: stagedTrace,
    });
    const cells = screen.getAllByTestId('trace-section-summary-cell');
    // Morning cell present + carries the SAVED-side name (staged
    // no longer has the section, so the renderer fell back to
    // savedSidecar for the name lookup).
    const morningCell = cells.find((c) => c.getAttribute('data-section-id') === 'morning');
    expect(morningCell).toBeTruthy();
    expect(morningCell!.textContent).toContain('Morning');
    expect(morningCell!.textContent).toContain('1r');
  });

  it('template/sidecar null → NO per-section row', () => {
    renderProps({
      template: null,
      savedTrace: tr([1, 2, 3, 4, 5, 6]),
      trace: tr([1, 99, 3, 4, 5, 6]),
    });
    expect(screen.queryByTestId('trace-section-summary')).toBeNull();
  });
});


// §16.2.7.t6 — per-row Revert button.
describe('<TracePane> — §16.2.7.t6 revert button', () => {
  beforeEach(() => cleanup());

  function tr(track_ids: Array<number>): Trace {
    const slots = track_ids.map((track_id, i) => ({
      slot: i, unit: 'slot', row: 1, block: i,
      source: 'A', strategy: 'sequential', pool_index: i,
      track_id, track_name: `T${track_id}`, track_artist: 'A',
      reason: '', excluded: [],
    }));
    return {
      trace_id: 'pv', station: 'ts', week: 1, rng: 'off',
      seed: null, generated_at: '2026-06-15T00:00:00Z', slots,
    };
  }

  it('UNCHANGED rows have NO revert button (nothing to revert)', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1, 2, 3]),
      savedTrace: tr([1, 2, 3]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      revertableSlots: new Set<number>(),
    });
    expect(screen.queryAllByTestId('trace-row-revert')).toHaveLength(0);
  });

  it('CHANGED row with revertable slot → enabled Revert button', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1, 99, 3]),  // index 1 (slot=1) changed
      savedTrace: tr([1, 2, 3]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      revertableSlots: new Set([1]),  // slot 1 has direct attribution
    });
    const buttons = screen.getAllByTestId('trace-row-revert');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAttribute('data-revert-enabled', 'true');
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(false);
  });

  it('CHANGED row with NON-revertable slot → disabled Revert button with cascade tooltip', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1, 99, 3]),
      savedTrace: tr([1, 2, 3]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      revertableSlots: new Set<number>(),  // empty — cascade only
    });
    const button = screen.getByTestId('trace-row-revert') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute('title') ?? '').toMatch(/structural edit/);
  });

  it('click on Revert button dispatches revertSlot { slotIndex } + stops propagation (no select)', async () => {
    const { on } = renderWithEvents(TracePane, { tab: 'slots',
      trace: tr([1, 99, 3]),
      savedTrace: tr([1, 2, 3]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      revertableSlots: new Set([1]),
    });
    let revertedSlot: number | null = null;
    let selectedSlot: number | null = null;
    on('revertSlot', (e: CustomEvent<{ slotIndex: number }>) => {
      revertedSlot = e.detail.slotIndex;
    });
    on('select', (e: CustomEvent<{ slotIndex: number }>) => {
      selectedSlot = e.detail.slotIndex;
    });
    await fireEvent.click(screen.getByTestId('trace-row-revert'));
    expect(revertedSlot).toBe(1);
    // stopPropagation: the select event must NOT have fired even
    // though the button is inside the row's <li>.
    expect(selectedSlot).toBeNull();
  });

  it('ADDED + REMOVED rows also get a Revert button (one per non-UNCHANGED row)', () => {
    // saved=[1,2,3,4] staged=[1,2,3,4,5] → index 4 ADDED.
    render(TracePane, { tab: 'slots',
      trace: tr([1, 2, 3, 4, 5]),
      savedTrace: tr([1, 2, 3, 4]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      revertableSlots: new Set([4]),
    });
    const buttons = screen.getAllByTestId('trace-row-revert');
    expect(buttons).toHaveLength(1);
    expect(buttons[0].closest('li')?.getAttribute('data-diff-kind')).toBe('added');
  });
});


// §16.2.7.t7 — Edits panel.
describe('<TracePane> — §16.2.7.t7 edits panel (semantic diff)', () => {
  beforeEach(() => cleanup());

  function tr(track_ids: Array<number>): Trace {
    const slots = track_ids.map((track_id, i) => ({
      slot: i, unit: 'slot', row: 1, block: i,
      source: 'A', strategy: 'sequential', pool_index: i,
      track_id, track_name: `T${track_id}`, track_artist: 'A',
      reason: '', excluded: [],
    }));
    return {
      trace_id: 'pv', station: 'ts', week: 1, rng: 'off',
      seed: null, generated_at: '2026-06-15T00:00:00Z', slots,
    };
  }

  it('renders nothing when showDiff is OFF (even with non-empty opLog)', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1, 2, 3]),
      savedTrace: tr([1, 2, 3]),
      showDiff: false,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      opLog: [{
        kind: 'renameSection', sectionId: 'morning', name: 'AM',
      }],
    });
    expect(screen.queryByTestId('trace-edits-panel')).toBeNull();
  });

  it('renders nothing when opLog is empty (even with showDiff on)', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1, 2, 3]),
      savedTrace: tr([1, 2, 3]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      opLog: [],
    });
    expect(screen.queryByTestId('trace-edits-panel')).toBeNull();
  });

  it('shows one numbered row per op with describeOp rendering', () => {
    render(TracePane, { tab: 'slots',
      trace: tr([1, 2, 3]),
      savedTrace: tr([1, 2, 3]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      opLog: [
        { kind: 'renameSection', sectionId: 'morning', name: 'AM' },
        {
          kind: 'patchBlock',
          address: { slot_index: 0 },
          fields: { size: 5 },
        },
        { kind: 'sectionDelete', sectionId: 'evening' },
      ],
    });
    const panel = screen.getByTestId('trace-edits-panel');
    expect(panel).toBeInTheDocument();
    expect(panel.textContent).toContain('Edits (3):');
    const rows = screen.getAllByTestId('trace-edits-row');
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("Renamed section 'morning' to 'AM'");
    expect(rows[1].textContent).toContain('Patched slot 0: size=5');
    expect(rows[2].textContent).toContain("Deleted section 'evening'");
  });

  it('surfaces structural edits invisible to trace diff (operator clearly did something)', () => {
    // Reorder doesn't change track_ids → trace would say "No
    // changes since save". The Edits panel makes the intent visible.
    render(TracePane, { tab: 'slots',
      trace: tr([1, 2, 3]),
      savedTrace: tr([1, 2, 3]),
      showDiff: true,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
      opLog: [{
        kind: 'reorderSections',
        layoutOrder: ['evening', 'morning'],
      }],
    });
    const rows = screen.getAllByTestId('trace-edits-row');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain(
      'Reordered sections to: evening, morning',
    );
  });
});


// The Songs tab: the week page's song list in the preview column.
describe('<TracePane> — Songs and Slots tabs', () => {
  beforeEach(() => cleanup());

  function template(): StationTemplate {
    return {
      station: 'ts', name: 'T',
      sources: [{ name: 'A', role: 'content' }],
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [],
        flat_slots: Array.from({ length: 4 }, () =>
          ({ source: 'A', strategy: 'sequential' as const, size: 1, cursor_id: 'C' })),
      },
    };
  }
  function sidecar(): Sidecar {
    return {
      schema_version: 1,
      sections: [
        { id: 'morning', name: 'Morning', blocks: [
          { kind: 'atomic' as const, slot_index: 0 },
          { kind: 'atomic' as const, slot_index: 1 },
        ]},
        { id: 'evening', name: 'Evening', blocks: [
          { kind: 'atomic' as const, slot_index: 2 },
          { kind: 'atomic' as const, slot_index: 3 },
        ]},
      ],
      macros: [],
      layout_order: ['morning', 'evening'],
    };
  }
  function tr(track_ids: number[]): Trace {
    return {
      trace_id: 'pv', station: 'ts', week: 1, rng: 'off', seed: null,
      generated_at: '2026-06-15T00:00:00Z',
      // Slots count from 1, as in a real trace.
      slots: track_ids.map((track_id, i) => ({
        slot: i + 1, unit: 'slot', row: 1, block: i + 1,
        source: 'A', strategy: 'sequential', pool_index: i,
        track_id, track_name: `T${track_id}`, track_artist: 'Artist',
        reason: 'next in A', excluded: [],
      })),
    };
  }
  const anEdit = [{ kind: 'patchBlock', address: { slot_index: 1 }, fields: { size: 1 } }];

  function renderSongs(overrides: Record<string, unknown> = {}) {
    return renderWithEvents(TracePane, {
      trace: tr([1, 2, 3, 4]),
      previewing: false,
      errorMessage: null,
      template: template(),
      savedSidecar: sidecar(),
      stagedSidecar: sidecar(),
      ...overrides,
    });
  }

  it('opens on Songs: the week as songs, grouped by section', () => {
    renderSongs();
    expect(screen.getByTestId('trace-tab-songs')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('trace-tab-slots')).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByTestId('trace-songs')).toBeInTheDocument();
    expect(screen.queryByTestId('trace-slots')).toBeNull();
    expect(screen.getAllByTestId('song-section').map((h) => h.textContent)).toEqual([
      expect.stringContaining('Morning'), expect.stringContaining('Evening'),
    ]);
    expect(screen.getAllByTestId('song-row')).toHaveLength(4);
  });

  it('a song click selects its slot (the page jumps to its block)', async () => {
    const { on } = renderSongs();
    let slotIndex: number | null = null;
    on('select', (e: CustomEvent<{ slotIndex: number }>) => { slotIndex = e.detail.slotIndex; });
    await fireEvent.click(screen.getAllByTestId('song-row')[2]);
    expect(slotIndex).toBe(3);
  });

  it('a song asks for its rule\'s panel too; a slot row only jumps', async () => {
    const { on } = renderSongs();
    const got: Array<{ slotIndex: number; open?: boolean }> = [];
    on('select', (e: CustomEvent<{ slotIndex: number; open?: boolean }>) => got.push(e.detail));
    await fireEvent.click(screen.getAllByTestId('song-row')[0]);
    expect(got).toEqual([{ slotIndex: 1, open: true }]);
    cleanup();
    const slots = renderSongs({ tab: 'slots' });
    const got2: Array<{ slotIndex: number; open?: boolean }> = [];
    slots.on('select', (e: CustomEvent<{ slotIndex: number; open?: boolean }>) => got2.push(e.detail));
    await fireEvent.click(screen.getAllByTestId('trace-row')[0]);
    expect(got2).toEqual([{ slotIndex: 1 }]);
  });

  it('while a rule\'s panel is open its songs are highlighted, and the one picked is marked', async () => {
    // Each of the four songs has its own rule here.
    const { rerender } = renderSongs();
    const rows = () => screen.getAllByTestId('song-row');
    await fireEvent.click(rows()[1]);
    // The page opens song 2's rule: it's marked, others with its rule highlighted.
    await rerender({ selectedRef: { kind: 'atomic-ref', slot_index: 1 } });
    expect(rows()[1]).toHaveAttribute('aria-pressed', 'true');
    expect(rows()[1]).toHaveClass('related');
    expect(rows()[0]).not.toHaveClass('related');
    // Another rule opened (its chip clicked): the picked song is unmarked.
    await rerender({ selectedRef: { kind: 'atomic-ref', slot_index: 3 } });
    expect(rows()[1]).toHaveAttribute('aria-pressed', 'false');
    expect(rows()[3]).toHaveClass('related');
    // Panel closed: nothing highlighted.
    await rerender({ selectedRef: null });
    expect(screen.queryAllByTestId('song-row').filter((r) => r.classList.contains('related'))).toHaveLength(0);
  });

  it('the tabs ask the page to switch (it owns and remembers the choice)', async () => {
    const { on } = renderSongs();
    const asked: string[] = [];
    on('tabChange', (e: CustomEvent<{ tab: string }>) => asked.push(e.detail.tab));
    await fireEvent.click(screen.getByTestId('trace-tab-slots'));
    await fireEvent.click(screen.getByTestId('trace-tab-songs'));  // already showing: nothing
    expect(asked).toEqual(['slots']);
  });

  it('Left and Right move between the tabs, as tabs do', async () => {
    const { on } = renderSongs();
    const asked: string[] = [];
    on('tabChange', (e: CustomEvent<{ tab: string }>) => asked.push(e.detail.tab));
    const songs = screen.getByTestId('trace-tab-songs');
    expect(songs).toHaveAttribute('tabindex', '0');
    expect(screen.getByTestId('trace-tab-slots')).toHaveAttribute('tabindex', '-1');
    await fireEvent.keyDown(songs, { key: 'ArrowRight' });
    await fireEvent.keyDown(songs, { key: 'ArrowLeft' });
    expect(asked).toEqual(['slots', 'slots']);  // two of them, wrapping round
    expect(document.activeElement).toBe(screen.getByTestId('trace-tab-slots'));
  });

  it('the tab panel is labelled by its tab', () => {
    renderSongs({ tab: 'slots' });
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'trace-tab-slots');
    expect(screen.getByTestId('trace-slots')).toBeInTheDocument();
    expect(screen.queryByTestId('trace-songs')).toBeNull();
  });

  it('with edits queued, songs that differ from the saved week are marked, and can be shown alone', () => {
    renderSongs({ trace: tr([1, 99, 3, 4]), savedTrace: tr([1, 2, 3, 4]), opLog: anEdit });
    expect(screen.getAllByTestId('song-change')).toHaveLength(1);
    expect(screen.getByTestId('song-only-changed')).toBeInTheDocument();
  });

  it('with nothing queued there is nothing to mark (and no "only changed")', () => {
    renderSongs({ trace: tr([1, 99, 3, 4]), savedTrace: tr([1, 2, 3, 4]), opLog: [] });
    expect(screen.queryByTestId('song-change')).toBeNull();
    expect(screen.queryByTestId('song-only-changed')).toBeNull();
  });

  it('the rules come from the staged template the preview was made from', () => {
    // Song 2's rule retyped to pick and previewed: the trace says pick.
    const staged = template();
    staged.layout.flat_slots![1] = { source: 'A', strategy: 'pick', size: 1 } as never;
    const previewed = tr([1, 2, 3, 4]);
    previewed.slots[1] = { ...previewed.slots[1], strategy: 'pick' };
    renderSongs({ trace: previewed, stagedTemplate: staged });
    expect(screen.getAllByTestId('song-rule')[1]).toHaveTextContent('Picked from A');
    expect(screen.queryByTestId('song-rule-changed')).toBeNull();
    cleanup();
    // Read against the saved template instead, it would look out of date.
    renderSongs({ trace: previewed });
    expect(screen.getByTestId('song-rule-changed')).toBeInTheDocument();
  });

  it("if the saved week can't be previewed, Songs says why nothing is marked", () => {
    renderSongs({ opLog: anEdit, savedTrace: null, savedTraceError: 'pool A is missing' });
    expect(screen.getByTestId('trace-songs-compare-error')).toHaveTextContent(
      "Couldn't compare with the saved week, so changed songs aren't marked: pool A is missing");
    cleanup();
    // Compare ticked: its own error line says it (once, not twice).
    renderSongs({ opLog: anEdit, savedTrace: null, savedTraceError: 'pool A is missing', showDiff: true });
    expect(screen.queryByTestId('trace-songs-compare-error')).toBeNull();
    expect(screen.getByTestId('trace-diff-error')).toBeInTheDocument();
    cleanup();
    // Nothing queued: nothing to compare.
    renderSongs({ opLog: [], savedTraceError: 'pool A is missing' });
    expect(screen.queryByTestId('trace-songs-compare-error')).toBeNull();
  });

  it('before the first preview: a note, not an empty list', () => {
    renderSongs({ trace: null });
    expect(screen.getByTestId('trace-empty')).toHaveTextContent(/songs show here/);
  });
});
