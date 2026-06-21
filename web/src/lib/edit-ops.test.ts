import { describe, it, expect } from 'vitest';
import {
  applyEdit, applyEdits, attributeOpsToSlot, replayOpLog,
  type BlockAddress, type Edit, type ReplayDeps,
} from './edit-ops';
import type { StationTemplate } from './editor';


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
        { source: 'A', strategy: 'pick', size: 2 },
        { source: 'A', strategy: 'sequential', size: 1 },
      ],
    },
  };
}


function makeRowTemplate(): StationTemplate {
  return {
    station: 'ts',
    name: 'Test',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'slot',
      unit_labels: [],
      row_start_offsets: [],
      row_template: [
        { source: 'A', strategy: 'pick', size: 1 },
        { source: 'A', strategy: 'pick', size: 1 },
      ],
    },
  };
}


describe('applyEdit — patchBlock', () => {
  it('merges field updates on a flat_slots block at the addressed slot_index', () => {
    const template = makeFlatTemplate();
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 1 },
      fields: { size: 5, strategy: 'window' },
    };
    const { template: next } = applyEdit(template, {}, op);
    // The targeted block reflects merged fields.
    expect(next.layout.flat_slots![1]).toMatchObject({
      source: 'A', strategy: 'window', size: 5,
    });
    // Siblings untouched.
    expect(next.layout.flat_slots![0]).toEqual(template.layout.flat_slots![0]);
    expect(next.layout.flat_slots![2]).toEqual(template.layout.flat_slots![2]);
  });

  it('null field value removes the key (matches backend parameter-only contract)', () => {
    const template: StationTemplate = {
      ...makeFlatTemplate(),
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [],
        flat_slots: [
          { source: 'A', strategy: 'pick', size: 1, exclusion_window: 4 },
        ],
      },
    };
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 0 },
      fields: { exclusion_window: null },
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next.layout.flat_slots![0]).not.toHaveProperty('exclusion_window');
    expect(next.layout.flat_slots![0]).toMatchObject({
      source: 'A', strategy: 'pick', size: 1,
    });
  });

  it('targets row_template blocks via block_idx (the §16.2.3 D-shared-block contract)', () => {
    const template = makeRowTemplate();
    const op: Edit = {
      kind: 'patchBlock',
      address: { block_idx: 0 },
      fields: { size: 7 },
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next.layout.row_template![0]).toMatchObject({
      source: 'A', strategy: 'pick', size: 7,
    });
    expect(next.layout.row_template![1]).toEqual(template.layout.row_template![1]);
  });

  it('PURE: does not mutate the input template', () => {
    const template = makeFlatTemplate();
    const snapshot = JSON.parse(JSON.stringify(template));
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 0 },
      fields: { size: 99 },
    };
    applyEdit(template, {}, op);
    expect(template).toEqual(snapshot);
  });

  it('returns the input sidecar unchanged (patchBlock affects template only)', () => {
    const template = makeFlatTemplate();
    const sidecar = { schema_version: 1, sections: [], layout_order: [] };
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 0 },
      fields: { size: 99 },
    };
    const result = applyEdit(template, sidecar, op);
    expect(result.sidecar).toBe(sidecar);  // identity, not just equality
  });

  it('no-ops on out-of-range slot_index (defensive — opLog should never have invalid addresses but verify)', () => {
    const template = makeFlatTemplate();
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 999 },
      fields: { size: 99 },
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next.layout.flat_slots).toEqual(template.layout.flat_slots);
  });

  it('no-ops on address shape that does not match layout form', () => {
    // flat_slots station + a block_idx-only address.
    const template = makeFlatTemplate();
    const op: Edit = {
      kind: 'patchBlock',
      address: { block_idx: 0 },
      fields: { size: 99 },
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next.layout.flat_slots).toEqual(template.layout.flat_slots);
  });

  it('no-ops on XOR-violating template (BOTH flat_slots AND row_template populated)', () => {
    // c3 review #1 fix #10: engine enforces flat_slots XOR
    // row_template; a corrupted station with both populated would
    // otherwise silently misroute the edit to flat_slots (the
    // if-flat-first branch). Defensive: return template unchanged
    // + let the engine's parse_template surface the XOR error
    // when the preview job runs.
    const template: StationTemplate = {
      ...makeFlatTemplate(),
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [],
        flat_slots: [{ source: 'A', strategy: 'pick', size: 1 }],
        row_template: [{ source: 'A', strategy: 'pick', size: 2 }],
      },
    };
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 0 },
      fields: { size: 99 },
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next).toBe(template);  // identity — unchanged
  });
});


describe('applyEdit — patchMacro (§16.2.6 c3b)', () => {
  const baseSidecar = {
    schema_version: 1,
    sections: [{ id: 'all', name: 'All', blocks: [{ kind: 'atomic', slot_index: 0 }] }],
    macros: [
      {
        id: 'm1', name: 'Jingle', repeat: 3,
        spec: { source: 'A', strategy: 'pick', size: 1, exclusion_window: 4 },
      },
      {
        id: 'm2', name: 'Other', repeat: 1,
        spec: { source: 'A', strategy: 'window', size: 2 },
      },
    ],
    layout_order: ['all'],
  };

  it('merges field updates into the targeted macro spec', () => {
    const template = makeFlatTemplate();
    const op: Edit = {
      kind: 'patchMacro',
      macroId: 'm1',
      fields: { size: 5, exclusion_window: null, strategy: 'window' },
    };
    const { sidecar: next } = applyEdit(template, baseSidecar, op);
    const nextSidecar = next as typeof baseSidecar;
    const target = nextSidecar.macros.find((m) => m.id === 'm1')!;
    expect(target.spec).toMatchObject({
      source: 'A', strategy: 'window', size: 5,
    });
    expect(target.spec).not.toHaveProperty('exclusion_window');
    // Other macro untouched.
    expect(nextSidecar.macros.find((m) => m.id === 'm2')!.spec)
      .toEqual(baseSidecar.macros.find((m) => m.id === 'm2')!.spec);
    // Top-level macro fields (name, repeat) untouched on target too.
    expect(target.name).toBe('Jingle');
    expect(target.repeat).toBe(3);
  });

  it('PURE: does not mutate the input sidecar', () => {
    const template = makeFlatTemplate();
    const snapshot = JSON.parse(JSON.stringify(baseSidecar));
    const op: Edit = {
      kind: 'patchMacro',
      macroId: 'm1',
      fields: { size: 99 },
    };
    applyEdit(template, baseSidecar, op);
    expect(baseSidecar).toEqual(snapshot);
  });

  it('returns the input template unchanged', () => {
    const template = makeFlatTemplate();
    const op: Edit = {
      kind: 'patchMacro',
      macroId: 'm1',
      fields: { size: 99 },
    };
    const result = applyEdit(template, baseSidecar, op);
    expect(result.template).toBe(template);
  });

  it('no-ops on unknown macroId (defensive — opLog shouldn\'t have invalid ids)', () => {
    const template = makeFlatTemplate();
    const op: Edit = {
      kind: 'patchMacro',
      macroId: 'nope',
      fields: { size: 99 },
    };
    const result = applyEdit(template, baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('no-ops when target macro has no spec (review #1 fix #2: prevents partial-spec replay)', () => {
    const template = makeFlatTemplate();
    const sidecarWithBrokenMacro = {
      schema_version: 1, sections: [], macros: [
        { id: 'm1', name: 'NoSpec', repeat: 1 },  // spec missing
      ], layout_order: [],
    };
    const op: Edit = {
      kind: 'patchMacro',
      macroId: 'm1',
      fields: { size: 5 },
    };
    const result = applyEdit(template, sidecarWithBrokenMacro, op);
    // Identity — the corrupted macro stays as-is so the engine's
    // validate_sidecar surfaces the spec-missing error rather than
    // applyEdit synthesizing a partial spec.
    expect(result.sidecar).toBe(sidecarWithBrokenMacro);
  });

  it('no-ops when target macro spec is an array (review #1 fix #4)', () => {
    // typeof [] === 'object' so the pre-fix object-check accepted
    // arrays + spreading them produced an index-keyed dict.
    const template = makeFlatTemplate();
    const sidecarWithArraySpec = {
      schema_version: 1, sections: [], macros: [
        { id: 'm1', name: 'ArrSpec', repeat: 1, spec: ['weird'] },
      ], layout_order: [],
    };
    const op: Edit = {
      kind: 'patchMacro',
      macroId: 'm1',
      fields: { size: 5 },
    };
    const result = applyEdit(template, sidecarWithArraySpec, op);
    expect(result.sidecar).toBe(sidecarWithArraySpec);
  });

  it('no-ops on sidecar missing macros array', () => {
    const template = makeFlatTemplate();
    const sidecarNoMacros = { schema_version: 1, sections: [], layout_order: [] };
    const op: Edit = {
      kind: 'patchMacro',
      macroId: 'm1',
      fields: { size: 99 },
    };
    const result = applyEdit(template, sidecarNoMacros, op);
    expect(result.sidecar).toBe(sidecarNoMacros);
  });
});


describe('applyEdit — renameSection (§16.2.6 c3b)', () => {
  const baseSidecar = {
    schema_version: 1,
    sections: [
      { id: 'morning', name: 'Morning slot', blocks: [] },
      { id: 'evening', name: 'Evening slot', blocks: [] },
    ],
    macros: [],
    layout_order: ['morning', 'evening'],
  };

  it('updates section.name for the targeted section id', () => {
    const template = makeFlatTemplate();
    const op: Edit = {
      kind: 'renameSection',
      sectionId: 'morning',
      name: 'Breakfast',
    };
    const { sidecar: next } = applyEdit(template, baseSidecar, op);
    const nextSidecar = next as typeof baseSidecar;
    expect(nextSidecar.sections.find((s) => s.id === 'morning')!.name).toBe('Breakfast');
    expect(nextSidecar.sections.find((s) => s.id === 'evening')!.name).toBe('Evening slot');
    // layout_order unchanged — only name flips.
    expect(nextSidecar.layout_order).toEqual(baseSidecar.layout_order);
  });

  it('PURE: does not mutate the input sidecar', () => {
    const template = makeFlatTemplate();
    const snapshot = JSON.parse(JSON.stringify(baseSidecar));
    const op: Edit = {
      kind: 'renameSection',
      sectionId: 'morning',
      name: 'X',
    };
    applyEdit(template, baseSidecar, op);
    expect(baseSidecar).toEqual(snapshot);
  });

  it('no-ops on unknown sectionId', () => {
    const template = makeFlatTemplate();
    const op: Edit = {
      kind: 'renameSection',
      sectionId: 'nope',
      name: 'X',
    };
    const result = applyEdit(template, baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });
});


describe('applyEdit — sectionCreate (§16.2.6 c3c)', () => {
  const baseSidecar = {
    schema_version: 1,
    sections: [
      { id: 's1', name: 'First', blocks: [] },
      { id: '_unsorted', blocks: [{ kind: 'atomic', slot_index: 0 }] },
    ],
    macros: [],
    layout_order: ['s1', '_unsorted'],
  };

  it('appends a new section with empty blocks + inserts id BEFORE _unsorted in layout_order', () => {
    const op: Edit = {
      kind: 'sectionCreate',
      sectionId: 'morning',
      name: 'Morning',
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), baseSidecar, op);
    const ns = next as typeof baseSidecar;
    const newSec = ns.sections.find((s) => s.id === 'morning')!;
    expect(newSec).toMatchObject({ id: 'morning', name: 'Morning', blocks: [] });
    // _unsorted stays at the end; new section is BEFORE it.
    expect(ns.layout_order).toEqual(['s1', 'morning', '_unsorted']);
  });

  it('appends to layout_order END when _unsorted is absent', () => {
    const sidecarNoUnsorted = {
      schema_version: 1,
      sections: [{ id: 's1', name: 'First', blocks: [] }],
      macros: [],
      layout_order: ['s1'],
    };
    const op: Edit = {
      kind: 'sectionCreate',
      sectionId: 'evening',
      name: null,
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), sidecarNoUnsorted, op);
    const ns = next as typeof sidecarNoUnsorted;
    expect(ns.layout_order).toEqual(['s1', 'evening']);
    // name null → no `name` key on the section.
    expect(ns.sections[1]).not.toHaveProperty('name');
  });

  it('no-ops on duplicate sectionId', () => {
    const op: Edit = { kind: 'sectionCreate', sectionId: 's1', name: 'Dup' };
    const result = applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('no-ops on reserved-prefix id (leading underscore — c3c review #1 fix #6)', () => {
    // System-bin ids (`_unsorted`, future `_archived`) are reserved
    // per §16.2.5.t2 / §16.2.5.t20. EditorCanvas's Add Section
    // input gates on the prefix at the input layer; this test pins
    // the defense-in-depth at the applyEdit layer.
    const op: Edit = {
      kind: 'sectionCreate',
      sectionId: '_archived',
      name: 'Archived',
    };
    const result = applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('PURE: input not mutated', () => {
    const snapshot = JSON.parse(JSON.stringify(baseSidecar));
    const op: Edit = { kind: 'sectionCreate', sectionId: 'x', name: 'X' };
    applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(baseSidecar).toEqual(snapshot);
  });
});


describe('applyEdit — sectionDelete (§16.2.6 c3c)', () => {
  it('removes empty section from sections + layout_order; _unsorted untouched', () => {
    const sidecar = {
      schema_version: 1,
      sections: [
        { id: 'a', blocks: [] },
        { id: 'b', blocks: [] },
        { id: '_unsorted', blocks: [{ kind: 'atomic', slot_index: 0 }] },
      ],
      macros: [],
      layout_order: ['a', 'b', '_unsorted'],
    };
    const op: Edit = { kind: 'sectionDelete', sectionId: 'a' };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), sidecar, op);
    const ns = next as typeof sidecar;
    expect(ns.sections.find((s) => s.id === 'a')).toBeUndefined();
    expect(ns.layout_order).toEqual(['b', '_unsorted']);
    // _unsorted unchanged (no blocks to re-home).
    expect(ns.sections.find((s) => s.id === '_unsorted')!.blocks)
      .toEqual(sidecar.sections.find((s) => s.id === '_unsorted')!.blocks);
  });

  it('non-empty section: blocks re-home to existing _unsorted (§16.2.5 D2)', () => {
    const sidecar = {
      schema_version: 1,
      sections: [
        { id: 'morning', blocks: [
          { kind: 'atomic', slot_index: 0 },
          { kind: 'atomic', slot_index: 1 },
        ]},
        { id: '_unsorted', blocks: [{ kind: 'atomic', slot_index: 2 }] },
      ],
      macros: [],
      layout_order: ['morning', '_unsorted'],
    };
    const op: Edit = { kind: 'sectionDelete', sectionId: 'morning' };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), sidecar, op);
    const ns = next as typeof sidecar;
    expect(ns.sections.find((s) => s.id === 'morning')).toBeUndefined();
    expect(ns.layout_order).toEqual(['_unsorted']);
    // _unsorted now carries the original block + the re-homed pair (appended at end).
    const unsorted = ns.sections.find((s) => s.id === '_unsorted')!;
    expect(unsorted.blocks).toEqual([
      { kind: 'atomic', slot_index: 2 },
      { kind: 'atomic', slot_index: 0 },
      { kind: 'atomic', slot_index: 1 },
    ]);
  });

  it('non-empty section: _unsorted CREATED on demand if absent + carries the UNSORTED_SECTION_NAME mirror (c3c review #1 fix #8)', () => {
    const sidecar = {
      schema_version: 1,
      sections: [
        { id: 'morning', blocks: [{ kind: 'atomic', slot_index: 0 }] },
      ],
      macros: [],
      layout_order: ['morning'],
    };
    const op: Edit = { kind: 'sectionDelete', sectionId: 'morning' };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), sidecar, op);
    const ns = next as {
      sections: Array<{ id: string; name?: string; blocks: unknown[] }>;
      layout_order: string[];
    };
    // _unsorted exists now + holds the orphan block + has the
    // display name field mirrored from
    // engine.editor_metadata.UNSORTED_SECTION_NAME.
    const unsorted = ns.sections.find((s) => s.id === '_unsorted')!;
    expect(unsorted).toBeDefined();
    expect(unsorted.blocks).toEqual([{ kind: 'atomic', slot_index: 0 }]);
    expect(unsorted.name).toBe('Unsorted (moved from a deleted section)');
    expect(ns.layout_order).toEqual(['_unsorted']);
  });

  it('no-ops on attempt to delete the system bin _unsorted', () => {
    const sidecar = {
      schema_version: 1,
      sections: [{ id: '_unsorted', blocks: [] }],
      macros: [],
      layout_order: ['_unsorted'],
    };
    const op: Edit = { kind: 'sectionDelete', sectionId: '_unsorted' };
    const result = applyEdit(makeFlatTemplate(), sidecar, op);
    expect(result.sidecar).toBe(sidecar);
  });

  it('no-ops on unknown sectionId', () => {
    const sidecar = {
      schema_version: 1,
      sections: [{ id: 'a', blocks: [] }],
      macros: [],
      layout_order: ['a'],
    };
    const op: Edit = { kind: 'sectionDelete', sectionId: 'nope' };
    const result = applyEdit(makeFlatTemplate(), sidecar, op);
    expect(result.sidecar).toBe(sidecar);
  });
});


describe('applyEdit — reorderSections (§16.2.6 c3c)', () => {
  const baseSidecar = {
    schema_version: 1,
    sections: [
      { id: 'a', blocks: [] },
      { id: 'b', blocks: [] },
      { id: '_unsorted', blocks: [] },
    ],
    macros: [],
    layout_order: ['a', 'b', '_unsorted'],
  };

  it('replaces layout_order with the new order', () => {
    const op: Edit = {
      kind: 'reorderSections',
      layoutOrder: ['b', 'a', '_unsorted'],
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), baseSidecar, op);
    const ns = next as typeof baseSidecar;
    expect(ns.layout_order).toEqual(['b', 'a', '_unsorted']);
    // sections array unchanged (only layout_order moves).
    expect(ns.sections).toEqual(baseSidecar.sections);
  });

  it('no-ops on length mismatch (defensive — backend rejects too)', () => {
    const op: Edit = {
      kind: 'reorderSections',
      layoutOrder: ['a', 'b'],  // missing _unsorted
    };
    const result = applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('no-ops on id-set mismatch', () => {
    const op: Edit = {
      kind: 'reorderSections',
      layoutOrder: ['a', 'b', 'newId'],  // _unsorted dropped, newId added
    };
    const result = applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('no-ops on DUPLICATE ids in the new order (c3c review #1 fix #1)', () => {
    // Pre-fix the length + set-membership check ACCEPTED an order
    // like `['a','a','b']` against `['a','b','_unsorted']`:
    // length matches (3), all members in oldSet — but the result
    // would silently drop '_unsorted' + duplicate 'a'. Tighten:
    // no-op when the new order has duplicate ids.
    const op: Edit = {
      kind: 'reorderSections',
      layoutOrder: ['a', 'a', 'b'],
    };
    const result = applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('PURE: input layoutOrder not mutated by reference identity', () => {
    const inputOrder = ['b', 'a', '_unsorted'];
    const op: Edit = { kind: 'reorderSections', layoutOrder: inputOrder };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), baseSidecar, op);
    const ns = next as typeof baseSidecar;
    // Copy made; mutating the input doesn't drift the staged state.
    inputOrder.push('mutated');
    expect(ns.layout_order).toEqual(['b', 'a', '_unsorted']);
  });
});


describe('applyEdit — moveBlock (§16.2.6 c3d)', () => {
  const baseSidecar = {
    schema_version: 1,
    sections: [
      { id: 'morning', name: 'Morning', blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'atomic', slot_index: 1 },
      ]},
      { id: 'evening', name: 'Evening', blocks: [
        { kind: 'atomic', slot_index: 2 },
      ]},
    ],
    macros: [],
    layout_order: ['morning', 'evening'],
  };

  it('cross-section move with explicit targetIndex inserts at that position', () => {
    // Move slot 0 from morning to evening at index 0 (front).
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'evening',
      targetIndex: 0,
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), baseSidecar, op);
    const ns = next as typeof baseSidecar;
    expect(ns.sections.find((s) => s.id === 'morning')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 1 },
    ]);
    expect(ns.sections.find((s) => s.id === 'evening')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 0 },
      { kind: 'atomic', slot_index: 2 },
    ]);
  });

  it('cross-section move with targetIndex=null appends to target', () => {
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'evening',
      targetIndex: null,
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), baseSidecar, op);
    const ns = next as typeof baseSidecar;
    expect(ns.sections.find((s) => s.id === 'evening')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 2 },
      { kind: 'atomic', slot_index: 0 },
    ]);
  });

  it('same-section reorder via move (drag chip to new position within section)', () => {
    // Move slot 0 within morning to position 1 (after slot 1).
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'morning',
      targetIndex: 1,
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), baseSidecar, op);
    const ns = next as typeof baseSidecar;
    expect(ns.sections.find((s) => s.id === 'morning')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 1 },
      { kind: 'atomic', slot_index: 0 },
    ]);
    // evening untouched.
    expect(ns.sections.find((s) => s.id === 'evening')!.blocks).toEqual(
      baseSidecar.sections.find((s) => s.id === 'evening')!.blocks,
    );
  });

  it('targetIndex out-of-range gets clamped to nearest valid index', () => {
    // 999 → end of target.
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'evening',
      targetIndex: 999,
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), baseSidecar, op);
    const ns = next as typeof baseSidecar;
    expect(ns.sections.find((s) => s.id === 'evening')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 2 },
      { kind: 'atomic', slot_index: 0 },
    ]);
  });

  it('no-op on unknown targetSectionId', () => {
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'nope',
      targetIndex: 0,
    };
    const result = applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('no-op on unknown source address', () => {
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 999 },
      targetSectionId: 'evening',
      targetIndex: 0,
    };
    const result = applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('c3d batch B finding #3: no-op on system-bin target (`_unsorted`)', () => {
    // System bins are auto-filled by section delete + structural add (D2);
    // backend 400s on manual moves into them. canDropOn filters client-
    // side in drag-drop, but keyboard / scripted paths bypass that.
    const sidecarWithBin = {
      ...baseSidecar,
      sections: [
        ...baseSidecar.sections,
        { id: '_unsorted', name: 'Unsorted', blocks: [] },
      ],
    };
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: '_unsorted',
      targetIndex: 0,
    };
    const result = applyEdit(makeFlatTemplate(), sidecarWithBin, op);
    expect(result.sidecar).toBe(sidecarWithBin);
  });

  it('c3d sweep S1: legacy leading-underscore section names stay movable (NOT a system bin)', () => {
    // drag-drop.ts:isSystemSection narrows to ['_unsorted'] only —
    // hand-authored legacy ids like `_archive` should NOT be trapped.
    // Earlier Batch B used `startsWith('_')` which over-rejected
    // these; sweep S1 switched to the membership check.
    const sidecarWithLegacy = {
      ...baseSidecar,
      sections: [
        ...baseSidecar.sections,
        { id: '_archive', name: 'Legacy archive', blocks: [] },
      ],
    };
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: '_archive',
      targetIndex: 0,
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), sidecarWithLegacy, op);
    const ns = next as typeof sidecarWithLegacy;
    expect(ns.sections.find((s) => s.id === '_archive')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 0 },
    ]);
  });

  it('c3d sweep S5: non-atomic block carrying the matching slot_index is skipped (future-proof)', () => {
    // Today macros don't carry slot_index, so the existing matcher
    // misses them by accident; sweep S5 adds an explicit
    // `kind !== 'atomic' → continue` so a future block kind that
    // DOES carry slot_index can't be erroneously matched. Construct
    // a synthetic non-atomic block with the same slot_index to
    // exercise the new guard.
    const sidecarWithNonAtomic = {
      ...baseSidecar,
      sections: [
        // Two sections: morning has a non-atomic block (synthetic
        // future shape) carrying slot_index: 0; evening has the
        // real atomic match. Only the atomic one should be picked
        // as the source.
        { id: 'morning', name: 'Morning', blocks: [
          { kind: 'macro', ref: 'm1', slot_index: 0 }, // future shape, ignored.
          { kind: 'atomic', slot_index: 1 },
        ]},
        { id: 'evening', name: 'Evening', blocks: [
          { kind: 'atomic', slot_index: 0 },
        ]},
      ],
    };
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'morning',
      targetIndex: 0,
    };
    const { sidecar: next } = applyEdit(makeFlatTemplate(), sidecarWithNonAtomic, op);
    const ns = next as typeof sidecarWithNonAtomic;
    // The atomic block from evening should move into morning at
    // index 0; the synthetic macro stays put in morning untouched.
    expect(ns.sections.find((s) => s.id === 'morning')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 0 },
      { kind: 'macro', ref: 'm1', slot_index: 0 },
      { kind: 'atomic', slot_index: 1 },
    ]);
    expect(ns.sections.find((s) => s.id === 'evening')!.blocks).toEqual([]);
  });

  it('c3d batch B finding #4: no-op on negative targetIndex (backend 400s)', () => {
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'evening',
      targetIndex: -1,
    };
    const result = applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(result.sidecar).toBe(baseSidecar);
  });

  it('c3d batch B finding #5: no-op when address matches blocks in multiple sections (drift)', () => {
    // Drift-rule 8 catches this at load time, but staging mode
    // shouldn't paper over it — backend 400s with "address matches
    // blocks in multiple sections" so the optimistic preview should
    // bail rather than picking the first hit.
    const drifted = {
      ...baseSidecar,
      sections: [
        { id: 'morning', name: 'Morning', blocks: [
          { kind: 'atomic', slot_index: 0 },
        ]},
        { id: 'evening', name: 'Evening', blocks: [
          { kind: 'atomic', slot_index: 0 }, // duplicate slot_index!
        ]},
      ],
    };
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'morning',
      targetIndex: 1,
    };
    const result = applyEdit(makeFlatTemplate(), drifted, op);
    expect(result.sidecar).toBe(drifted);
  });

  it('PURE: input not mutated', () => {
    const snapshot = JSON.parse(JSON.stringify(baseSidecar));
    const op: Edit = {
      kind: 'moveBlock',
      address: { slot_index: 0 },
      targetSectionId: 'evening',
      targetIndex: 0,
    };
    applyEdit(makeFlatTemplate(), baseSidecar, op);
    expect(baseSidecar).toEqual(snapshot);
  });
});


describe('applyEdit — retypeBlock (§16.2.6 c3d)', () => {
  // c3d batch C: fixture switched from `pick` (single param field) to
  // `mix` (genre + exclusion_window) so the merge-vs-allowlist tests
  // can exercise both branches: same-strategy retypes preserve mix's
  // params; cross-strategy retypes (mix → window) demonstrate the
  // allowlist filter dropping genre + exclusion_window.
  function makeRetypeTemplate(): StationTemplate {
    return {
      station: 'ts',
      name: 'Test',
      sources: [
        { name: 'A', colour: '#abc', role: 'content' },
        { name: 'B', colour: '#def', role: 'content' },
      ],
      layout: {
        unit: 'slot',
        unit_labels: [],
        row_start_offsets: [],
        flat_slots: [
          {
            source: 'A',
            strategy: 'mix',
            size: 1,
            genre: 'jazz',
            exclusion_window: 4,
          },
          { source: 'A', strategy: 'sequential', size: 2 },
        ],
      },
    };
  }

  it('same-strategy retype merges newFields + preserves other allowlist-allowed fields', () => {
    // mix → mix: genre + exclusion_window are BOTH in mix's allowlist
    // so they survive. newFields swaps exclusion_window; genre stays.
    const template = makeRetypeTemplate();
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 0 },
      newStrategy: null, // stays 'mix'
      newSource: 'B',
      newFields: { exclusion_window: 8 },
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next.layout.flat_slots![0]).toEqual({
      size: 1,
      strategy: 'mix',
      source: 'B',
      genre: 'jazz',
      exclusion_window: 8,
    });
    // Sibling unchanged.
    expect(next.layout.flat_slots![1]).toEqual(template.layout.flat_slots![1]);
  });

  it('c3d batch C finding #6: cross-strategy retype drops fields not in new strategy allowlist', () => {
    // mix → window: genre + exclusion_window aren't in window's
    // allowlist (which is just `allowlist_sources`) so both drop.
    // Mirrors backend editor.py:551-554 — pre-Batch-C, the optimistic
    // preview kept the stale fields until Save dropped them.
    const template = makeRetypeTemplate();
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 0 },
      newStrategy: 'window',
      newSource: 'B',
      newFields: { allowlist_sources: ['p1'] },
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next.layout.flat_slots![0]).toEqual({
      size: 1,
      strategy: 'window',
      source: 'B',
      allowlist_sources: ['p1'],
    });
    expect(next.layout.flat_slots![0]).not.toHaveProperty('genre');
    expect(next.layout.flat_slots![0]).not.toHaveProperty('exclusion_window');
  });

  it('c3d batch C: unknown strategy → empty allowlist → all non-structural fields drop', () => {
    // Backend fallback: EDITABLE_FIELDS_BY_STRATEGY.get(strategy, frozenset())
    // — anything unrecognised has an empty allowlist, so the filter
    // strips every non-structural field. Tests the defensive fallback
    // path (`editableFieldNamesForStrategy` returns an empty set).
    const template = makeRetypeTemplate();
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 0 },
      // Cast through unknown so the typed Edit union doesn't reject
      // the bogus strategy. In practice the schema-typed editor UI
      // never produces this; the test exists for the defensive path.
      newStrategy: 'made_up_strategy' as unknown as string,
      newSource: null,
      newFields: { irrelevant_param: 99 },
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next.layout.flat_slots![0]).toEqual({
      size: 1,
      strategy: 'made_up_strategy',
      source: 'A',
    });
  });

  it('newStrategy null preserves the existing strategy (c3d batch A: backend semantic mirror)', () => {
    const template = makeRetypeTemplate();
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 0 },
      newStrategy: null,
      newSource: 'B',
      newFields: {},
    };
    const { template: next } = applyEdit(template, {}, op);
    // Backend's contract: `null` means "don't change". Strategy
    // stays 'mix'; mix's allowlist keeps both genre + exclusion_window.
    expect(next.layout.flat_slots![0]).toEqual({
      size: 1,
      strategy: 'mix',
      source: 'B',
      genre: 'jazz',
      exclusion_window: 4,
    });
  });

  it('newSource null preserves the existing source (c3d batch A)', () => {
    const template = makeRetypeTemplate();
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 0 },
      newStrategy: 'window',
      newSource: null,
      newFields: {},
    };
    const { template: next } = applyEdit(template, {}, op);
    // strategy swaps; source preserved (was 'A'); mix-only fields
    // (genre + exclusion_window) drop because they're not in window's
    // allowlist (c3d batch C allowlist filter).
    expect(next.layout.flat_slots![0]).toEqual({
      size: 1,
      strategy: 'window',
      source: 'A',
    });
  });

  it('newFields null entry clears the existing key (backend merge contract)', () => {
    const template = makeRetypeTemplate();
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 0 },
      newStrategy: null,
      newSource: null,
      newFields: { exclusion_window: null },
    };
    const { template: next } = applyEdit(template, {}, op);
    // exclusion_window cleared; strategy + source + size + genre preserved.
    expect(next.layout.flat_slots![0]).toEqual({
      size: 1,
      strategy: 'mix',
      source: 'A',
      genre: 'jazz',
    });
    expect(next.layout.flat_slots![0]).not.toHaveProperty('exclusion_window');
  });

  it('PURE: input template not mutated', () => {
    const template = makeRetypeTemplate();
    const snapshot = JSON.parse(JSON.stringify(template));
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 0 },
      newStrategy: 'window',
      newSource: null,
      newFields: { window: 4 },
    };
    applyEdit(template, {}, op);
    expect(template).toEqual(snapshot);
  });

  it('sidecar returned unchanged (retype affects template only)', () => {
    const template = makeRetypeTemplate();
    const sidecar = { schema_version: 1, sections: [], layout_order: [] };
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 0 },
      newStrategy: 'window',
      newSource: 'B',
      newFields: {},
    };
    const result = applyEdit(template, sidecar, op);
    expect(result.sidecar).toBe(sidecar);
  });

  it('no-op on out-of-range slot_index', () => {
    const template = makeRetypeTemplate();
    const op: Edit = {
      kind: 'retypeBlock',
      address: { slot_index: 999 },
      newStrategy: 'window',
      newSource: null,
      newFields: {},
    };
    const { template: next } = applyEdit(template, {}, op);
    expect(next).toBe(template);
  });
});


describe('applyEdit — structuralBlockEdit (§16.2.6 c3d-follow-up)', () => {
  function makeFlatTemplate3(): StationTemplate {
    return {
      station: 'ts',
      name: 'Test',
      sources: [
        { name: 'A', colour: '#abc', role: 'content' },
      ],
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
  const baseSidecar3 = {
    schema_version: 1,
    sections: [
      { id: 'morning', name: 'Morning', blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'atomic', slot_index: 1 },
      ]},
      { id: 'evening', name: 'Evening', blocks: [
        { kind: 'atomic', slot_index: 2 },
      ]},
    ],
    macros: [],
    layout_order: ['morning', 'evening'],
  };

  it('size: changes flat_slots[idx].size; sidecar untouched', () => {
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'size', slot_index: 1, new_size: 5 },
    };
    const { template: nextT, sidecar: nextS } = applyEdit(
      makeFlatTemplate3(), baseSidecar3, op,
    );
    expect(nextT.layout.flat_slots![1].size).toBe(5);
    expect(nextT.layout.flat_slots![0].size).toBe(1);
    expect(nextS).toBe(baseSidecar3);
  });

  it('size: no-op on missing new_size', () => {
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'size', slot_index: 1 },
    };
    const template = makeFlatTemplate3();
    const result = applyEdit(template, baseSidecar3, op);
    expect(result.template).toBe(template);
    expect(result.sidecar).toBe(baseSidecar3);
  });

  it('size: no-op on new_size < 1', () => {
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'size', slot_index: 1, new_size: 0 },
    };
    const template = makeFlatTemplate3();
    const result = applyEdit(template, baseSidecar3, op);
    expect(result.template).toBe(template);
  });

  it('size: no-op on idx out of range', () => {
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'size', slot_index: 999, new_size: 5 },
    };
    const template = makeFlatTemplate3();
    const result = applyEdit(template, baseSidecar3, op);
    expect(result.template).toBe(template);
  });

  it('add: inserts new block + shifts atomic refs by +1 + auto-rescues to _unsorted', () => {
    // Insert at slot_index=1: slots become [old0, NEW, old1, old2].
    // Sidecar refs at slot_index >= 1 shift to 2, 3.
    // New ref {slot_index: 1} appended to (auto-created) _unsorted.
    const newBlock = { source: 'A', strategy: 'pick', size: 1 };
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'add', slot_index: 1, new_block: newBlock },
    };
    const { template: nextT, sidecar: nextS } = applyEdit(
      makeFlatTemplate3(), baseSidecar3, op,
    );
    expect(nextT.layout.flat_slots!.length).toBe(4);
    expect(nextT.layout.flat_slots![1]).toEqual(newBlock);
    const ns = nextS as typeof baseSidecar3;
    // Atomic refs at slot_index >= 1 shifted.
    expect(ns.sections.find((s) => s.id === 'morning')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 0 },
      { kind: 'atomic', slot_index: 2 }, // was 1
    ]);
    expect(ns.sections.find((s) => s.id === 'evening')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 3 }, // was 2
    ]);
    // _unsorted auto-created with the new ref.
    const unsorted = ns.sections.find((s) => s.id === '_unsorted')!;
    expect(unsorted.name).toBe('Unsorted (moved from a deleted section)');
    expect(unsorted.blocks).toEqual([
      { kind: 'atomic', slot_index: 1 },
    ]);
    expect(ns.layout_order).toContain('_unsorted');
  });

  it('add: existing _unsorted is reused (no duplicate section, name preserved)', () => {
    const sidecarWithBin = {
      ...baseSidecar3,
      sections: [
        ...baseSidecar3.sections,
        { id: '_unsorted', name: 'Custom name', blocks: [
          { kind: 'atomic', slot_index: 99 },
        ]},
      ],
      layout_order: [...baseSidecar3.layout_order, '_unsorted'],
    };
    const newBlock = { source: 'A', strategy: 'pick', size: 1 };
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'add', slot_index: 0, new_block: newBlock },
    };
    const { sidecar: nextS } = applyEdit(
      makeFlatTemplate3(), sidecarWithBin, op,
    );
    const ns = nextS as typeof sidecarWithBin;
    const unsortedSections = ns.sections.filter((s) => s.id === '_unsorted');
    expect(unsortedSections.length).toBe(1);
    expect(unsortedSections[0].name).toBe('Custom name'); // preserved
    expect(unsortedSections[0].blocks).toEqual([
      { kind: 'atomic', slot_index: 100 }, // was 99, shifted +1
      { kind: 'atomic', slot_index: 0 },   // new ref at insert idx
    ]);
  });

  it('add: no-op on missing new_block', () => {
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'add', slot_index: 1 },
    };
    const template = makeFlatTemplate3();
    const result = applyEdit(template, baseSidecar3, op);
    expect(result.template).toBe(template);
    expect(result.sidecar).toBe(baseSidecar3);
  });

  it('add: idx === flat_slots.length (append at end) is valid', () => {
    const newBlock = { source: 'A', strategy: 'pick', size: 1 };
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'add', slot_index: 3, new_block: newBlock },
    };
    const { template: nextT } = applyEdit(
      makeFlatTemplate3(), baseSidecar3, op,
    );
    expect(nextT.layout.flat_slots!.length).toBe(4);
    expect(nextT.layout.flat_slots![3]).toEqual(newBlock);
  });

  it('remove: cascade-drops the matching atomic ref + shifts higher refs by -1', () => {
    // Remove slot_index=1: morning loses its slot-1 ref;
    // evening's slot-2 ref shifts to slot-1.
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'remove', slot_index: 1 },
    };
    const { template: nextT, sidecar: nextS } = applyEdit(
      makeFlatTemplate3(), baseSidecar3, op,
    );
    expect(nextT.layout.flat_slots!.length).toBe(2);
    const ns = nextS as typeof baseSidecar3;
    expect(ns.sections.find((s) => s.id === 'morning')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 0 },
    ]);
    expect(ns.sections.find((s) => s.id === 'evening')!.blocks).toEqual([
      { kind: 'atomic', slot_index: 1 }, // was 2
    ]);
  });

  it('remove: no-op (rolls back template + sidecar) when cascade would orphan macros', () => {
    // Section has only one atomic anchor (slot_index=0) + a macro.
    // Removing slot 0 would leave a macro-only section that
    // resolve_macro_addresses can't position. Backend 400s; frontend
    // no-ops so the staged preview stays honest.
    const orphanRisky = {
      ...baseSidecar3,
      sections: [
        { id: 'morning', name: 'Morning', blocks: [
          { kind: 'atomic', slot_index: 0 },
          { kind: 'macro', ref: 'm1' },
        ]},
        { id: 'evening', name: 'Evening', blocks: [
          { kind: 'atomic', slot_index: 2 },
        ]},
      ],
    };
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'remove', slot_index: 0 },
    };
    const template = makeFlatTemplate3();
    const result = applyEdit(template, orphanRisky, op);
    // Template unchanged (no pop); sidecar reference-equal.
    expect(result.template).toBe(template);
    expect(result.sidecar).toBe(orphanRisky);
  });

  it('remove: removing the only atomic from an ALL-macro section IS allowed if no macros remain', () => {
    // Same shape as the orphan test but no macro — cascade leaves an
    // empty section, which is fine (not orphaning anything).
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'remove', slot_index: 0 },
    };
    const allEmpty = {
      ...baseSidecar3,
      sections: [
        { id: 'morning', name: 'Morning', blocks: [
          { kind: 'atomic', slot_index: 0 },
        ]},
        { id: 'evening', name: 'Evening', blocks: [
          { kind: 'atomic', slot_index: 2 },
        ]},
      ],
    };
    const { template: nextT } = applyEdit(
      makeFlatTemplate3(), allEmpty, op,
    );
    expect(nextT.layout.flat_slots!.length).toBe(2);
  });

  it('remove: no-op on idx out of range', () => {
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'remove', slot_index: 999 },
    };
    const template = makeFlatTemplate3();
    const result = applyEdit(template, baseSidecar3, op);
    expect(result.template).toBe(template);
    expect(result.sidecar).toBe(baseSidecar3);
  });

  it('row_template stations are rejected (backend 400 mirror — whole-op no-op)', () => {
    // Backend rejects structural edits on row_template-based layouts
    // (block_idx-domain analogue is a future slice). Frontend mirror.
    const rowTemplate: StationTemplate = {
      station: 'ts',
      name: 'Test',
      sources: [{ name: 'A', colour: '#abc', role: 'content' }],
      layout: {
        unit: 'slot',
        unit_labels: [],
        row_start_offsets: [],
        row_template: [
          { source: 'A', strategy: 'pick', size: 1 },
        ],
      },
    };
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'size', slot_index: 0, new_size: 5 },
    };
    const result = applyEdit(rowTemplate, baseSidecar3, op);
    expect(result.template).toBe(rowTemplate);
    expect(result.sidecar).toBe(baseSidecar3);
  });

  it('PURE: input template + sidecar not mutated by add', () => {
    const template = makeFlatTemplate3();
    const templateSnap = JSON.parse(JSON.stringify(template));
    const sidecarSnap = JSON.parse(JSON.stringify(baseSidecar3));
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: {
        op: 'add', slot_index: 1,
        new_block: { source: 'A', strategy: 'pick', size: 1 },
      },
    };
    applyEdit(template, baseSidecar3, op);
    expect(template).toEqual(templateSnap);
    expect(baseSidecar3).toEqual(sidecarSnap);
  });

  it('c3d-follow-up review F1: add with non-sidecar input no-ops the WHOLE op (no half-applied template+sidecar)', () => {
    // Without the gate, the template insert ran but the sidecar
    // shift + auto-rescue silently skipped — leaving flat_slots
    // ahead of stale atomic refs. The gate matches `_moveBlock` /
    // `_sectionDelete`: all-or-nothing.
    const template = makeFlatTemplate3();
    const badSidecar = 'not a sidecar';
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: {
        op: 'add', slot_index: 1,
        new_block: { source: 'A', strategy: 'pick', size: 1 },
      },
    };
    const result = applyEdit(template, badSidecar, op);
    expect(result.template).toBe(template);
    expect(result.sidecar).toBe(badSidecar);
  });

  it('c3d-follow-up review F1: remove with non-sidecar input no-ops the WHOLE op', () => {
    // Symmetric pin for `remove`: pre-fix, flat_slots.pop ran but
    // _shiftAtomicSlotIndices early-returned on the non-sidecar
    // input — template lost a slot while every sidecar ref at
    // slot_index > idx stayed in place.
    const template = makeFlatTemplate3();
    const badSidecar = { sections: 'not an array' }; // wrong shape
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'remove', slot_index: 1 },
    };
    const result = applyEdit(template, badSidecar, op);
    expect(result.template).toBe(template);
    expect(result.sidecar).toBe(badSidecar);
  });

  it('c3d-follow-up review F1: size with non-sidecar input STILL applies (size doesn\'t touch sidecar)', () => {
    // Contrast pin: the F1 gate lives in the `add` + `remove`
    // branches only. `size` is sidecar-independent — it should
    // still resize the template even if the sidecar is broken,
    // because no sidecar-side adjustment exists to skip.
    const template = makeFlatTemplate3();
    const badSidecar = 'not a sidecar';
    const op: Edit = {
      kind: 'structuralBlockEdit',
      edit: { op: 'size', slot_index: 0, new_size: 7 },
    };
    const result = applyEdit(template, badSidecar, op);
    expect(result.template.layout.flat_slots![0].size).toBe(7);
    expect(result.sidecar).toBe(badSidecar);
  });
});


describe('replayOpLog — §16.2.6 c3c + c3d block-kind dispatch', () => {
  function baseDepsC3c(): ReplayDeps {
    return {
      patchBlock: async () => {},
      patchMacro: async () => {},
      renameSection: async () => {},
      createSection: async () => {},
      deleteSection: async () => {},
      reorderSections: async () => {},
      moveBlock: async () => {},
      retypeBlock: async () => {},
      structuralBlockEdit: async () => {},
      refreshUpdatedAt: async () => {},
      isConflict: () => false,
    };
  }

  it('§16.2.6 c3d — moveBlock dispatches to deps.moveBlock with (address, targetSectionId, targetIndex)', async () => {
    const calls: Array<{ address: BlockAddress; tgt: string; idx: number | null }> = [];
    const deps: ReplayDeps = {
      ...baseDepsC3c(),
      moveBlock: async (address, tgt, idx) => {
        calls.push({ address, tgt, idx });
      },
    };
    const ops: Edit[] = [
      { kind: 'moveBlock', address: { slot_index: 0 }, targetSectionId: 'evening', targetIndex: 1 },
      { kind: 'moveBlock', address: { slot_index: 1 }, targetSectionId: 'morning', targetIndex: null },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(true);
    expect(calls).toEqual([
      { address: { slot_index: 0 }, tgt: 'evening', idx: 1 },
      { address: { slot_index: 1 }, tgt: 'morning', idx: null },
    ]);
  });

  it('§16.2.6 c3d — retypeBlock dispatches to deps.retypeBlock with (address, newStrategy, newSource, newFields)', async () => {
    let captured: {
      address?: BlockAddress;
      ns?: string | null;
      nsrc?: string | null;
      nf?: Record<string, unknown>;
    } = {};
    const deps: ReplayDeps = {
      ...baseDepsC3c(),
      retypeBlock: async (address, newStrategy, newSource, newFields) => {
        captured = { address, ns: newStrategy, nsrc: newSource, nf: { ...newFields } };
      },
    };
    const ops: Edit[] = [
      {
        kind: 'retypeBlock',
        address: { slot_index: 2 },
        newStrategy: 'window',
        newSource: 'B',
        newFields: { window: 5 },
      },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(true);
    expect(captured).toEqual({
      address: { slot_index: 2 },
      ns: 'window',
      nsrc: 'B',
      nf: { window: 5 },
    });
  });

  it('§16.2.6 c3d-follow-up — structuralBlockEdit dispatches to deps.structuralBlockEdit with the edit payload', async () => {
    const calls: Array<{ op: string; slot_index: number; new_size?: number; new_block?: Record<string, unknown> }> = [];
    const deps: ReplayDeps = {
      ...baseDepsC3c(),
      structuralBlockEdit: async (edit) => {
        calls.push({ ...edit });
      },
    };
    const ops: Edit[] = [
      { kind: 'structuralBlockEdit', edit: { op: 'size', slot_index: 0, new_size: 3 } },
      { kind: 'structuralBlockEdit', edit: { op: 'add', slot_index: 1, new_block: { source: 'A', strategy: 'pick', size: 1 } } },
      { kind: 'structuralBlockEdit', edit: { op: 'remove', slot_index: 2 } },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(true);
    expect(calls).toEqual([
      { op: 'size', slot_index: 0, new_size: 3 },
      { op: 'add', slot_index: 1, new_block: { source: 'A', strategy: 'pick', size: 1 } },
      { op: 'remove', slot_index: 2 },
    ]);
  });

  it('sectionCreate dispatches to deps.createSection with (id, name)', async () => {
    const calls: Array<{ sectionId: string; name: string | null }> = [];
    const deps: ReplayDeps = {
      ...baseDepsC3c(),
      createSection: async (sectionId, name) => {
        calls.push({ sectionId, name });
      },
    };
    const ops: Edit[] = [
      { kind: 'sectionCreate', sectionId: 'morning', name: 'Morning' },
      { kind: 'sectionCreate', sectionId: 'evening', name: null },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(true);
    expect(calls).toEqual([
      { sectionId: 'morning', name: 'Morning' },
      { sectionId: 'evening', name: null },
    ]);
  });

  it('sectionDelete dispatches to deps.deleteSection with sectionId', async () => {
    const calls: string[] = [];
    const deps: ReplayDeps = {
      ...baseDepsC3c(),
      deleteSection: async (sectionId) => { calls.push(sectionId); },
    };
    const ops: Edit[] = [
      { kind: 'sectionDelete', sectionId: 'morning' },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(true);
    expect(calls).toEqual(['morning']);
  });

  it('reorderSections dispatches to deps.reorderSections with the new order', async () => {
    let captured: string[] | null = null;
    const deps: ReplayDeps = {
      ...baseDepsC3c(),
      reorderSections: async (layoutOrder) => { captured = [...layoutOrder]; },
    };
    const ops: Edit[] = [
      { kind: 'reorderSections', layoutOrder: ['b', 'a', '_unsorted'] },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(true);
    expect(captured).toEqual(['b', 'a', '_unsorted']);
  });
});


describe('applyEdits — composition', () => {
  it('applies ops in order; later ops see earlier ops effects', () => {
    const template = makeFlatTemplate();
    const ops: Edit[] = [
      { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 3 } },
      // Second op against the SAME slot — overwrites size again.
      { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 5 } },
      // Third op against a different slot — independent.
      { kind: 'patchBlock', address: { slot_index: 1 }, fields: { strategy: 'window' } },
    ];
    const { template: next } = applyEdits(template, {}, ops);
    expect(next.layout.flat_slots![0]).toMatchObject({ size: 5 });
    expect(next.layout.flat_slots![1]).toMatchObject({ strategy: 'window', size: 2 });
  });

  it('empty op list returns the base unchanged (identity)', () => {
    const template = makeFlatTemplate();
    const sidecar = { schema_version: 1, sections: [], layout_order: [] };
    const result = applyEdits(template, sidecar, []);
    expect(result.template).toBe(template);
    expect(result.sidecar).toBe(sidecar);
  });
});


describe('replayOpLog — §16.2.6 c3b review #1 batch C', () => {
  /** Minimal ReplayDeps with all 9 callables as no-op success. Tests
   *  override the kinds they care about per case. Centralised so the
   *  test bodies don't repeat the full 9-prop scaffolding. */
  function baseDeps(): ReplayDeps {
    return {
      patchBlock: async () => {},
      patchMacro: async () => {},
      renameSection: async () => {},
      // §16.2.6 c3c — section-level kinds.
      createSection: async () => {},
      deleteSection: async () => {},
      reorderSections: async () => {},
      moveBlock: async () => {},
      retypeBlock: async () => {},
      structuralBlockEdit: async () => {},
      refreshUpdatedAt: async () => {},
      isConflict: () => false,
    };
  }

  it('happy path: dispatches each kind to its matching deps callable in queue order', async () => {
    const calls: Array<{ kind: string; args: unknown[] }> = [];
    let refreshCount = 0;
    const deps: ReplayDeps = {
      ...baseDeps(),
      patchBlock: async (address, fields) => {
        calls.push({ kind: 'patchBlock', args: [address, fields] });
      },
      patchMacro: async (macroId, fields) => {
        calls.push({ kind: 'patchMacro', args: [macroId, fields] });
      },
      renameSection: async (sectionId, name) => {
        calls.push({ kind: 'renameSection', args: [sectionId, name] });
      },
      refreshUpdatedAt: async () => { refreshCount += 1; },
    };
    const ops: Edit[] = [
      { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 3 } },
      { kind: 'patchMacro', macroId: 'm1', fields: { strategy: 'window' } },
      { kind: 'renameSection', sectionId: 's1', name: 'New' },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.totalEdits).toBe(3);
      expect(result.remaining).toEqual([]);
    }
    // Calls happened in order, one per op.
    expect(calls).toEqual([
      { kind: 'patchBlock', args: [{ slot_index: 0 }, { size: 3 }] },
      { kind: 'patchMacro', args: ['m1', { strategy: 'window' }] },
      { kind: 'renameSection', args: ['s1', 'New'] },
    ]);
    // refreshUpdatedAt called after each successful PATCH.
    expect(refreshCount).toBe(3);
    // Input ops array NOT mutated.
    expect(ops.length).toBe(3);
  });

  it('empty op list: no calls, success', async () => {
    let refreshCount = 0;
    const deps: ReplayDeps = {
      ...baseDeps(),
      patchBlock: async () => { throw new Error('should not run'); },
      patchMacro: async () => { throw new Error('should not run'); },
      renameSection: async () => { throw new Error('should not run'); },
      refreshUpdatedAt: async () => { refreshCount += 1; },
    };
    const result = await replayOpLog([], deps);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.totalEdits).toBe(0);
      expect(result.remaining).toEqual([]);
    }
    expect(refreshCount).toBe(0);
  });

  it('conflict on edit 2 of 3: stops, returns conflict result with edit2+edit3 still queued', async () => {
    const calls: Array<string> = [];
    const conflictErr = new Error('409');
    const deps: ReplayDeps = {
      ...baseDeps(),
      patchBlock: async () => { calls.push('patchBlock'); },
      patchMacro: async () => {
        calls.push('patchMacro');
        throw conflictErr;
      },
      renameSection: async () => { calls.push('renameSection'); },
      isConflict: (err) => err === conflictErr,
    };
    const ops: Edit[] = [
      { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 1 } },
      { kind: 'patchMacro', macroId: 'm1', fields: { size: 2 } },
      { kind: 'renameSection', sectionId: 's1', name: 'X' },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.totalEdits).toBe(3);
      expect(result.failedAtIdx).toBe(2);
      // Failed edit + remaining all stay in the returned queue.
      expect(result.remaining).toHaveLength(2);
      expect(result.remaining[0].kind).toBe('patchMacro');
      expect(result.remaining[1].kind).toBe('renameSection');
      expect('conflict' in result && result.conflict).toBe(true);
    }
    // patchBlock + the failing patchMacro both ran; renameSection didn't.
    expect(calls).toEqual(['patchBlock', 'patchMacro']);
  });

  it('non-conflict error on edit 1: returns error string + failedAtIdx=1', async () => {
    const networkErr = new Error('network down');
    const deps: ReplayDeps = {
      ...baseDeps(),
      patchBlock: async () => { throw networkErr; },
    };
    const ops: Edit[] = [
      { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 1 } },
      { kind: 'patchMacro', macroId: 'm1', fields: {} },
    ];
    const result = await replayOpLog(ops, deps);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.failedAtIdx).toBe(1);
      expect(result.totalEdits).toBe(2);
      expect(result.remaining).toHaveLength(2);
      if ('error' in result) {
        expect(result.error).toBe('network down');
      }
    }
  });

  it('refreshUpdatedAt is called AFTER each successful PATCH but NOT after a failure', async () => {
    let refreshCount = 0;
    const deps: ReplayDeps = {
      ...baseDeps(),
      patchMacro: async () => { throw new Error('fail'); },
      refreshUpdatedAt: async () => { refreshCount += 1; },
    };
    const ops: Edit[] = [
      { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 1 } },
      { kind: 'patchMacro', macroId: 'm1', fields: {} },
    ];
    await replayOpLog(ops, deps);
    // patchBlock succeeded → refresh fired once.
    // patchMacro failed → refresh NOT fired again.
    expect(refreshCount).toBe(1);
  });
});


// ---- §16.2.7.t6 — attributeOpsToSlot -----------------------------------


describe('attributeOpsToSlot', () => {
  function patchBlockOp(slotIndex: number): Edit {
    return {
      kind: 'patchBlock',
      address: { slot_index: slotIndex } as BlockAddress,
      fields: { size: 5 },
    };
  }
  function retypeBlockOp(slotIndex: number): Edit {
    return {
      kind: 'retypeBlock',
      address: { slot_index: slotIndex } as BlockAddress,
      newStrategy: 'window',
      newSource: null,
      newFields: {},
    };
  }
  function moveBlockOp(slotIndex: number): Edit {
    return {
      kind: 'moveBlock',
      address: { slot_index: slotIndex } as BlockAddress,
      targetSectionId: 'evening',
      targetIndex: 0,
    };
  }
  function structuralOp(slotIndex: number, op: 'size' | 'add' | 'remove' = 'size'): Edit {
    return {
      kind: 'structuralBlockEdit',
      edit: { op, slot_index: slotIndex, new_size: 3 },
    };
  }

  it('patchBlock targeting slot N → that op index in result', () => {
    const log: Edit[] = [patchBlockOp(5)];
    expect(attributeOpsToSlot(log, 5)).toEqual([0]);
    expect(attributeOpsToSlot(log, 6)).toEqual([]);
  });

  it('retypeBlock + moveBlock + structuralBlockEdit all attributable by slot_index', () => {
    const log: Edit[] = [
      retypeBlockOp(3),
      moveBlockOp(3),
      structuralOp(3, 'size'),
    ];
    expect(attributeOpsToSlot(log, 3)).toEqual([0, 1, 2]);
  });

  it('multiple ops targeting the same slot → multiple indices returned', () => {
    const log: Edit[] = [
      patchBlockOp(2),  // 0
      patchBlockOp(7),  // 1 — irrelevant
      patchBlockOp(2),  // 2
      retypeBlockOp(2), // 3
    ];
    expect(attributeOpsToSlot(log, 2)).toEqual([0, 2, 3]);
  });

  it('cascade case (structural-add at slot=0 + later patchBlock at slot=5) NOT attributed to slot 5', () => {
    // Per D5 — cascade attribution is out of scope. The
    // structural-add at slot=0 shifts every later slot by +1,
    // but attributeOpsToSlot returns only DIRECT matches.
    // For slot=5: the patchBlock(slot=5) IS a direct match;
    // the structural-add(slot=0) is NOT. Operator's intent for
    // "revert slot 5" is just the patchBlock, not the structural.
    const log: Edit[] = [
      structuralOp(0, 'add'),  // cascade source
      patchBlockOp(5),         // direct
    ];
    expect(attributeOpsToSlot(log, 5)).toEqual([1]);
    // For slot=0: only the structural matches directly.
    expect(attributeOpsToSlot(log, 0)).toEqual([0]);
  });

  it('other kinds (patchMacro / section* / reorder) never match', () => {
    const log: Edit[] = [
      { kind: 'patchMacro', macroId: 'm1', fields: { size: 2 } },
      { kind: 'sectionCreate', sectionId: 's1', name: 'S1' },
      { kind: 'sectionDelete', sectionId: 's1' },
      { kind: 'renameSection', sectionId: 's1', name: 'X' },
      { kind: 'reorderSections', layoutOrder: ['s1', 's2'] },
    ];
    expect(attributeOpsToSlot(log, 0)).toEqual([]);
    expect(attributeOpsToSlot(log, 99)).toEqual([]);
  });

  it('empty opLog → empty result for any slot', () => {
    expect(attributeOpsToSlot([], 0)).toEqual([]);
    expect(attributeOpsToSlot([], 999)).toEqual([]);
  });
});
