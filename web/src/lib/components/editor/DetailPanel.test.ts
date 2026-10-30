import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import { renderWithEvents } from '$lib/renderWithEvents';
import '@testing-library/jest-dom/vitest';
import { tick } from 'svelte';
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
    // The place in plain words; the template's address on hover.
    expect(screen.getByTestId('detail-address')).toHaveTextContent('Slot 1');
    expect(screen.getByTestId('detail-address')).toHaveAttribute('title', 'slot_index=0');
    // The rule in words at the top, the strategy by its plain name.
    expect(screen.getByTestId('detail-rule')).toHaveTextContent('Next 5 in order from A');
    expect(screen.getByTestId('detail-strategy')).toHaveTextContent('In order (sequential)');
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
    expect(screen.getByTestId('detail-address')).toHaveTextContent('200 songs, one rule');
    expect(screen.getByTestId('detail-size')).toHaveTextContent('200 × 1');
    expect(screen.getByTestId('detail-strategy')).toHaveTextContent('weighted');
    // §16.2.4 c2: macros are now EDITABLE. The form below shows the
    // strategy's editable fields (here: `window` int + `allowlist_sources`
    // string_list). The read-only spec dump from §16.2.3 was removed
    // since the form covers the same fields.
    expect(screen.getByTestId('edit-form')).toBeInTheDocument();
    expect(screen.getByTestId('macro-fanout-warning')).toHaveTextContent(
      /all 200 songs this rule makes/i,
    );
    // window field rendered as int input seeded with 7.
    expect(screen.getByTestId('input-window')).toHaveValue(7);
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
      /isn't in the station any more/i,
    );
  });

  it('clicking the close button dispatches `close`', async () => {
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    const onClose = vi.fn();
    const { on } = renderWithEvents(DetailPanel, {
      selected: { ref, section: SECTION },
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    on('close', onClose);
    await fireEvent.click(screen.getByTestId('detail-close'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Escape key dispatches `close`', async () => {
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    const onClose = vi.fn();
    const { on } = renderWithEvents(DetailPanel, {
      selected: { ref, section: SECTION },
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    on('close', onClose);
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

  it('pick block now shows allowlist_sources field (§16.2.4 D3)', () => {
    // §16.2.4 D3: pick + window strategies pick up `allowlist_sources`
    // (string_list, optional). The "no editable parameters" placeholder
    // from §16.2.3 no longer applies to pick.
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 1 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.queryByTestId('no-params')).toBeNull();
    expect(screen.getByTestId('field-allowlist_sources')).toBeInTheDocument();
    // The chip-multi input renders a text input under the testIdPrefix.
    expect(screen.getByTestId('input-allowlist_sources-input')).toBeInTheDocument();
    expect(screen.getByTestId('detail-save')).toBeInTheDocument();
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
    const { on } = renderWithEvents(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    on('save', (e) => onSave(e.detail));
    await fireEvent.input(screen.getByTestId('input-genre'), { target: { value: 'New' } });
    await fireEvent.click(screen.getByTestId('detail-save'));
    expect(onSave).toHaveBeenCalledTimes(1);
    const detail = onSave.mock.calls[0][0];
    expect(detail.ref).toEqual({ kind: 'atomic-ref', slot_index: 0 });
    // Sends all editable fields; genre updated, exclusion_window unchanged.
    expect(detail.fields).toEqual({ genre: 'New', exclusion_window: 8 });
  });

  it('editing an INT field to a new number saves it (c1 review 🔴-1)', async () => {
    // Regression guard: Svelte's <input type=number> binding makes the
    // value a NUMBER, not a string. The earlier onSave did
    // `(value ?? '').trim()` → `(10).trim()` → TypeError, so saving
    // after editing any int field crashed + no `save` fired.
    const onSave = vi.fn();
    const { on } = renderWithEvents(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    on('save', (e) => onSave(e.detail));
    // exclusion_window is an int field; 8 → 10.
    await fireEvent.input(screen.getByTestId('input-exclusion_window'), {
      target: { value: '10' },
    });
    await fireEvent.click(screen.getByTestId('detail-save'));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0].fields).toEqual({ genre: 'Old', exclusion_window: 10 });
  });

  it('returning an int field to its original value clears dirty (c1 review 🟡-4)', async () => {
    render(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    const input = screen.getByTestId('input-exclusion_window');
    const save = screen.getByTestId('detail-save');
    await fireEvent.input(input, { target: { value: '10' } });
    expect(save).toBeEnabled();
    // Back to the seeded value — should read as clean, not dirty.
    await fireEvent.input(input, { target: { value: '8' } });
    expect(save).toBeDisabled();
  });

  it('clearing an OPTIONAL field sends null in the payload', async () => {
    const onSave = vi.fn();
    const { on } = renderWithEvents(DetailPanel, {
      selected: { ref: { kind: 'atomic-ref', slot_index: 0 }, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    on('save', (e) => onSave(e.detail));
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

  it('macro selection NOW shows the edit form (§16.2.4 D1)', () => {
    // §16.2.4 D1 inverts the §16.2.3 behaviour: macros are editable
    // (edit-once-applies-to-all). The form fans out via a `macroSave`
    // event with `{macroId, fields}` (NOT `save`/`ref`).
    const ref: MacroPlaceholder = { kind: 'macro-placeholder', macro_id: 'm1', pick_in_macro: 1 };
    render(DetailPanel, {
      selected: { ref, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('edit-form')).toBeInTheDocument();
    expect(screen.getByTestId('macro-fanout-warning')).toBeInTheDocument();
    // No retype panel on macros — that's per-block (PATCH /blocks/retype).
    expect(screen.queryByTestId('retype-toggle')).toBeNull();
  });

  it('macro selection with an unknown macro_id renders no edit form (defensive)', async () => {
    // Slice-review sweep honesty: the c2 review #6 saveStateError
    // guard in onSave defends against a `detail.macro = null` +
    // `detail.block` truthy + Save click race, but with today's
    // resolution (detail.block = macro?.spec ?? null) `macro` and
    // `block` are null together. So the Save path itself can't be
    // reached when macro is missing. The guard costs nothing and
    // survives a future `detail` refactor that decouples them.
    //
    // What this test ACTUALLY pins: an unknown macro_id resolves
    // detail.block = null, so the edit form doesn't render at all
    // (the existing "engine block not found" warning path takes
    // over). Useful regression cover against a future sidecar-
    // resolution change that would let a missing macro_id silently
    // open an empty form.
    const ref: MacroPlaceholder = {
      kind: 'macro-placeholder', macro_id: 'ghost', pick_in_macro: 1,
    };
    render(DetailPanel, {
      selected: { ref, section: ESEC },
      template: makeEditTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.queryByTestId('edit-form')).toBeNull();
    expect(screen.queryByTestId('save-state-error')).toBeNull();
  });

  it('c2 review #1: editValues re-seed when block.strategy changes (same ref, post-retype)', async () => {
    // c2 review #1: seededFor key includes block strategy+source so
    // a refetch after retype (selected.ref unchanged, template
    // mutated) re-seeds editValues for the NEW strategy's fields.
    // Render mix slot (genre + exclusion_window), then swap the
    // template to a jingle block at the same slot_index → form
    // should now show only `genre`.
    const ref: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    const sidecar = makeSidecar();
    const { rerender } = renderWithEvents(DetailPanel, {
      selected: { ref, section: ESEC },
      template: makeEditTemplate(), sidecar,
    });
    expect(screen.getByTestId('input-genre')).toHaveValue('Old');
    expect(screen.getByTestId('input-exclusion_window')).toBeInTheDocument();

    // Swap template: slot 0 is now jingle. Selection ref is the same
    // object reference. The block content (strategy/source) shifted.
    const t2 = makeEditTemplate();
    t2.layout.flat_slots![0] = {
      source: 'A', strategy: 'jingle', size: 1, genre: 'Bumper',
    };
    await rerender({ template: t2 });
    await tick();
    // jingle has only `genre` editable → exclusion_window disappears,
    // genre re-seeds to the new block's value (proves the reseed
    // actually fired vs sticking with 'Old' from the prior block).
    expect(screen.queryByTestId('input-exclusion_window')).toBeNull();
    expect(screen.getByTestId('input-genre')).toHaveValue('Bumper');
  });
});


// ---- §16.2.5 c2 review #1 fixes ----------------------------------------


function makeStructTemplate(): StationTemplate {
  return {
    station: 'st', name: 'St',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'slot', unit_labels: [], row_start_offsets: [],
      flat_slots: [
        { source: 'A', strategy: 'jingle', size: 1, genre: 'G' },
        { source: 'A', strategy: 'jingle', size: 4, genre: 'G' },
        { source: 'A', strategy: 'jingle', size: 8, genre: 'G' },
      ],
    },
  };
}

function makeStructSidecar(): Sidecar {
  return {
    schema_version: 1,
    sections: [{
      id: 'lead', name: 'Lead',
      blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'atomic', slot_index: 1 },
        { kind: 'atomic', slot_index: 2 },
      ],
    }],
    macros: [],
    layout_order: ['lead'],
  };
}

const STRUCT_SEC: SidecarSection = makeStructSidecar().sections[0];


describe('<DetailPanel> — c2 review #1 fixes', () => {
  beforeEach(() => cleanup());

  it('newSize re-seeds when the selected slot changes', async () => {
    // c2 review #1: prior `if (newSize === null) ...` only fired
    // once → switching from slot 1 (size=4) to slot 2 (size=8)
    // left the input showing 4. Selection-keyed reseed must
    // populate the input with each new slot's currentSize.
    const tpl = makeStructTemplate();
    const sc = makeStructSidecar();
    const { rerender } = renderWithEvents(DetailPanel, {
      selected: {
        ref: { kind: 'atomic-ref', slot_index: 1 } as AtomicRef,
        section: STRUCT_SEC,
      },
      template: tpl, sidecar: sc,
    });
    expect((screen.getByTestId('structural-size-input') as HTMLInputElement)
      .value).toBe('4');
    // Flip to slot 2 (size=8).
    await rerender({
      selected: {
        ref: { kind: 'atomic-ref', slot_index: 2 } as AtomicRef,
        section: STRUCT_SEC,
      },
    });
    await tick();
    expect((screen.getByTestId('structural-size-input') as HTMLInputElement)
      .value).toBe('8');
  });

  it('Delete-slot is two-step — first click arms, second commits', async () => {
    const handler = vi.fn();
    const { on } = renderWithEvents(DetailPanel, {
      selected: {
        ref: { kind: 'atomic-ref', slot_index: 0 } as AtomicRef,
        section: STRUCT_SEC,
      },
      template: makeStructTemplate(), sidecar: makeStructSidecar(),
    });
    on('structuralEdit', handler);
    const btn = screen.getByTestId('structural-delete');
    expect(btn).toHaveTextContent('Remove slot');
    // First click: arms (text/state changes), no dispatch.
    await fireEvent.click(btn);
    expect(handler).not.toHaveBeenCalled();
    expect(btn).toHaveTextContent(/click again to remove/i);
    expect(btn).toHaveAttribute('data-armed', 'true');
    // Second click within the 3s window: commits.
    await fireEvent.click(btn);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail.edit).toEqual({
      op: 'remove', slot_index: 0,
    });
  });

  it('Delete-slot arm-state resets when selection changes', async () => {
    const handler = vi.fn();
    const { on, rerender } = renderWithEvents(DetailPanel, {
      selected: {
        ref: { kind: 'atomic-ref', slot_index: 0 } as AtomicRef,
        section: STRUCT_SEC,
      },
      template: makeStructTemplate(), sidecar: makeStructSidecar(),
    });
    on('structuralEdit', handler);
    // Arm on slot 0.
    await fireEvent.click(screen.getByTestId('structural-delete'));
    expect(screen.getByTestId('structural-delete')).toHaveAttribute(
      'data-armed', 'true',
    );
    // Switch to slot 1 — arm state must reset so a single click on
    // the new selection doesn't immediately commit.
    await rerender({
      selected: {
        ref: { kind: 'atomic-ref', slot_index: 1 } as AtomicRef,
        section: STRUCT_SEC,
      },
    });
    await tick();
    expect(screen.getByTestId('structural-delete')).toHaveAttribute(
      'data-armed', 'false',
    );
    // Click on the new selection arms, doesn't commit.
    await fireEvent.click(screen.getByTestId('structural-delete'));
    expect(handler).not.toHaveBeenCalled();
  });

  it('Delete-slot arm survives refetch-driven `selected` identity churn', async () => {
    // Slice-review sweep: the prior `$: if (selected !== null)
    // deleteArmedAt = null;` reset on any new object identity for
    // `selected`. A background refetch (parent rebuilds ExpandedRef
    // on every sidecar reload) would silently disarm the operator's
    // first click. The new key-based reset compares the LOGICAL chip
    // (sectionId + slot_index) so refetches leave the arm intact.
    const handler = vi.fn();
    const ref0: AtomicRef = { kind: 'atomic-ref', slot_index: 0 };
    const { on, rerender } = renderWithEvents(DetailPanel, {
      selected: { ref: ref0, section: STRUCT_SEC },
      template: makeStructTemplate(), sidecar: makeStructSidecar(),
    });
    on('structuralEdit', handler);
    // Arm.
    await fireEvent.click(screen.getByTestId('structural-delete'));
    expect(screen.getByTestId('structural-delete')).toHaveAttribute(
      'data-armed', 'true',
    );
    // Simulate a refetch: parent passes a NEW object identity for
    // the SAME logical chip (same section.id + slot_index).
    await rerender({
      selected: {
        ref: { kind: 'atomic-ref', slot_index: 0 } as AtomicRef,
        section: { ...STRUCT_SEC },
      },
      sidecar: makeStructSidecar(),  // also a fresh sidecar
      template: makeStructTemplate(),
    });
    await tick();
    // Arm state must SURVIVE the refetch.
    expect(screen.getByTestId('structural-delete')).toHaveAttribute(
      'data-armed', 'true',
    );
    // Second click commits — proving the arm wasn't silently reset.
    await fireEvent.click(screen.getByTestId('structural-delete'));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('Delete-slot commit dispatches `close` so the stale selection clears', async () => {
    // Slice-review sweep: after a successful remove, the parent
    // refetches; `selected.ref.slot_index` is then stale (the slot
    // it pointed to no longer exists; flat_slots shifted). The
    // structural panel's `currentSize` would read what's now a
    // DIFFERENT block's size — a follow-up Apply-resize would
    // mutate the wrong block. Dispatching `close` on commit forces
    // the operator to re-click to continue editing.
    const onStructural = vi.fn();
    const onClose = vi.fn();
    const { on } = renderWithEvents(DetailPanel, {
      selected: {
        ref: { kind: 'atomic-ref', slot_index: 0 } as AtomicRef,
        section: STRUCT_SEC,
      },
      template: makeStructTemplate(), sidecar: makeStructSidecar(),
    });
    on('structuralEdit', onStructural);
    on('close', onClose);
    await fireEvent.click(screen.getByTestId('structural-delete'));  // arm
    await fireEvent.click(screen.getByTestId('structural-delete'));  // commit
    expect(onStructural).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
