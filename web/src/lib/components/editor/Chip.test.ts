import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import Chip from './Chip.svelte';
import type {
  AtomicRef,
  MacroPlaceholder,
  Sidecar,
  StationTemplate,
} from '$lib/editor';

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
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro() });

    const chip = screen.getByTestId('chip');
    expect(chip).toHaveAttribute('data-chip-kind', 'atomic');
    expect(chip).toHaveAttribute('data-source', 'A');
    expect(chip).toHaveTextContent('Pk');
    // No ×N count for atomic chips.
    expect(screen.queryByTestId('chip-count')).toBeNull();
  });

  it('uses the source colour declared on the template', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro() });
    const chip = screen.getByTestId('chip');
    // Colour is exposed via inline style for downstream CSS.
    expect(chip.getAttribute('style')).toMatch(/--chip-color:\s*#c0392b/i);
  });

  it('renders sequential atomic with S:{cursor_id} label', () => {
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 1 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro() });
    expect(screen.getByTestId('chip')).toHaveTextContent('S:NIGHT_1');
  });

  it('renders a macro placeholder as a wider chip with ×N count', () => {
    const template = makeFlatTemplate();
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder',
      macro_id: 'm1',
      pick_in_macro: 1,
    };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro() });

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
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro() });
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
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro() });
    const title = screen.getByTestId('chip').getAttribute('title') ?? '';
    expect(title).toContain('strategy: sequential');
    expect(title).toContain('cursor_id: NIGHT_1');
  });

  it('renders a defensive ? on out-of-bounds atomic ref', () => {
    // A validated sidecar wouldn't produce this; the chip shouldn't
    // crash on a degenerate fixture either.
    const template = makeFlatTemplate();
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 999 };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro() });
    expect(screen.getByTestId('chip')).toHaveTextContent('?');
  });

  it('renders a defensive ? on a dangling macro ref', () => {
    const template = makeFlatTemplate();
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder', macro_id: 'ghost', pick_in_macro: 1,
    };
    render(Chip, { ref, template, sidecar: makeSidecarWithMacro() });
    const chip = screen.getByTestId('chip');
    expect(chip).toHaveTextContent('?');
    expect(chip.getAttribute('title') ?? '').toContain('ghost');
  });
});
