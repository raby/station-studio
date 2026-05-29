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
    expect(panel).toHaveAttribute('data-kind', 'atomic');
    expect(screen.getByTestId('detail-address')).toHaveTextContent('slot_index=0');
    // §16.2.3 c1: cursor_id is now editable, surfacing in the edit
    // form's <select> (read-only dl row moved into the form). The
    // makeTemplate() has no cursors_init, so the select renders with
    // the current value present but no enumerated options — the value
    // still round-trips through the input's value.
    expect(screen.getByTestId('edit-form')).toBeInTheDocument();
    expect(screen.getByTestId('input-cursor_id')).toBeInTheDocument();
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


// ---- §16.2.3 c1 — atomic edit form --------------------------------------


function makeEditTemplate(): StationTemplate {
  // flat_slots station with one block of each editable strategy + a
  // cursors_init so the sequential cursor select has options.
  return {
    station: 'ed', name: 'Edit',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'slot', unit_labels: [], row_start_offsets: [],
      cursors_init: { NIGHT_1: 0, DAY: 10 },
      flat_slots: [
        { source: 'A', strategy: 'mix', size: 1, genre: 'Old', exclusion_window: 8 },
        { source: 'A', strategy: 'pick', size: 1 },
        { source: 'A', strategy: 'sequential', size: 1, cursor_id: 'NIGHT_1' },
      ],
    },
  };
}

function rowTemplateEditTemplate(): StationTemplate {
  return {
    station: 'rt', name: 'RT',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'day', unit_labels: ['Mon', 'Tue'], row_start_offsets: [0, 10],
      cursors_init: {},
      row_template: [{ source: 'A', strategy: 'jingle', size: 1, genre: 'Day' }],
    },
  };
}

const ESEC: SidecarSection = { id: 's', name: 'Sec', blocks: [] };

describe('<DetailPanel> — §16.2.3 c1 edit form', () => {
  beforeEach(() => cleanup());

  it('renders strategy-aware fields for a mix block (genre + exclusion_window)', () => {
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('edit-form')).toBeInTheDocument();
    expect(screen.getByTestId('input-genre')).toHaveValue('Old');
    // number input value comes back as a number via testing-library
    expect(screen.getByTestId('input-exclusion_window')).toHaveValue(8);
  });

  it('shows "no editable parameters" for a pick block', () => {
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 1 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('no-params')).toBeInTheDocument();
    expect(screen.queryByTestId('detail-save')).toBeNull();
  });

  it('renders cursor_id as a <select> of cursors_init keys', () => {
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 2 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    const sel = screen.getByTestId('input-cursor_id') as HTMLSelectElement;
    const opts = Array.from(sel.options).map((o) => o.value);
    expect(opts).toContain('NIGHT_1');
    expect(opts).toContain('DAY');
  });

  it('Save is disabled until a field changes (dirty)', async () => {
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    const save = screen.getByTestId('detail-save');
    expect(save).toBeDisabled();
    await fireEvent.input(screen.getByTestId('input-genre'), { target: { value: 'New' } });
    expect(save).toBeEnabled();
  });

  it('shows a validation error + keeps Save disabled when a required field is cleared', async () => {
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    await fireEvent.input(screen.getByTestId('input-genre'), { target: { value: '' } });
    expect(screen.getByTestId('error-genre')).toBeInTheDocument();
    expect(screen.getByTestId('detail-save')).toBeDisabled();
  });

  it('dispatches `save` with the edited fields on submit', async () => {
    const onSave = vi.fn();
    const { component } = render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    component.$on('save', (e) => onSave(e.detail));
    await fireEvent.input(screen.getByTestId('input-genre'), { target: { value: 'New' } });
    await fireEvent.click(screen.getByTestId('detail-save'));
    expect(onSave).toHaveBeenCalledTimes(1);
    const detail = onSave.mock.calls[0][0];
    expect(detail.ref).toEqual({ kind: 'atomic-ref', slot_index: 0 });
    // Sends all editable fields; genre updated, exclusion_window unchanged.
    expect(detail.fields).toEqual({ genre: 'New', exclusion_window: 8 });
  });

  it('clearing an OPTIONAL field sends null in the payload', async () => {
    const onSave = vi.fn();
    const { component } = render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    component.$on('save', (e) => onSave(e.detail));
    // exclusion_window is optional → clearing it sends null.
    await fireEvent.input(screen.getByTestId('input-exclusion_window'), { target: { value: '' } });
    await fireEvent.click(screen.getByTestId('detail-save'));
    expect(onSave.mock.calls[0][0].fields).toEqual({ genre: 'Old', exclusion_window: null });
  });

  it('shows the shared-row-template warning for a row_template block', () => {
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', row_idx: 1, block_idx: 0 }, section: ESEC },
      template: rowTemplateEditTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('shared-block-warning')).toBeInTheDocument();
  });

  it('does NOT show the shared warning for a flat_slots block', () => {
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.queryByTestId('shared-block-warning')).toBeNull();
  });

  it('macro selection shows NO edit form (macro edit is §16.2.4)', () => {
    const ref: MacroPlaceholder = { kind: 'macro-placeholder', macro_id: 'm1', pick_in_macro: 1 };
    render(DetailPanel, {
      selected: { ref, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.queryByTestId('edit-form')).toBeNull();
  });
});
