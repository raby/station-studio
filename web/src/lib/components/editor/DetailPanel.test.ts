import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import DetailPanel from './DetailPanel.svelte';
import type {
  AtomicRef,
  MacroPlaceholder,
  Sidecar,
  SidecarSection,
  StationTemplate,
} from '$lib/editor';


function makeTemplate(): StationTemplate {
  return {
    station: 'ts', name: 'Test',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'slot', unit_labels: [], row_start_offsets: [],
      flat_slots: [
        { source: 'A', strategy: 'sequential', size: 5, cursor_id: 'NIGHT_1' },
      ],
    },
  };
}


function makeSidecar(): Sidecar {
  return {
    schema_version: 1, sections: [],
    macros: [{
      id: 'm1', name: 'M1', repeat: 200,
      spec: { source: 'A', strategy: 'weighted', size: 1, window: 7 },
    }],
    layout_order: [],
  };
}


const SECTION: SidecarSection = { id: 's1', name: 'Section 1', blocks: [] };


describe('<DetailPanel>', () => {
  beforeEach(() => cleanup());

  it('renders nothing when `selected` is null', () => {
    render(DetailPanel, {
      selected: null, template: makeTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.queryByTestId('detail-panel')).toBeNull();
  });

  it('renders an atomic block with all engine fields', () => {
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    render(DetailPanel, {
      selected: { ref, section: SECTION },
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    const panel = screen.getByTestId('detail-panel');
    expect(panel).toHaveAttribute('data-kind', 'atomic');
    expect(screen.getByTestId('detail-section-name')).toHaveTextContent('Section 1');
    expect(screen.getByTestId('detail-strategy')).toHaveTextContent('sequential');
    expect(screen.getByTestId('detail-source')).toHaveTextContent('A');
    expect(screen.getByTestId('detail-address')).toHaveTextContent('slot_index=0');
    // cursor_id from the engine block surfaces.
    expect(panel).toHaveTextContent('cursor_id');
    expect(panel).toHaveTextContent('NIGHT_1');
  });

  it('renders a macro placeholder with macro name + repeat + spec fields', () => {
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder', macro_id: 'm1', pick_in_macro: 1,
    };
    render(DetailPanel, {
      selected: { ref, section: SECTION },
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    const panel = screen.getByTestId('detail-panel');
    expect(panel).toHaveAttribute('data-kind', 'macro');
    expect(panel).toHaveTextContent('M1');
    expect(screen.getByTestId('detail-repeat')).toHaveTextContent('200');
    expect(screen.getByTestId('detail-strategy')).toHaveTextContent('weighted');
    expect(panel).toHaveTextContent('window');
    expect(panel).toHaveTextContent('7');
  });

  it('falls back to section.id when section.name is omitted', () => {
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    const section: SidecarSection = { id: 'unnamed-1', blocks: [] };
    render(DetailPanel, {
      selected: { ref, section },
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('detail-section-name')).toHaveTextContent('unnamed-1');
  });

  it('shows a "not found" warning when atomic ref is out-of-bounds', () => {
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 999 };
    render(DetailPanel, {
      selected: { ref, section: SECTION },
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('detail-panel')).toHaveTextContent(
      /engine block not found/i,
    );
  });

  it('clicking the close button dispatches `close`', async () => {
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    const onClose = vi.fn();
    const { component } = render(DetailPanel, {
      selected: { ref, section: SECTION },
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    component.$on('close', onClose);
    await fireEvent.click(screen.getByTestId('detail-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape key dispatches `close`', async () => {
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    const onClose = vi.fn();
    const { component } = render(DetailPanel, {
      selected: { ref, section: SECTION },
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    component.$on('close', onClose);
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
