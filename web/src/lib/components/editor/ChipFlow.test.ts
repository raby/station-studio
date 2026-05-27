import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import ChipFlow from './ChipFlow.svelte';
import type { Sidecar, SidecarSection, StationTemplate } from '$lib/editor';


function makeFlatTemplate(): StationTemplate {
  return {
    station: 'ts',
    name: 'Test',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'slot',
      unit_labels: [],
      row_start_offsets: [],
      flat_slots: [
        { source: 'A', strategy: 'pick', size: 1 },
        { source: 'A', strategy: 'pick', size: 1 },
        { source: 'A', strategy: 'pick', size: 1 },
      ],
    },
  };
}


describe('<ChipFlow>', () => {
  beforeEach(() => cleanup());

  it('renders one chip per atomic block', () => {
    const template = makeFlatTemplate();
    const sidecar: Sidecar = {
      schema_version: 1,
      sections: [],
      macros: [],
      layout_order: [],
    };
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'atomic', slot_index: 1 },
        { kind: 'atomic', slot_index: 2 },
      ],
    };
    render(ChipFlow, { section, template, sidecar });
    expect(screen.getAllByTestId('chip')).toHaveLength(3);
  });

  it('collapses contiguous macro placeholders to one chip with ×N count', () => {
    // The macro repeats 5 times → expandSection emits 5
    // MacroPlaceholders. ChipFlow renders ONLY the first.
    const template = makeFlatTemplate();
    const sidecar: Sidecar = {
      schema_version: 1,
      sections: [],
      macros: [
        {
          id: 'm', name: 'm', repeat: 5,
          spec: { source: 'A', strategy: 'pick', size: 1 },
        },
      ],
      layout_order: [],
    };
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [{ kind: 'macro', ref: 'm' }],
    };
    render(ChipFlow, { section, template, sidecar });
    expect(screen.getAllByTestId('chip')).toHaveLength(1);
    expect(screen.getByTestId('chip-count')).toHaveTextContent('×5');
  });

  it('interleaves atomic + macro chips (atomic chips between macro spans render separately)', () => {
    // [atomic, macro×3, atomic, macro×3] — first macro span
    // collapses to 1; second span (different macro_id sequence
    // boundary marker) collapses to 1; total = 4 chips visible.
    const template = makeFlatTemplate();
    const sidecar: Sidecar = {
      schema_version: 1,
      sections: [],
      macros: [
        { id: 'm', repeat: 3, spec: { source: 'A', strategy: 'pick', size: 1 } },
      ],
      layout_order: [],
    };
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'macro', ref: 'm' },
        { kind: 'atomic', slot_index: 1 },
        { kind: 'macro', ref: 'm' },
      ],
    };
    render(ChipFlow, { section, template, sidecar });
    // 1 atomic + 1 macro-collapsed + 1 atomic + 1 macro-collapsed
    // (each macro span emits placeholders; ChipFlow folds within a
    // contiguous span but NOT across atomic boundaries).
    expect(screen.getAllByTestId('chip')).toHaveLength(4);
    // Two ×3 count badges (one per macro span).
    expect(screen.getAllByTestId('chip-count')).toHaveLength(2);
  });

  it('renders one chip PER offset-instance when a macro is inside repeat_with_offsets (c3 review 🔴-1)', () => {
    // c3 review 🔴-1 regression guard: ChipFlow's earlier
    // macro-collapse used lastMacroId tracking, which incorrectly
    // folded ALL placeholders across offsets into a single chip —
    // hiding offset-instance distinctions.
    //
    // Fixture: section with 2 offsets + 1 macro block (repeat=3).
    // expandSection emits 6 placeholders: [pick=1,2,3, 1,2,3].
    // Correct: render the 2 first-of-each-span placeholders =
    //   2 chips (each shows ×3 from macro.repeat).
    // Buggy old behaviour: render 1 chip (×3) — eliding one
    //   offset-instance entirely.
    const template = makeFlatTemplate();
    const sidecar: Sidecar = {
      schema_version: 1, sections: [],
      macros: [
        {
          id: 'm', name: 'm', repeat: 3,
          spec: { source: 'A', strategy: 'pick', size: 1 },
        },
      ],
      layout_order: [],
    };
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [{ kind: 'macro', ref: 'm' }],
      repeat_with_offsets: [0, 100],
    };
    render(ChipFlow, { section, template, sidecar });
    // 2 chips (one per offset's first-of-span macro placeholder),
    // each carrying its own ×3 count badge.
    expect(screen.getAllByTestId('chip')).toHaveLength(2);
    expect(screen.getAllByTestId('chip-count')).toHaveLength(2);
    for (const badge of screen.getAllByTestId('chip-count')) {
      expect(badge).toHaveTextContent('×3');
    }
  });

  it('renders the empty flow without crashing on an empty section', () => {
    const template = makeFlatTemplate();
    const sidecar: Sidecar = {
      schema_version: 1, sections: [], macros: [], layout_order: [],
    };
    const section: SidecarSection = { id: 's', name: 'S', blocks: [] };
    render(ChipFlow, { section, template, sidecar });
    expect(screen.queryAllByTestId('chip')).toHaveLength(0);
    expect(screen.getByTestId('chip-flow')).toBeInTheDocument();
  });
});
