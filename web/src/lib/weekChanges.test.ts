import { describe, expect, it } from 'vitest';
import shared from '../../../tests/fixtures/slot_after.json';
import type { Trace, TraceSlot, WeekChange } from './api';
import type { Edit } from './edit-ops';
import type { StationTemplate } from './editor';
import {
  changedFields, changedRule, changesAfter, changeWords, itemFor, itemsOf, whyNotJustThisWeek,
} from './weekChanges';

function slot(row: number, block: number, extra: Partial<TraceSlot> = {}): TraceSlot {
  return {
    slot: 0, unit: '', row, block, source: block === 2 ? 'B' : 'A', strategy: 'weighted',
    pool_index: 1, track_id: block * 10 + row, track_name: `t${row}.${block}`, track_artist: null, reason: 'r',
    ...extra,
  };
}

function trace(slots: TraceSlot[]): Trace {
  return {
    trace_id: 't', station: 's', week: 1, rng: 'on', seed: 1, generated_at: '',
    slots: slots.map((s, i) => ({ ...s, slot: i + 1 })),
  };
}

const flat: StationTemplate = {
  station: 's', name: 'S',
  sources: [{ name: 'A', colour: '#a00', role: 'content' }, { name: 'B', colour: '#0a0', role: 'content' }],
  layout: {
    unit: 'slot', unit_labels: [], row_start_offsets: [],
    flat_slots: [
      { source: 'A', strategy: 'weighted', size: 3 },
      { source: 'B', strategy: 'weighted', size: 2, window: 5 },
    ],
  },
};
const sidecar = {
  schema_version: 1, layout_order: ['all'], macros: [],
  sections: [{ id: 'all', name: 'All', blocks: [{ kind: 'atomic', slot_index: 0 }, { kind: 'atomic', slot_index: 1 }] }],
};

describe('itemFor', () => {
  it("counts the song's place within its rule's songs in its row", () => {
    const t = trace([slot(1, 1), slot(1, 1), slot(1, 2), slot(2, 1), slot(2, 1), slot(2, 2)]);
    expect(itemFor(t, 1)).toEqual({ row: 1, block: 1, k: 0, rule_strategy: 'weighted', rule_source: 'A' });
    expect(itemFor(t, 2)?.k).toBe(1);
    expect(itemFor(t, 5)).toMatchObject({ row: 2, block: 1, k: 1 });
    expect(itemFor(t, 6)).toMatchObject({ row: 2, block: 2, k: 0, rule_source: 'B' });
    expect(itemFor(t, 7)).toBeNull();
    expect(itemFor(t, 0)).toBeNull();
  });
});

describe('changedRule and changedFields', () => {
  it('the rule as a panel change makes it, its size kept', () => {
    const rule = changedRule(flat, sidecar as never, {
      kind: 'patchBlock', address: { slot_index: 1 }, fields: { source: 'A', window: null, size: 9 },
    });
    expect(rule).toEqual({ source: 'A', strategy: 'weighted', size: 2 });
    expect(changedFields(flat, 2, rule!)).toEqual({ source: 'A', window: null });
    // The station's template is untouched.
    expect(flat.layout.flat_slots![1]).toEqual({ source: 'B', strategy: 'weighted', size: 2, window: 5 });
  });

  it('a retype', () => {
    const rule = changedRule(flat, sidecar as never, {
      kind: 'retypeBlock', address: { slot_index: 0 }, newStrategy: 'sequential', newSource: 'B',
      newFields: { cursor_id: 'C' },
    });
    expect(rule).toMatchObject({ source: 'B', strategy: 'sequential', size: 3, cursor_id: 'C' });
    expect(changedFields(flat, 1, rule!)).toMatchObject({ source: 'B', strategy: 'sequential', cursor_id: 'C' });
  });

  it('nothing changed but the size is no change', () => {
    const rule = changedRule(flat, sidecar as never, { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 1 } });
    expect(changedFields(flat, 1, rule!)).toEqual({});
  });

  it("isn't of a rule that isn't there", () => {
    expect(changedRule(flat, sidecar as never, { kind: 'patchBlock', address: { slot_index: 5 }, fields: {} })).toBeNull();
    expect(changedRule(flat, sidecar as never, { kind: 'patchBlock', address: {}, fields: {} })).toBeNull();
  });
});

describe('changesAfter', () => {
  const swap = { row: 1, block: 2, k: 1, kind: 'swap' as const, rule_strategy: 'weighted', rule_source: 'B' };

  it('follows each change to where its rule plays: the shared slotAfter cases', () => {
    type Case = { op: Record<string, unknown>; p: number; after: number | null };
    for (const c of shared as Case[]) {
      const isSize = c.op.kind === 'structuralBlockEdit' && (c.op.edit as { op: string }).op === 'size';
      // k = 0: a size of 2 keeps it (the size cases' new size is 2).
      const got = changesAfter([{ ...swap, block: c.p + 1, k: 0 }], [c.op as unknown as Edit]);
      expect(got.map((x) => x.block - 1), JSON.stringify(c)).toEqual(c.after === null ? [] : [isSize ? c.p : c.after]);
    }
  });

  it('through several edits in order; a removed rule or a song past a smaller size goes', () => {
    const add: Edit = { kind: 'structuralBlockEdit', edit: { op: 'add', slot_index: 0, new_block: {} } };
    const move: Edit = { kind: 'moveRules', from: 2, count: 1, to: 0, sectionId: 'all' };
    expect(changesAfter([swap], [add, move]).map((x) => x.block)).toEqual([1]);
    const remove: Edit = { kind: 'structuralBlockEdit', edit: { op: 'remove', slot_index: 1 } };
    expect(changesAfter([swap], [remove])).toEqual([]);
    const smaller: Edit = { kind: 'structuralBlockEdit', edit: { op: 'size', slot_index: 1, new_size: 1 } };
    expect(changesAfter([swap], [smaller])).toEqual([]);
    expect(changesAfter([{ ...swap, k: 0 }], [smaller])).toEqual([{ ...swap, k: 0 }]);
    // Other edits leave it be; no edits, the same list.
    const patch: Edit = { kind: 'patchBlock', address: { slot_index: 1 }, fields: { window: 3 } };
    expect(changesAfter([swap], [patch])).toEqual([swap]);
    const list = [swap];
    expect(changesAfter(list, [])).toBe(list);
  });
});

describe('itemsOf', () => {
  it('sends only what the kind takes', () => {
    const stored = [
      { id: 1, row: 1, block: 1, k: 0, kind: 'swap', track_id: 5, rule: null, rule_strategy: 'w', rule_source: 'A',
        track: null, position: 1, also_plays_at: [], created_at: null },
      { id: 2, row: 1, block: 2, k: 0, kind: 'rule', track_id: null, rule: { source: 'A' }, rule_strategy: 'w',
        rule_source: 'B', track: null, position: 4, also_plays_at: [], created_at: null },
    ] as WeekChange[];
    expect(itemsOf(stored)).toEqual([
      { row: 1, block: 1, k: 0, kind: 'swap', track_id: 5, rule: null, rule_strategy: 'w', rule_source: 'A' },
      { row: 1, block: 2, k: 0, kind: 'rule', track_id: null, rule: { source: 'A' }, rule_strategy: 'w', rule_source: 'B' },
    ]);
  });
});

describe('whyNotJustThisWeek', () => {
  const t = trace([slot(1, 1), slot(1, 1), slot(1, 1), slot(1, 2), slot(1, 2)]);
  const base = { trace: t, template: flat, slot: t.slots[0], viewingCurrent: true, queued: 0 };
  it('null when the song can be changed', () => {
    expect(whyNotJustThisWeek(base)).toBeNull();
  });
  it('says why not', () => {
    expect(whyNotJustThisWeek({ ...base, viewingCurrent: false })).toMatch(/current version/);
    expect(whyNotJustThisWeek({ ...base, slot: undefined })).toMatch(/no longer in the week/);
    expect(whyNotJustThisWeek({ ...base, queued: 2 })).toMatch(/Save or discard/);
    const moved = { ...flat, layout: { ...flat.layout, flat_slots: [flat.layout.flat_slots![1], flat.layout.flat_slots![0]] } };
    expect(whyNotJustThisWeek({ ...base, template: moved })).toMatch(/regenerate it first/);
  });
});

describe('changeWords', () => {
  it('a swap, and a rule change by what it changed (source and kind first)', () => {
    expect(changeWords({ kind: 'swap', track: { name: 'Song', artist: 'Band' } })).toBe('Swapped for Song by Band');
    expect(changeWords({ kind: 'swap', track: { name: 'Song', artist: null } })).toBe('Swapped for Song');
    expect(changeWords({ kind: 'swap', track: null })).toBe('Swapped for a song no longer in the library');
    expect(changeWords({ kind: 'rule', fields: { window: 3, source: 'B', strategy: 'pick', genre: null } }))
      .toBe('Picked by its rule changed: source B, strategy pick, no genre, window 3');
    expect(changeWords({ kind: 'rule', fields: { allow: ['X', 'Y'] } })).toBe('Picked by its rule changed: allow X, Y');
    expect(changeWords({ kind: 'rule', fields: {} })).toBe('Picked by its rule changed');
  });
});

describe('positionOf and songLabel', () => {
  const t = trace([slot(1, 1, { unit: 'Mon' }), slot(1, 2, { unit: 'Mon' }), slot(2, 1, { unit: 'Tue' }), slot(2, 1, { unit: 'Tue' })]);
  it('finds the song, and says it with its day', async () => {
    const { positionOf, songLabel } = await import('./weekChanges');
    expect(positionOf(t, 2, 1, 1)).toBe(4);
    expect(positionOf(t, 1, 2, 0)).toBe(2);
    expect(positionOf(t, 1, 2, 1)).toBeNull();
    expect(songLabel(t, 4)).toBe('song 4 (Tue)');
    expect(songLabel(t, null)).toBe('a song no longer in the week');
    expect(songLabel(trace([slot(1, 1)]), 1)).toBe('song 1');
  });
});
