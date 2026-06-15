import { describe, it, expect } from 'vitest';
import { applyEdit, applyEdits, type Edit } from './edit-ops';
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
