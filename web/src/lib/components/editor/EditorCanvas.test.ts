import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import EditorCanvas from './EditorCanvas.svelte';
import type { Sidecar, StationTemplate } from '$lib/editor';


function makeTemplate(flatSlotCount: number = 3): StationTemplate {
  return {
    station: 'ts', name: 'Test',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'slot', unit_labels: [], row_start_offsets: [],
      flat_slots: Array(flatSlotCount).fill(
        { source: 'A', strategy: 'pick', size: 1 },
      ),
    },
  };
}


function makeSidecar(over: Partial<Sidecar> = {}): Sidecar {
  return {
    schema_version: 1,
    sections: [],
    macros: [],
    layout_order: [],
    ...over,
  };
}


describe('<EditorCanvas>', () => {
  beforeEach(() => cleanup());

  it('renders sections in layout_order', () => {
    const template = makeTemplate(3);
    const sidecar = makeSidecar({
      sections: [
        { id: 'b', name: 'Beta', blocks: [{ kind: 'atomic', slot_index: 0 }] },
        { id: 'a', name: 'Alpha', blocks: [{ kind: 'atomic', slot_index: 1 }] },
      ],
      layout_order: ['a', 'b'], // reversed from sections[] order
    });
    render(EditorCanvas, { template, sidecar });

    const sections = screen.getAllByTestId('section');
    // Matches layout_order, not sections[].
    expect(sections[0].getAttribute('data-section-id')).toBe('a');
    expect(sections[1].getAttribute('data-section-id')).toBe('b');
  });

  it('skips ids in layout_order that have no matching section', () => {
    const template = makeTemplate(2);
    const sidecar = makeSidecar({
      sections: [
        { id: 'real', name: 'Real', blocks: [{ kind: 'atomic', slot_index: 0 }] },
      ],
      layout_order: ['ghost', 'real'],
    });
    render(EditorCanvas, { template, sidecar });
    const sections = screen.getAllByTestId('section');
    expect(sections).toHaveLength(1);
    expect(sections[0].getAttribute('data-section-id')).toBe('real');
  });

  it('starts all sections EXPANDED when total chips ≤ threshold', () => {
    const template = makeTemplate(3);
    const sidecar = makeSidecar({
      sections: [
        {
          id: 's', name: 'S',
          blocks: [
            { kind: 'atomic', slot_index: 0 },
            { kind: 'atomic', slot_index: 1 },
            { kind: 'atomic', slot_index: 2 },
          ],
        },
      ],
      layout_order: ['s'],
    });
    render(EditorCanvas, { template, sidecar });
    const canvas = screen.getByTestId('editor-canvas');
    expect(canvas.getAttribute('data-total-chips')).toBe('3');
    expect(canvas.getAttribute('data-initially-collapsed')).toBe('false');
    // Body visible.
    expect(screen.getByTestId('section-body')).toBeInTheDocument();
    // No large-station hint.
    expect(screen.queryByTestId('large-station-hint')).toBeNull();
  });

  it('starts all sections COLLAPSED + shows hint when total chips > threshold', () => {
    // Build a section with 501 atomic blocks (one over the limit).
    const template = makeTemplate(501);
    const sidecar = makeSidecar({
      sections: [
        {
          id: 's', name: 'S',
          blocks: Array.from({ length: 501 }, (_, i) => ({
            kind: 'atomic' as const, slot_index: i,
          })),
        },
      ],
      layout_order: ['s'],
    });
    render(EditorCanvas, { template, sidecar });
    const canvas = screen.getByTestId('editor-canvas');
    expect(canvas.getAttribute('data-total-chips')).toBe('501');
    expect(canvas.getAttribute('data-initially-collapsed')).toBe('true');
    // Body hidden.
    expect(screen.queryByTestId('section-body')).toBeNull();
    // Hint visible with locale-formatted count.
    const hint = screen.getByTestId('large-station-hint');
    expect(hint).toHaveTextContent('501');
  });

  it('renders an empty-canvas message when layout_order is empty', () => {
    const template = makeTemplate(0);
    const sidecar = makeSidecar({ sections: [], layout_order: [] });
    render(EditorCanvas, { template, sidecar });
    expect(screen.getByTestId('empty-canvas')).toBeInTheDocument();
    expect(screen.queryByTestId('section')).toBeNull();
  });

  it('renders the synthesized trivial sidecar (one All-slots section)', () => {
    // Smoke test: what the c1 endpoint sends when no
    // stations/{slug}.editor.json exists.
    const template = makeTemplate(3);
    const sidecar = makeSidecar({
      _comment: 'Synthesized trivial sidecar',
      sections: [
        {
          id: 'all-slots', name: 'All slots',
          blocks: [
            { kind: 'atomic', slot_index: 0 },
            { kind: 'atomic', slot_index: 1 },
            { kind: 'atomic', slot_index: 2 },
          ],
        },
      ],
      layout_order: ['all-slots'],
    });
    render(EditorCanvas, { template, sidecar });
    expect(screen.getByTestId('section-name')).toHaveTextContent('All slots');
    expect(screen.getAllByTestId('chip')).toHaveLength(3);
  });
});
