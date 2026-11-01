import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {
  applyEdit, applyEdits, attributeOpsToSlot, describeOp, replayOpLog, resolveMacroAddresses, slotsNow, undoSet,
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

  it("by where the rule is now: rules added or removed after an edit move it along", () => {
    const log: Edit[] = [
      patchBlockOp(1),         // 0: rule at 1, then 2 (add at 0), then 1 (remove at 0)…
      structuralOp(0, 'add'),  // 1: the new rule at 0
      patchBlockOp(4),         // 2: rule at 4, then 3
      structuralOp(0, 'remove'), // 3: removes the rule added by 1
      patchBlockOp(6),         // 4
      structuralOp(6, 'remove'), // 5: …and removes 4's rule
    ];
    expect(slotsNow(log)).toEqual([1, undefined, 3, undefined, undefined, undefined]);
    expect(attributeOpsToSlot(log, 1)).toEqual([0]);
    expect(attributeOpsToSlot(log, 3)).toEqual([2]);
    expect(attributeOpsToSlot(log, 4)).toEqual([]);
    expect(attributeOpsToSlot(log, 6)).toEqual([]);
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


// ---- §16.2.7.t7 — describeOp -------------------------------------------


describe('describeOp', () => {
  it('patchBlock with slot_index + fields', () => {
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 5 } as BlockAddress,
      fields: { size: 10, cursor_id: 'C' },
    };
    expect(describeOp(op)).toBe('Patched slot 5: size=10 · cursor_id=C');
  });

  it('patchBlock with null field renders explicit clear', () => {
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 0 } as BlockAddress,
      fields: { genre: null },
    };
    expect(describeOp(op)).toBe('Patched slot 0: genre=null');
  });

  it('patchBlock with array field renders bracketed list', () => {
    const op: Edit = {
      kind: 'patchBlock',
      address: { slot_index: 2 } as BlockAddress,
      fields: { allowlist_sources: ['rock', 'jazz'] },
    };
    expect(describeOp(op)).toBe('Patched slot 2: allowlist_sources=[rock,jazz]');
  });

  it('patchBlock without slot_index (drift) renders defensive "(no address)"', () => {
    const op: Edit = {
      kind: 'patchBlock',
      address: {} as BlockAddress,
      fields: { size: 1 },
    };
    expect(describeOp(op)).toBe('Patched (no address): size=1');
  });

  it('patchMacro', () => {
    const op: Edit = {
      kind: 'patchMacro',
      macroId: 'jingles',
      fields: { repeat: 3 },
    };
    expect(describeOp(op)).toBe("Patched macro 'jingles': repeat=3");
  });

  it('renameSection', () => {
    const op: Edit = {
      kind: 'renameSection',
      sectionId: 'morning',
      name: 'AM',
    };
    expect(describeOp(op)).toBe("Renamed section 'morning' to 'AM'");
  });

  it('sectionCreate with + without name', () => {
    expect(describeOp({
      kind: 'sectionCreate', sectionId: 'evening', name: 'PM',
    })).toBe("Created section 'evening' ('PM')");
    expect(describeOp({
      kind: 'sectionCreate', sectionId: 'unnamed', name: null,
    })).toBe("Created section 'unnamed'");
  });

  it('sectionDelete', () => {
    expect(describeOp({
      kind: 'sectionDelete', sectionId: 'morning',
    })).toBe("Deleted section 'morning'");
  });

  it('reorderSections', () => {
    expect(describeOp({
      kind: 'reorderSections',
      layoutOrder: ['morning', 'evening', '_unsorted'],
    })).toBe('Reordered sections to: morning, evening, _unsorted');
  });

  it('moveBlock with targetIndex + with null (appended)', () => {
    expect(describeOp({
      kind: 'moveBlock',
      address: { slot_index: 3 } as BlockAddress,
      targetSectionId: 'evening',
      targetIndex: 0,
    })).toBe("Moved slot 3 to section 'evening' at position 0");
    expect(describeOp({
      kind: 'moveBlock',
      address: { slot_index: 3 } as BlockAddress,
      targetSectionId: 'evening',
      targetIndex: null,
    })).toBe("Moved slot 3 to section 'evening' appended");
  });

  it('retypeBlock with strategy + source / strategy only / source only / neither', () => {
    expect(describeOp({
      kind: 'retypeBlock',
      address: { slot_index: 7 } as BlockAddress,
      newStrategy: 'window', newSource: 'rock', newFields: {},
    })).toBe("Retyped slot 7 to strategy 'window' / source 'rock'");
    expect(describeOp({
      kind: 'retypeBlock',
      address: { slot_index: 7 } as BlockAddress,
      newStrategy: 'window', newSource: null, newFields: {},
    })).toBe("Retyped slot 7 to strategy 'window'");
    expect(describeOp({
      kind: 'retypeBlock',
      address: { slot_index: 7 } as BlockAddress,
      newStrategy: null, newSource: 'rock', newFields: {},
    })).toBe("Retyped slot 7 to source 'rock'");
    expect(describeOp({
      kind: 'retypeBlock',
      address: { slot_index: 7 } as BlockAddress,
      newStrategy: null, newSource: null, newFields: {},
    })).toBe('Retyped slot 7 to (no change)');
  });

  it('structuralBlockEdit — size / add / remove', () => {
    expect(describeOp({
      kind: 'structuralBlockEdit',
      edit: { op: 'size', slot_index: 0, new_size: 4 },
    })).toBe('Resized slot 0 to size 4');
    expect(describeOp({
      kind: 'structuralBlockEdit',
      edit: { op: 'add', slot_index: 5 },
    })).toBe('Inserted slot at position 5');
    expect(describeOp({
      kind: 'structuralBlockEdit',
      edit: { op: 'remove', slot_index: 5 },
    })).toBe('Removed slot at position 5');
  });
});


// ---- patchMacro fans out to the template (as the server does) ------------

describe('resolveMacroAddresses — same as engine.editor_metadata.resolve_macro_addresses', () => {
  // Python's answer for every macro in the three example stations that
  // have macros: [first address, count] (each a contiguous run).
  // Regenerate with resolve_macro_addresses if a station's sidecar changes.
  const PYTHON: Record<string, Record<string, [number, number]>> = {"rebirth-radio":{"weighted-1":[1,200],"sequential-1":[217,95],"weighted-2":[313,201],"sequential-2":[530,96],"weighted-3":[627,200],"sequential-3":[843,97],"weighted-4":[941,200],"sequential-4":[1157,96],"weighted-5":[1254,200],"sequential-5":[1470,150],"sequential-6":[1620,120],"sequential-7":[1756,140],"sequential-8":[1896,160],"sequential-9":[2056,140]},"starter":{"weighted-1":[1,7],"weighted-2":[9,7],"weighted-3":[17,7],"weighted-4":[25,7],"weighted-5":[33,7],"weighted-6":[41,7],"weighted-7":[49,7],"weighted-8":[57,7],"weighted-9":[65,7],"weighted-10":[73,7],"weighted-11":[81,7],"weighted-12":[89,7],"weighted-13":[97,7],"weighted-14":[105,7],"weighted-15":[113,7],"weighted-16":[121,7],"weighted-17":[129,7],"weighted-18":[137,7],"weighted-19":[145,7],"weighted-20":[153,7],"weighted-21":[161,7]},"stateful-starter":{"sequential-1":[0,12],"sequential-2":[13,11],"sequential-3":[25,11],"sequential-4":[37,11],"sequential-5":[49,11],"sequential-6":[61,11],"sequential-7":[73,11],"sequential-8":[85,11],"sequential-9":[97,11],"sequential-10":[109,11],"sequential-11":[121,11],"sequential-12":[133,11],"sequential-13":[145,11],"sequential-14":[157,11],"sequential-15":[169,11],"sequential-16":[181,11],"sequential-17":[193,11],"sequential-18":[205,11],"sequential-19":[217,11],"sequential-20":[229,11],"sequential-21":[241,11],"sequential-22":[253,11],"sequential-23":[265,11],"sequential-24":[277,11],"sequential-25":[289,11],"sequential-26":[301,11],"sequential-27":[313,11],"sequential-28":[325,11]}};
  const STATIONS = path.resolve(__dirname, '../../../stations');
  for (const [slug, macros] of Object.entries(PYTHON)) {
    it(slug, () => {
      const sidecar = JSON.parse(fs.readFileSync(path.join(STATIONS, `${slug}.editor.json`), 'utf-8'));
      for (const [id, [first, count]] of Object.entries(macros)) {
        expect(resolveMacroAddresses(sidecar, id), id).toEqual(
          Array.from({ length: count }, (_, i) => ({ slot_index: first + i })),
        );
      }
    });
  }

  it('repeating rows: block_idx addresses, de-duplicated across sections', () => {
    const sidecar = {
      schema_version: 1, layout_order: ['a', 'b'],
      macros: [{ id: 'm', repeat: 2, spec: { source: 'A', strategy: 'pick', size: 1 } }],
      sections: [
        { id: 'a', blocks: [{ kind: 'atomic', row_idx: 0, block_idx: 0 }, { kind: 'macro', ref: 'm' }] },
        { id: 'b', blocks: [{ kind: 'atomic', row_idx: 1, block_idx: 0 }, { kind: 'macro', ref: 'm' }] },
      ],
    };
    expect(resolveMacroAddresses(sidecar, 'm')).toEqual([{ block_idx: 1 }, { block_idx: 2 }]);
  });

  it('drift (a section whose blocks do not line up) resolves to nothing', () => {
    const sidecar = {
      schema_version: 1, layout_order: ['a'],
      macros: [{ id: 'm', repeat: 2, spec: { source: 'A', strategy: 'pick', size: 1 } }],
      sections: [{ id: 'a', blocks: [
        { kind: 'atomic', slot_index: 0 }, { kind: 'macro', ref: 'm' }, { kind: 'atomic', slot_index: 9 },
      ] }],
    };
    expect(resolveMacroAddresses(sidecar, 'm')).toEqual([]);
    expect(resolveMacroAddresses(null, 'm')).toEqual([]);
  });

  it("applyEdit patchMacro changes the macro's template blocks and its spec", () => {
    const template = makeFlatTemplate(); // slots 0-2
    const sidecar = {
      schema_version: 1, layout_order: ['a'],
      macros: [{ id: 'm', repeat: 2, spec: { source: 'A', strategy: 'pick', size: 1 } }],
      sections: [{ id: 'a', blocks: [{ kind: 'atomic', slot_index: 0 }, { kind: 'macro', ref: 'm' }] }],
    };
    const out = applyEdit(template, sidecar, { kind: 'patchMacro', macroId: 'm', fields: { size: 9 } });
    expect(out.template.layout.flat_slots!.map((b) => b.size)).toEqual([1, 9, 9]);
    expect((out.sidecar as typeof sidecar).macros[0].spec.size).toBe(9);
  });
});


describe('undoSet — undoing a change takes the later ones built on it', () => {
  const sidecar = {
    schema_version: 1,
    sections: [
      { id: 'morning', name: 'Morning', blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'atomic', slot_index: 1 },
      ]},
      { id: 'evening', name: 'Evening', blocks: [{ kind: 'atomic', slot_index: 2 }] },
    ],
    macros: [],
    layout_order: ['morning', 'evening'],
  };
  const t = makeFlatTemplate();
  const patch = (slot: number, size = 3): Edit => ({ kind: 'patchBlock', address: { slot_index: slot }, fields: { size } });
  const add = (slot: number): Edit => ({
    kind: 'structuralBlockEdit', edit: { op: 'add', slot_index: slot, new_block: { source: 'A', strategy: 'pick', size: 1 } },
  });
  const remove = (slot: number): Edit => ({ kind: 'structuralBlockEdit', edit: { op: 'remove', slot_index: slot } });
  const retype = (slot: number): Edit => ({
    kind: 'retypeBlock', address: { slot_index: slot }, newStrategy: 'sequential', newSource: null, newFields: {},
  });

  it('a change nothing builds on goes alone, settings to the same rule included', () => {
    const log = [patch(0), patch(0, 4), { kind: 'renameSection', sectionId: 'morning', name: 'AM' } as Edit, patch(2)];
    expect(undoSet(log, t, sidecar, [0])).toEqual([0]);
    expect(undoSet(log, t, sidecar, [2])).toEqual([2]);
  });

  it('nothing to undo: nothing (out-of-range places ignored)', () => {
    expect(undoSet([patch(0)], t, sidecar, [])).toEqual([]);
    expect(undoSet([patch(0)], t, sidecar, [5, -1])).toEqual([]);
  });

  it('an added rule: later edits to it or to any rule after it go too', () => {
    // Slot 1 is the added rule; slot 3 was slot 2. Slot 0 is before it.
    const log = [add(1), patch(1), patch(3), patch(0)];
    expect(undoSet(log, t, sidecar, [0])).toEqual([0, 1, 2]);
  });

  it('a removed rule: later edits to the rules after it go too', () => {
    // After removing slot 0, slot 1 is what was slot 2.
    expect(undoSet([remove(0), patch(1)], t, sidecar, [0])).toEqual([0, 1]);
    expect(undoSet([remove(2), patch(1)], t, sidecar, [0])).toEqual([0]);
  });

  it('follows the rules through later adds and removes', () => {
    // Add at 2, then add at 0 (before it): what was slot 2 is now 3.
    const log = [add(2), add(0), patch(2), patch(3)];
    expect(undoSet(log, t, sidecar, [0])).toEqual([0, 3]);
  });

  it('a retyped rule: later settings or size for it go too, not other rules', () => {
    const size: Edit = { kind: 'structuralBlockEdit', edit: { op: 'size', slot_index: 1, new_size: 2 } };
    expect(undoSet([retype(1), patch(1), size, patch(2)], t, sidecar, [0])).toEqual([0, 1, 2]);
    // Followed when a rule is added before it (slot 1 → 2).
    expect(undoSet([retype(1), add(0), patch(2), patch(1)], t, sidecar, [0])).toEqual([0, 2]);
  });

  it('a new section: renaming, deleting or moving a rule into it go too', () => {
    const log: Edit[] = [
      { kind: 'sectionCreate', sectionId: 'late', name: 'Late' },
      { kind: 'renameSection', sectionId: 'late', name: 'Late night' },
      { kind: 'moveBlock', address: { slot_index: 2 }, targetSectionId: 'late', targetIndex: null },
      { kind: 'renameSection', sectionId: 'morning', name: 'AM' },
      { kind: 'sectionDelete', sectionId: 'late' },
    ];
    expect(undoSet(log, t, sidecar, [0])).toEqual([0, 1, 2, 4]);
  });

  it('a reorder after the sections changed goes with that change', () => {
    const log: Edit[] = [
      { kind: 'sectionCreate', sectionId: 'late', name: 'Late' },
      { kind: 'reorderSections', layoutOrder: ['late', 'evening', 'morning'] },
    ];
    expect(undoSet(log, t, sidecar, [0])).toEqual([0, 1]);
    // An added rule can make the Unsorted section: the same.
    expect(undoSet([add(0), { kind: 'reorderSections', layoutOrder: ['_unsorted', 'morning', 'evening'] }], t, sidecar, [0]))
      .toEqual([0, 1]);
    // Undoing the reorder itself takes nothing else.
    expect(undoSet(log, t, sidecar, [1])).toEqual([1]);
  });

  it('a deleted section made again goes with the delete', () => {
    const log: Edit[] = [
      { kind: 'sectionDelete', sectionId: 'evening' },
      { kind: 'sectionCreate', sectionId: 'evening', name: 'Evening again' },
      { kind: 'renameSection', sectionId: 'evening', name: 'Eve' },
    ];
    expect(undoSet(log, t, sidecar, [0])).toEqual([0, 1, 2]);
  });

  it("an edit the server would refuse without it goes too: removing a section's last rule beside a macro", () => {
    // Morning: rule 0 and a macro (rule 1). Moving rule 2 into Morning
    // lets rule 0 be removed (the macro still has a rule beside it);
    // without the move, removing rule 0 would leave the macro alone.
    const withMacro = {
      ...sidecar,
      sections: [
        { id: 'morning', name: 'Morning', blocks: [{ kind: 'atomic', slot_index: 0 }, { kind: 'macro', ref: 'm' }] },
        { id: 'evening', name: 'Evening', blocks: [{ kind: 'atomic', slot_index: 2 }] },
      ],
      macros: [{ id: 'm', name: 'M', repeat: 1, spec: { source: 'A', strategy: 'pick', size: 1 } }],
    };
    const log: Edit[] = [
      { kind: 'moveBlock', address: { slot_index: 2 }, targetSectionId: 'morning', targetIndex: null },
      remove(0),
    ];
    expect(applyEdits(t, withMacro, [remove(0)]).template).toBe(t); // refused on its own
    expect(undoSet(log, t, withMacro, [0])).toEqual([0, 1]);
  });

  it('a change of several edits (identical rules) goes with what builds on any of them', () => {
    const log = [patch(0), patch(2), add(0), patch(1)];
    expect(undoSet(log, t, sidecar, [0, 1])).toEqual([0, 1]);
    expect(undoSet(log, t, sidecar, [2])).toEqual([2, 3]);
  });

  // Every edit left after an undo must still do what it did: change the
  // same rule (and that rule still has the type it had), or still find
  // its section. Random queues of every kind, each edit undone in turn.
  it('what stays still changes the same rules (random queues)', () => {
    // mulberry32: seeded, so a failure repeats.
    let seed = 7;
    const rand = (n: number): number => {
      seed = (seed + 0x6d2b79f5) | 0;
      let x = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
      return (((x ^ (x >>> 14)) >>> 0) / 4294967296 * n) | 0;
    };
    const strategies = ['pick', 'sequential', 'window'];
    type State = { template: StationTemplate; sidecar: unknown };
    const ids = (st: State): string[] => ((st.sidecar as { layout_order: string[] }).layout_order);
    const slots = (st: State): number => st.template.layout.flat_slots!.length;

    function randomOp(st: State, n: number): Edit {
      const len = slots(st);
      const user = ids(st).filter((id) => !id.startsWith('_'));
      switch (rand(10)) {
        case 0: return { kind: 'patchBlock', address: { slot_index: rand(len) }, fields: { size: 1 + rand(4) } };
        case 1: return { kind: 'retypeBlock', address: { slot_index: rand(len) }, newStrategy: strategies[rand(3)], newSource: null, newFields: {} };
        case 2: return { kind: 'structuralBlockEdit', edit: { op: 'add', slot_index: rand(len + 1), new_block: { source: 'A', strategy: 'pick', size: 1 } } };
        case 3: return len > 1
          ? { kind: 'structuralBlockEdit', edit: { op: 'remove', slot_index: rand(len) } }
          : { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 2 } };
        case 4: return { kind: 'structuralBlockEdit', edit: { op: 'size', slot_index: rand(len), new_size: 1 + rand(3) } };
        case 5: return { kind: 'sectionCreate', sectionId: `s${n}`, name: `S${n}` };
        case 6: return user.length > 1
          ? { kind: 'sectionDelete', sectionId: user[rand(user.length)] }
          : { kind: 'sectionCreate', sectionId: `t${n}`, name: null };
        case 7: return { kind: 'renameSection', sectionId: user[rand(user.length)], name: `R${n}` };
        case 8: return { kind: 'moveBlock', address: { slot_index: rand(len) }, targetSectionId: user[rand(user.length)], targetIndex: rand(3) };
        default: {
          const order = ids(st).slice();
          for (let i = order.length - 1; i > 0; i--) {
            const k = rand(i + 1);
            [order[i], order[k]] = [order[k], order[i]];
          }
          return { kind: 'reorderSections', layoutOrder: order };
        }
      }
    }

    // Which rule each slot is (rules only move by adds and removes),
    // and its type, before each edit; and whether each edit did anything.
    function replay(log: Edit[], names: number[] = log.map((_, j) => j)) {
      let st: State = { template: t, sidecar };
      let rules = [0, 1, 2].map((i) => `r${i}`);
      const seen: { rule: string | null; strategy: string | null; did: boolean }[] = [];
      log.forEach((op, j) => {
        const slot = op.kind === 'patchBlock' || op.kind === 'retypeBlock' || op.kind === 'moveBlock'
          ? op.address.slot_index
          : op.kind === 'structuralBlockEdit' && op.edit.op !== 'add' ? op.edit.slot_index : undefined;
        const strategy = slot !== undefined ? st.template.layout.flat_slots![slot]?.strategy ?? null : null;
        const next = applyEdit(st.template, st.sidecar, op);
        seen.push({
          rule: slot !== undefined ? rules[slot] ?? null : null,
          strategy,
          did: next.template !== st.template || next.sidecar !== st.sidecar,
        });
        if (op.kind === 'structuralBlockEdit' && op.edit.op === 'add' && next.template !== st.template) {
          rules = [...rules.slice(0, op.edit.slot_index), `n${names[j]}`, ...rules.slice(op.edit.slot_index)];
        }
        if (op.kind === 'structuralBlockEdit' && op.edit.op === 'remove' && next.template !== st.template) {
          rules = rules.filter((_, i) => i !== op.edit.slot_index);
        }
        st = next;
      });
      return seen;
    }

    let undone = 0;
    let alone = 0;
    for (let run = 0; run < 300; run++) {
      const log: Edit[] = [];
      let st: State = { template: t, sidecar };
      for (let n = 0; n < 8; n++) {
        const op = randomOp(st, n);
        log.push(op);
        st = applyEdit(st.template, st.sidecar, op);
      }
      const before = replay(log);
      for (let i = 0; i < log.length; i++) {
        const out = new Set(undoSet(log, t, sidecar, [i]));
        expect(out.has(i)).toBe(true);
        const keep = log.map((_, j) => j).filter((j) => !out.has(j));
        const after = replay(keep.map((j) => log[j]), keep);
        keep.forEach((j, k) => {
          const was = before[j];
          const now = after[k];
          if (!was.did) return;
          const what = `run ${run}: undoing ${i} (${log[i].kind}) leaves ${j} (${log[j].kind})`;
          expect(now.did, `${what} doing nothing`).toBe(true);
          // An added rule isn't any earlier rule: compare added ones by
          // being added, and the rest by which rule.
          if (was.rule !== null) {
            expect(now.rule, `${what} on another rule`).toBe(was.rule);
            if (log[j].kind !== 'moveBlock' && log[j].kind !== 'structuralBlockEdit') {
              expect(now.strategy, `${what} on another type`).toBe(was.strategy);
            }
          }
        });
        undone += out.size;
        if (out.size === 1) alone += 1;
      }
    }
    // Not just "undo everything after": most changes go alone.
    expect(alone).toBeGreaterThan(300 * 8 * 0.4);
    expect(undone).toBeGreaterThan(0);
  });
});
