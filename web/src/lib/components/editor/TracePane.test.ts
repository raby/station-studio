import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import TracePane from './TracePane.svelte';
import type { Trace } from '$lib/api';


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
    render(TracePane, { trace: null, previewing: false, errorMessage: null });
    expect(screen.getByTestId('trace-empty')).toHaveTextContent(
      /Edit a chip/,
    );
    // No slot rows.
    expect(screen.queryAllByTestId('trace-row')).toHaveLength(0);
  });

  it('renders the previewing indicator when previewing=true', () => {
    render(TracePane, { trace: null, previewing: true, errorMessage: null });
    expect(screen.getByTestId('trace-status-previewing')).toHaveTextContent(
      /Previewing/,
    );
    // Empty-state hidden while previewing.
    expect(screen.queryByTestId('trace-empty')).not.toBeInTheDocument();
  });

  it('renders the trace slots when a trace is supplied', () => {
    const trace = makeTrace(3);
    render(TracePane, { trace, previewing: false, errorMessage: null });

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
    render(TracePane, {
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
    const { component } = render(TracePane, {
      trace, previewing: false, errorMessage: null,
    });
    let selectedSlotIndex: number | null = null;
    component.$on('select', (e: CustomEvent<{ slotIndex: number }>) => {
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
    render(TracePane, {
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
    render(TracePane, {
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
    render(TracePane, {
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
    render(TracePane, {
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
    render(TracePane, {
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
    render(TracePane, {
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
    const { component } = render(TracePane, {
      trace: staged,
      savedTrace: saved,
      showDiff: false,
      previewing: false,
      errorMessage: null,
      weekValue: 1,
      savedTraceError: null,
    });
    let dispatched: { on: boolean } | null = null;
    component.$on('showDiffChange', (e: CustomEvent<{ on: boolean }>) => {
      dispatched = e.detail;
    });
    const toggle = screen.getByTestId('trace-show-diff');
    await fireEvent.click(toggle);
    expect(dispatched).toEqual({ on: true });
  });

  it('savedTraceError + showDiff=false: NO banner (don\'t leak the error when diff is off)', () => {
    const staged = traceFor([1, 2, 3]);
    render(TracePane, {
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
    render(TracePane, {
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
    render(TracePane, {
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
