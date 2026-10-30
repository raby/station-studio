import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import { renderWithEvents } from '$lib/renderWithEvents';
import '@testing-library/jest-dom/vitest';
import Chip from './Chip.svelte';
import {
  DEFAULT_VIEW_MODE,
  type AtomicRef,
  type MacroPlaceholder,
  type Sidecar,
  type StationTemplate,
  type ViewModeState,
} from '$lib/editor';

const VM: ViewModeState = DEFAULT_VIEW_MODE;

/**
 * Pin <Chip>'s read-only render branches:
 *   - atomic ref → compact strategy label + source color
 *   - atomic ref pointing at row_template
 *   - macro placeholder → wider chip + ×N count badge
 *   - dangling refs → defensive '?' render (doesn't crash)
 */


function makeFlatTemplate(): StationTemplate {
  return {
    station: 'ts',
    name: 'Test',
    sources: [
      { name: 'A', colour: '#c0392b', role: 'content' },
      { name: 'B', colour: '#2980b9', role: 'content' },
    ],
    layout: {
      unit: 'slot',
      unit_labels: [],
      row_start_offsets: [],
      flat_slots: [
        { source: 'A', strategy: 'pick', size: 1 },
        { source: 'B', strategy: 'sequential', size: 1, cursor_id: 'NIGHT_1' },
      ],
    },
  };
}


function makeSidecarWithMacro(): Sidecar {
  return {
    schema_version: 1,
    sections: [],
    macros: [
      {
        id: 'm1',
        name: '200 × weighted',
        repeat: 200,
        spec: { source: 'B', strategy: 'weighted', size: 1 },
      },
    ],
    layout_order: [],
  };
}


describe('<Chip>', () => {
  beforeEach(() => cleanup());

  it('renders an atomic ref with compact strategy label', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });

    const chip = screen.getByTestId('chip');
    expect(chip).toHaveAttribute('data-chip-kind', 'atomic');
    expect(chip).toHaveAttribute('data-source', 'A');
    expect(chip).toHaveTextContent('Pk');
    // No ×N count for atomic chips.
    expect(screen.queryByTestId('chip-count')).toBeNull();
  });

  it('§16.2.6.t3: carries data-ref-key matching refKey(ref) for trace-jump lookup', () => {
    // The page's handleTraceSelect resolves a trace row → ExpandedRef
    // → refKey → document.querySelector('[data-ref-key=...]'). If the
    // attribute drifts (renamed, dropped, formatter change) the jump
    // silently no-ops. Pin the three shapes here.
    const template = makeFlatTemplate();
    const sidecar = makeSidecarWithMacro();
    const atomicFlat: AtomicRef = { kind: 'atomic-ref', slot_index: 7 };
    const { unmount } = render(Chip, { ref: atomicFlat, template, sidecar, viewMode: VM });
    expect(screen.getByTestId('chip')).toHaveAttribute(
      'data-ref-key', 'atomic:slot=7',
    );
    unmount();

    const atomicRow: AtomicRef = { kind: 'atomic-ref', row_idx: 2, block_idx: 5 };
    const { unmount: unmount2 } = render(Chip, { ref: atomicRow, template, sidecar, viewMode: VM });
    expect(screen.getByTestId('chip')).toHaveAttribute(
      'data-ref-key', 'atomic:row=2:block=5',
    );
    unmount2();

    const macro: MacroPlaceholder = {
      kind: 'macro-placeholder', macro_id: 'm1', pick_in_macro: 1,
    };
    render(Chip, { ref: macro, template, sidecar, viewMode: VM });
    expect(screen.getByTestId('chip')).toHaveAttribute(
      'data-ref-key', 'macro:m1:pick=1',
    );
  });

  it('uses the source colour declared on the template', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });
    const chip = screen.getByTestId('chip');
    // Colour is exposed via inline style for downstream CSS.
    expect(chip.getAttribute('style')).toMatch(/--chip-color:\s*#c0392b/i);
  });

  it('renders sequential atomic with S:{cursor_id} label', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 1 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });
    expect(screen.getByTestId('chip')).toHaveTextContent('S:NIGHT_1');
  });

  it('renders a macro placeholder as a wider chip with ×N count', () => {
    const template = makeFlatTemplate();
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder',
      macro_id: 'm1',
      pick_in_macro: 1,
    };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });

    const chip = screen.getByTestId('chip');
    expect(chip).toHaveAttribute('data-chip-kind', 'macro');
    expect(chip).toHaveClass('macro');
    // Macro spec is weighted → 'W' compact label.
    expect(chip).toHaveTextContent('W');
    // ×200 count badge.
    expect(screen.getByTestId('chip-count')).toHaveTextContent('×200');
  });

  it('macro chip tooltip includes name + repeat + strategy', () => {
    const template = makeFlatTemplate();
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder', macro_id: 'm1', pick_in_macro: 1,
    };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });
    const chip = screen.getByTestId('chip');
    const title = chip.getAttribute('title') ?? '';
    expect(title).toContain('200 × weighted'); // name
    expect(title).toContain('repeat: 200');
    expect(title).toContain('strategy: weighted');
    expect(title).toContain('source: B');
  });

  it('atomic chip tooltip lists the full verbose label', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 1 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });
    const title = screen.getByTestId('chip').getAttribute('title') ?? '';
    expect(title).toContain('strategy: sequential');
    expect(title).toContain('cursor_id: NIGHT_1');
  });

  it('renders a defensive ? on out-of-bounds atomic ref', () => {
    // A validated sidecar wouldn't produce this; the chip shouldn't
    // crash on a degenerate fixture either.
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 999 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });
    expect(screen.getByTestId('chip')).toHaveTextContent('?');
  });

  it('renders a defensive ? on a dangling macro ref', () => {
    const template = makeFlatTemplate();
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder', macro_id: 'ghost', pick_in_macro: 1,
    };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });
    const chip = screen.getByTestId('chip');
    expect(chip).toHaveTextContent('?');
    expect(chip.getAttribute('title') ?? '').toContain('ghost');
  });

  // ---- c4 keyboard a11y (§16.2.2.t6 promote) + select event --------

  it('chip is a native <button> (focusable + keyboard-activatable)', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM });
    const chip = screen.getByTestId('chip');
    expect(chip.tagName).toBe('BUTTON');
    expect(chip).toHaveAttribute('type', 'button');
  });

  it('clicking the chip dispatches a `select` event carrying the ref', async () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 1 };
    const onSelect = vi.fn();
    const { on } = renderWithEvents(Chip, {
      ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM,
    });
    on('select', (e: Event) => {
      onSelect((e as CustomEvent<{ ref: AtomicRef }>).detail);
    });
    await fireEvent.click(screen.getByTestId('chip'));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toEqual({ ref });
  });

  // ---- c4 view-mode toggles ------------------------------------------

  it('verbose label mode shows strategy + source', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    render(Chip, {
      ref, template, sidecar: makeSidecarWithMacro(),
      viewMode: { ...VM, labelMode: 'verbose' },
    });
    expect(screen.getByTestId('chip')).toHaveTextContent('pick (A)');
  });

  it('colorBy=strategy uses the strategy palette, not the source colour', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 }; // pick
    render(Chip, {
      ref, template, sidecar: makeSidecarWithMacro(),
      viewMode: { ...VM, colorBy: 'strategy' },
    });
    const chip = screen.getByTestId('chip');
    // pick → strategy palette colour (#7d3c98), not source #c0392b.
    expect(chip.getAttribute('style')).toMatch(/--chip-color:\s*#7d3c98/i);
  });

  it('macro chip applies the per-mode CSS class', () => {
    const template = makeFlatTemplate();
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder', macro_id: 'm1', pick_in_macro: 1,
    };
    for (const mode of ['wider-chip', 'stacked', 'count-badge'] as const) {
      const { unmount } = render(Chip, {
        ref, template, sidecar: makeSidecarWithMacro(),
        viewMode: { ...VM, macroVisual: mode },
      });
      const chip = screen.getByTestId('chip');
      // Convert kebab to the class suffix the component uses.
      const classSuffix = mode === 'count-badge' ? 'macro-badge' : `macro-${mode.split('-')[0]}`;
      expect(chip.className).toContain(classSuffix);
      unmount();
    }
  });
});


// ---- §16.2.5 c2 — drag-source tests -------------------------------------
// c2 review #4 #6: shared synthetic-event builder.
import { makeDragEvent } from './__tests__/drag-fixture';


describe('<Chip> — c2 drag source', () => {
  it('atomic chips are draggable + carry the ref JSON in dataTransfer', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    render(Chip, {
      ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM,
    });
    const chip = screen.getByTestId('chip');
    expect(chip).toHaveAttribute('draggable', 'true');
    const e = makeDragEvent('dragstart');
    chip.dispatchEvent(e);
    const dt = (e as unknown as { dataTransfer: { getData: (k: string) => string } })
      .dataTransfer;
    const payload = dt.getData('application/x-station-chip-ref');
    expect(JSON.parse(payload)).toEqual(ref);
  });

  it('macro chips are NOT draggable (preserves section-position math)', () => {
    const template = makeFlatTemplate();
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder', macro_id: 'm1', pick_in_macro: 1,
    };
    render(Chip, {
      ref, template, sidecar: makeSidecarWithMacro(), viewMode: VM,
    });
    const chip = screen.getByTestId('chip');
    expect(chip).toHaveAttribute('draggable', 'false');
  });
});
