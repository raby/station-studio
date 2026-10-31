import { describe, expect, it } from 'vitest';
import { editFromPanel, editsFromPanel, refToAddress } from './panelEdits';

const flat = { kind: 'atomic-ref', slot_index: 3 } as const;
const row = { kind: 'atomic-ref', row_idx: 2, block_idx: 1 } as const;
const macro = { kind: 'macro-placeholder', macro_id: 'm', pick_in_macro: 1 } as const;

describe('refToAddress', () => {
  it('a flat block by slot, a row block by its place in the row (every row), a macro by nothing', () => {
    expect(refToAddress(flat)).toEqual({ slot_index: 3 });
    expect(refToAddress(row)).toEqual({ block_idx: 1 });
    expect(refToAddress(macro)).toBeNull();
    expect(refToAddress({ kind: 'atomic-ref' })).toBeNull();
  });
});

describe('editFromPanel', () => {
  it('save → patchBlock, copying the fields', () => {
    const fields = { size: 4 };
    const edit = editFromPanel({ type: 'save', detail: { ref: flat, fields } });
    expect(edit).toEqual({ kind: 'patchBlock', address: { slot_index: 3 }, fields: { size: 4 } });
    fields.size = 9;
    expect(edit).toMatchObject({ fields: { size: 4 } }); // a copy, not the event's object
  });

  it('save with no fields, or of a macro chip, queues nothing', () => {
    expect(editFromPanel({ type: 'save', detail: { ref: flat, fields: {} } })).toBeNull();
    expect(editFromPanel({ type: 'save', detail: { ref: macro, fields: { size: 2 } } })).toBeNull();
  });

  it('retype → retypeBlock', () => {
    expect(editFromPanel({
      type: 'retype',
      detail: { ref: row, newStrategy: 'pick', newSource: null, newFields: { position: 1 } },
    })).toEqual({
      kind: 'retypeBlock', address: { block_idx: 1 }, newStrategy: 'pick', newSource: null, newFields: { position: 1 },
    });
    expect(editFromPanel({
      type: 'retype', detail: { ref: macro, newStrategy: 'pick', newSource: null, newFields: {} },
    })).toBeNull();
  });

  it('macroSave → patchMacro (nothing when no fields changed)', () => {
    expect(editFromPanel({ type: 'macroSave', detail: { macroId: 'm', fields: { genre: 'Day' } } }))
      .toEqual({ kind: 'patchMacro', macroId: 'm', fields: { genre: 'Day' } });
    expect(editFromPanel({ type: 'macroSave', detail: { macroId: 'm', fields: {} } })).toBeNull();
  });

  it('blockMove → moveBlock', () => {
    expect(editFromPanel({ type: 'blockMove', detail: { ref: flat, targetSectionId: 's2', targetIndex: 0 } }))
      .toEqual({ kind: 'moveBlock', address: { slot_index: 3 }, targetSectionId: 's2', targetIndex: 0 });
    expect(editFromPanel({ type: 'blockMove', detail: { ref: macro, targetSectionId: 's2', targetIndex: null } }))
      .toBeNull();
  });

  it('structuralEdit → structuralBlockEdit', () => {
    expect(editFromPanel({ type: 'structuralEdit', detail: { edit: { op: 'size', slot_index: 3, new_size: 2 } } }))
      .toEqual({ kind: 'structuralBlockEdit', edit: { op: 'size', slot_index: 3, new_size: 2 } });
  });
});

describe('editsFromPanel', () => {
  it('the rule, then each identical rule ticked', () => {
    const other = { kind: 'atomic-ref', slot_index: 7 } as const;
    expect(editsFromPanel({ type: 'save', detail: { ref: flat, fields: { size: 4 }, also: [other] } })).toEqual([
      { kind: 'patchBlock', address: { slot_index: 3 }, fields: { size: 4 } },
      { kind: 'patchBlock', address: { slot_index: 7 }, fields: { size: 4 } },
    ]);
    expect(editsFromPanel({
      type: 'retype',
      detail: { ref: row, newStrategy: 'pick', newSource: null, newFields: {}, also: [{ kind: 'atomic-ref', row_idx: 0, block_idx: 4 }] },
    }).map((e) => (e as { address: unknown }).address)).toEqual([{ block_idx: 1 }, { block_idx: 4 }]);
    expect(editsFromPanel({ type: 'macroSave', detail: { macroId: 'm', fields: { window: 2 }, alsoMacroIds: ['n', 'o'] } }))
      .toEqual(['m', 'n', 'o'].map((macroId) => ({ kind: 'patchMacro', macroId, fields: { window: 2 } })));
  });

  it('nothing for nothing, and no twins means just the one', () => {
    expect(editsFromPanel({ type: 'save', detail: { ref: flat, fields: {}, also: [flat] } })).toEqual([]);
    expect(editsFromPanel({ type: 'macroSave', detail: { macroId: 'm', fields: { window: 2 } } })).toHaveLength(1);
  });
});
