import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import type { Sidecar, StationTemplate } from './editor';
import { identicalRules } from './identicalRules';

const STATIONS = path.resolve(__dirname, '../../../stations');
function station(slug: string, template = slug): { template: StationTemplate; sidecar: Sidecar } {
  return {
    template: JSON.parse(fs.readFileSync(path.join(STATIONS, `${template}.json`), 'utf-8')),
    sidecar: JSON.parse(fs.readFileSync(path.join(STATIONS, `${slug}.editor.json`), 'utf-8')),
  };
}

describe('identicalRules', () => {
  it('the starter: each 7 × weighted macro has 20 twins', () => {
    const { sidecar } = station('starter', 'rebirth-radio'); // template unused for macros
    const first = sidecar.macros![0];
    const twins = identicalRules({} as StationTemplate, sidecar, {
      kind: 'macro-placeholder', macro_id: first.id, pick_in_macro: 1,
    });
    expect(twins.macroIds).toHaveLength(20);
    expect(twins.macroIds).not.toContain(first.id);
    expect(twins.songs).toBe(20 * first.repeat * first.spec.size);
    expect(twins.refs).toEqual([]);
  });

  it('a macro whose settings differ is not a twin', () => {
    const { sidecar } = station('starter', 'rebirth-radio');
    const changed = structuredClone(sidecar);
    changed.macros![3].spec = { ...changed.macros![3].spec, window: 2 };
    const twins = identicalRules({} as StationTemplate, changed, {
      kind: 'macro-placeholder', macro_id: changed.macros![0].id, pick_in_macro: 1,
    });
    expect(twins.macroIds).toHaveLength(19);
    expect(twins.macroIds).not.toContain(changed.macros![3].id);
  });

  it('key order in the template does not matter', () => {
    const { sidecar } = station('starter', 'rebirth-radio');
    const reordered = structuredClone(sidecar);
    const spec = reordered.macros![1].spec as unknown as Record<string, unknown>;
    reordered.macros![1].spec = Object.fromEntries(Object.entries(spec).reverse()) as unknown as NonNullable<Sidecar['macros']>[number]['spec'];
    const twins = identicalRules({} as StationTemplate, reordered, {
      kind: 'macro-placeholder', macro_id: reordered.macros![0].id, pick_in_macro: 1,
    });
    expect(twins.macroIds).toContain(reordered.macros![1].id);
  });

  it('Rebirth Radio: single slots match single slots with the same spec, never slots inside a macro', () => {
    const { template, sidecar } = station('rebirth-radio');
    const blocks = template.layout.flat_slots!;
    const atomic = sidecar.sections.flatMap((s) => s.blocks)
      .filter((b) => b.kind === 'atomic')
      .map((b) => (b as { slot_index: number }).slot_index);
    const key = (i: number) => JSON.stringify(blocks[i], Object.keys(blocks[i]).sort());
    // Pick a single slot that has at least one twin.
    const slot = atomic.find((i) => atomic.some((j) => j !== i && key(j) === key(i)))!;
    expect(slot).toBeDefined();
    const twins = identicalRules(template, sidecar, { kind: 'atomic-ref', slot_index: slot });
    const expected = atomic.filter((j) => j !== slot && key(j) === key(slot));
    expect(twins.refs).toEqual(expected.map((j) => ({ kind: 'atomic-ref', slot_index: j })));
    expect(twins.songs).toBe(expected.reduce((n, j) => n + blocks[j].size, 0));
    expect(twins.macroIds).toEqual([]);
  });

  it('a repeating row: blocks of the row, songs counted for every row of the week', () => {
    const template = {
      station: 's', name: 'S', sources: [],
      layout: {
        unit: 'day', unit_labels: ['Mon', 'Tue', 'Wed'], row_start_offsets: [0, 0, 0],
        row_template: [
          { source: 'A', strategy: 'pick', size: 1 },
          { source: 'B', strategy: 'window', size: 2 },
          { source: 'A', strategy: 'pick', size: 1 },
          { size: 1, strategy: 'pick', source: 'A' },
        ],
      },
    } as unknown as StationTemplate;
    const sidecar: Sidecar = {
      schema_version: 1, macros: [], layout_order: ['d'],
      sections: [{ id: 'd', blocks: [0, 1, 2, 3].map((i) => ({ kind: 'atomic', row_idx: 0, block_idx: i })) }],
    };
    const twins = identicalRules(template, sidecar, { kind: 'atomic-ref', row_idx: 2, block_idx: 0 });
    expect(twins.refs).toEqual([
      { kind: 'atomic-ref', row_idx: 0, block_idx: 2 },
      { kind: 'atomic-ref', row_idx: 0, block_idx: 3 },
    ]);
    expect(twins.songs).toBe(2 * 1 * 3); // two blocks, one song each, three days
  });
});
