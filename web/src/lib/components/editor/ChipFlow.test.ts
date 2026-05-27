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
