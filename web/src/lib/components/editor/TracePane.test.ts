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
