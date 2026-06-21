import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import { tick } from 'svelte';
import '@testing-library/jest-dom/vitest';
import ChipFlow from './ChipFlow.svelte';
import { makeDragEvent } from './__tests__/drag-fixture';
import { CHIP_DRAG_MIME } from '$lib/drag-drop';
import {
  DEFAULT_VIEW_MODE,
  type Sidecar,
  type SidecarSection,
  type StationTemplate,
  type ViewModeState,
} from '$lib/editor';

const VM: ViewModeState = DEFAULT_VIEW_MODE;


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
    render(ChipFlow, { section, template, sidecar, viewMode: VM });
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
    render(ChipFlow, { section, template, sidecar, viewMode: VM });
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
    render(ChipFlow, { section, template, sidecar, viewMode: VM });
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
    render(ChipFlow, { section, template, sidecar, viewMode: VM });
    // 2 chips (one per offset's first-of-span macro placeholder),
    // each carrying its own ×3 count badge.
    expect(screen.getAllByTestId('chip')).toHaveLength(2);
    expect(screen.getAllByTestId('chip-count')).toHaveLength(2);
    for (const badge of screen.getAllByTestId('chip-count')) {
      expect(badge).toHaveTextContent('×3');
    }
  });

  describe('§16.2.5.t22 drag-target lifecycle hardening', () => {
    function _setupDroppableFlow(): HTMLElement {
      const template = makeFlatTemplate();
      const sidecar: Sidecar = {
        schema_version: 1, sections: [], macros: [], layout_order: [],
      };
      const section: SidecarSection = {
        id: 's', name: 'S',
        blocks: [
          { kind: 'atomic', slot_index: 0 },
          { kind: 'atomic', slot_index: 1 },
          { kind: 'atomic', slot_index: 2 },
        ],
      };
      render(ChipFlow, { section, template, sidecar, viewMode: VM });
      return screen.getByTestId('chip-flow');
    }

    async function _flushFrame(): Promise<void> {
      // jsdom's requestAnimationFrame is timer-backed; one
      // setTimeout(0) is enough to fire the next-tick rAF callback,
      // and `tick()` flushes Svelte's reactive update to the DOM
      // attribute that mirrors `dropIndicatorAt`.
      await new Promise((r) => setTimeout(r, 16));
      await tick();
    }

    it('(b) dragleave cleanup runs even when dataTransfer.types is blanked', async () => {
      // Browsers (Firefox / Safari) blank `dataTransfer.types` on
      // dragleave across some boundaries. Pre-.t22 the
      // `_hasChipPayload` gate returned early on those events,
      // leaving the indicator pinned. Verify cleanup runs anyway.
      const flow = _setupDroppableFlow();

      // Prime the drag state: dragover with the chip MIME sets
      // pending + schedules a rAF that writes dropIndicatorAt.
      // The chip rects in jsdom are 0×0 so the cursor (clientX=0)
      // falls AFTER every midpoint → indicator lands at
      // rendered.length (= 3), serialised as the string "3".
      flow.dispatchEvent(makeDragEvent('dragover', CHIP_DRAG_MIME, '{}'));
      await _flushFrame();
      expect(flow.dataset.dropAt).toBe('3');

      // Dragleave WITHOUT the chip MIME (simulates the masked-types
      // case). Pre-fix this would return at the gate without
      // cleanup. Post-fix the cleanup runs unconditionally and the
      // attribute disappears (null → no attribute → undefined).
      flow.dispatchEvent(new Event('dragleave', { bubbles: true }));
      await _flushFrame();
      expect(flow.dataset.dropAt).toBeUndefined();
    });

    it('(c) cross-section dragend (window-level) clears stale indicator', async () => {
      // ESC-cancel of a drag whose source lives in another section
      // never fires `dragend` on this ChipFlow (the event targets
      // the source's container). Pre-.t22 the indicator pinned
      // until the next drag cycle moved through this container.
      // Post-.t22 the window-level listener tears it down.
      const flow = _setupDroppableFlow();

      flow.dispatchEvent(makeDragEvent('dragover', CHIP_DRAG_MIME, '{}'));
      await _flushFrame();
      expect(flow.dataset.dropAt).toBe('3');

      window.dispatchEvent(new Event('dragend'));
      await _flushFrame();
      expect(flow.dataset.dropAt).toBeUndefined();
    });
  });

  it('renders the empty flow without crashing on an empty section', () => {
    const template = makeFlatTemplate();
    const sidecar: Sidecar = {
      schema_version: 1, sections: [], macros: [], layout_order: [],
    };
    const section: SidecarSection = { id: 's', name: 'S', blocks: [] };
    render(ChipFlow, { section, template, sidecar, viewMode: VM });
    expect(screen.queryAllByTestId('chip')).toHaveLength(0);
    expect(screen.getByTestId('chip-flow')).toBeInTheDocument();
  });
});
