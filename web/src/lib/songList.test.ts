import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import type { Trace, TraceSlot } from './api';
import type { Edit } from './edit-ops';
import type { EngineBlock, Sidecar, StationTemplate } from './editor';
import { describeQueued, filterGroups, queuedChanges, removedCount, rowRule, ruleDefaultsFrom, ruleKey, ruleText, songGroups, songsChanged } from './songList';

// The two real example stations: Rebirth Radio (flat slots, sections,
// 200-song macros) and Rebirth Classics (a repeating row).
const STATIONS = path.resolve(__dirname, '../../../stations');
function station(slug: string): { template: StationTemplate; sidecar: Sidecar } {
  return {
    template: JSON.parse(fs.readFileSync(path.join(STATIONS, `${slug}.json`), 'utf-8')),
    sidecar: JSON.parse(fs.readFileSync(path.join(STATIONS, `${slug}.editor.json`), 'utf-8')),
  };
}

/** Song i (0-based) from block i of a flat station, numbered as the
 *  engine numbers traces: slot, row and block from 1. */
function slot(i: number, block: EngineBlock, over: Partial<TraceSlot> = {}): TraceSlot {
  return {
    slot: i + 1, unit: 'slot', row: 1, block: i + 1, source: block.source, strategy: block.strategy,
    pool_index: i, track_id: 1000 + i, track_name: `Song ${i}`, track_artist: `Artist ${i}`,
    reason: `${block.strategy} from '${block.source}'`, ...over,
  };
}

function trace(slots: TraceSlot[], week = 1): Trace {
  return { trace_id: 't', station: 's', week, rng: 'r', seed: null, generated_at: '', slots };
}

/** A week of a flat-slots station: one song per block, in order. */
function flatWeek(template: StationTemplate, n?: number): Trace {
  const blocks = template.layout.flat_slots!;
  return trace(blocks.slice(0, n ?? blocks.length).map((b, i) => slot(i, b)));
}

describe('ruleText', () => {
  const b = (over: Partial<EngineBlock>): EngineBlock => ({ source: 'NIGHT', strategy: 'pick', size: 1, ...over });
  it.each([
    [b({}), 'Picked from NIGHT'],
    [b({ strategy: 'window', size: 4 }), '4 in a row from NIGHT'],
    [b({ strategy: 'window', size: 1 }), 'Next from NIGHT'],
    [b({ strategy: 'sequential', cursor_id: 'n1' }), 'Next in order from NIGHT'],
    [b({ strategy: 'sequential', cursor_id: 'n1', size: 4 }), 'Next 4 in order from NIGHT'],
    [b({ strategy: 'weighted' }), 'Shuffled from NIGHT, no artist twice in 10'],
    [b({ strategy: 'weighted', window: 25 }), 'Shuffled from NIGHT, no artist twice in 25'],
    [b({ strategy: 'jingle', source: 'JINGLES', genre: 'Daytime' }), 'Jingle from JINGLES: Daytime'],
    [b({ strategy: 'jingle', source: 'JINGLES' }), 'Jingle from JINGLES'],
    [b({ strategy: 'mix', genre: 'Disco' }), 'Mix from NIGHT: Disco'],
    [b({ strategy: 'mix' }), 'Mix from NIGHT'],
    [b({ strategy: 'mix', genre: 'Disco', exclusion_window: 4 }), 'Mix from NIGHT: Disco, not used in the last 4 weeks'],
    [b({ strategy: 'mix', genre: 'Disco', exclusion_window: 1 }), 'Mix from NIGHT: Disco, not used in the last 1 week'],
    [b({ strategy: 'prev_ref', position: 3 }), "Same as last week's song 3 (else shuffled from NIGHT)"],
    [b({ strategy: 'prev_ref', position: 3, fallback_genre: 'Soul' }), "Same as last week's song 3 (else a Soul mix)"],
    [b({ strategy: 'prev_ref' }), 'Same as last week (else shuffled from NIGHT)'],
    [b({ strategy: 'archive_ref', source: 'CHART', position: 7 }), 'Song 7 from the CHART archive'],
    [b({ strategy: 'archive_ref', source: 'CHART' }), 'From the CHART archive'],
    [b({ allowlist_sources: ['A', 'B'] }), 'Picked from NIGHT, only songs also in A or B'],
  ])('%o → %s', (block, text) => {
    expect(ruleText(block)).toBe(text);
  });
});

describe('ruleText with the station\'s defaults', () => {
  const b = (over: Partial<EngineBlock>): EngineBlock => ({ source: 'NIGHT', strategy: 'pick', size: 1, ...over });
  it('a setting the rule leaves out is the station\'s; one the rule sets wins', () => {
    expect(ruleText(b({ strategy: 'weighted' }), { artistWindow: 25 })).toBe('Shuffled from NIGHT, no artist twice in 25');
    expect(ruleText(b({ strategy: 'weighted', window: 4 }), { artistWindow: 25 })).toBe('Shuffled from NIGHT, no artist twice in 4');
    expect(ruleText(b({ strategy: 'mix', genre: 'Disco' }), { mixExclusionWeeks: 6 }))
      .toBe('Mix from NIGHT: Disco, not used in the last 6 weeks');
    expect(ruleText(b({ strategy: 'mix', genre: 'Disco' }), { mixExclusionWeeks: 0 })).toBe('Mix from NIGHT: Disco');
  });
  it('ruleDefaultsFrom reads the two rules', () => {
    expect(ruleDefaultsFrom([
      { key: 'artist_dedup_window', value: 12 }, { key: 'mix_exclusion_weeks', value: 3 }, { key: 'x', value: ['a'] },
    ])).toEqual({ artistWindow: 12, mixExclusionWeeks: 3 });
    expect(ruleDefaultsFrom([])).toEqual({ artistWindow: undefined, mixExclusionWeeks: undefined });
  });
});

describe('songGroups', () => {
  it('Rebirth Radio: songs in order, under section names, with their rules', () => {
    const { template, sidecar } = station('rebirth-radio');
    const week = flatWeek(template);
    const groups = songGroups(week, template, sidecar);

    expect(groups.flatMap((g) => g.rows).map((r) => r.position)).toEqual(week.slots.map((_, i) => i + 1));
    expect(groups[0].sectionName).toBe(sidecar.sections[0].name);
    // Each section's songs stay together: one run per section here.
    expect(groups.map((g) => g.sectionId)).toEqual(sidecar.sections.map((s) => s.id));

    const [first, second] = groups[0].rows;
    expect(first.block).toEqual(template.layout.flat_slots![0]);
    expect(first.ref).toEqual({ kind: 'atomic-ref', slot_index: 0 });
    expect(first.macroName).toBeNull();
    // Slot 1 is the first of the 200 × weighted macro.
    expect(second.ref).toMatchObject({ kind: 'macro-placeholder', macro_id: 'weighted-1', pick_in_macro: 1 });
    expect(second.macroName).toBe('200 × weighted');
    expect(second.block?.strategy).toBe('weighted');
  });

  it('Rebirth Classics: a repeating row keeps playing order', () => {
    const { template, sidecar } = station('rebirth-classics');
    const row = template.layout.row_template!;
    const slots: TraceSlot[] = [];
    for (let r = 0; r < 3; r++) {
      row.forEach((b, bi) => slots.push(slot(slots.length, b, { row: r + 1, block: bi + 1 })));
    }
    const groups = songGroups(trace(slots), template, sidecar);
    const rows = groups.flatMap((g) => g.rows);
    expect(rows.map((r) => r.slot.slot)).toEqual(slots.map((s) => s.slot));
    expect(rows.every((r) => r.block !== null)).toBe(true);
    expect(rows[1].block).toEqual(row[1]);
    expect(groups[0].sectionName).toBe('Weekly cycle (7 days)');
  });

  it('marks songs that differ from the saved week, and counts removed ones', () => {
    const { template, sidecar } = station('rebirth-radio');
    const saved = flatWeek(template, 5);
    const edited = trace([
      ...saved.slots.slice(0, 2),
      { ...saved.slots[2], track_id: 99, track_name: 'Other song' },
      ...saved.slots.slice(3, 4),
    ]);
    const rows = songGroups(edited, template, sidecar, saved).flatMap((g) => g.rows);
    expect(rows.map((r) => r.change)).toEqual([null, null, 'changed', null]);
    expect(removedCount(edited, saved)).toBe(1);
    expect(removedCount(edited, null)).toBe(0);

    const longer = trace([...saved.slots, slot(5, template.layout.flat_slots![5])]);
    expect(songGroups(longer, template, sidecar, saved).flatMap((g) => g.rows)[5].change).toBe('added');
  });

  it('songs outside every section go in their own run', () => {
    const { template } = station('rebirth-radio');
    const empty: Sidecar = { schema_version: 1, sections: [], macros: [], layout_order: [] };
    const groups = songGroups(flatWeek(template, 3), template, empty);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ sectionId: null, sectionName: 'Not in a section' });
    expect(groups[0].rows.every((r) => r.ref === null && r.block === null)).toBe(true);
  });

  it("notes a song whose block's rule has changed since it was picked", () => {
    const { template, sidecar } = station('rebirth-radio');
    const week = flatWeek(template, 3);
    // An older week: slot 1 was picked by a sequential rule, now weighted.
    week.slots[1] = { ...week.slots[1], strategy: 'sequential' };
    const rows = songGroups(week, template, sidecar).flatMap((g) => g.rows);
    expect(rows.map((r) => r.ruleChanged)).toEqual([false, true, false]);
  });

  it('songsChanged counts different, new and gone songs', () => {
    const { template } = station('rebirth-radio');
    const saved = flatWeek(template, 4);
    const edited = trace([saved.slots[0], { ...saved.slots[1], track_id: 99 }, saved.slots[2]]);
    expect(songsChanged(edited, saved)).toBe(2); // one different, one gone
    expect(songsChanged(saved, saved)).toBe(0);
    expect(songsChanged(edited, null)).toBe(0);
  });

  it('ruleKey: one rule per repeating-row block (every row), per flat slot, per macro', () => {
    expect(ruleKey({ kind: 'atomic-ref', row_idx: 0, block_idx: 1 })).toBe(ruleKey({ kind: 'atomic-ref', row_idx: 4, block_idx: 1 }));
    expect(ruleKey({ kind: 'atomic-ref', row_idx: 0, block_idx: 1 })).not.toBe(ruleKey({ kind: 'atomic-ref', row_idx: 0, block_idx: 0 }));
    expect(ruleKey({ kind: 'atomic-ref', slot_index: 3 })).toBe('slot:3');
    expect(ruleKey({ kind: 'macro-placeholder', macro_id: 'm', pick_in_macro: 1 })).toBe('macro:m');
  });
});

describe('describeQueued', () => {
  it('says which rule each change is to and what it does after it', () => {
    const { template, sidecar } = station('rebirth-classics');
    const section = sidecar.sections.find((s) => s.blocks.some((b) => b.kind === 'atomic' && b.block_idx === 1))!;
    const at = section.blocks.findIndex((b) => b.kind === 'atomic' && b.block_idx === 1) + 1;
    const lines = describeQueued([
      { kind: 'retypeBlock', address: { block_idx: 1 }, newStrategy: 'pick', newSource: null, newFields: {} },
      { kind: 'patchBlock', address: { block_idx: 1 }, fields: { allowlist_sources: ['X'] } },
    ], template, sidecar);
    const source = template.layout.row_template![1].source;
    expect(lines).toEqual([
      `${section.name}, rule ${at}: Picked from ${source}`,
      // Each line is the rule after that change, on top of the ones before.
      `${section.name}, rule ${at}: Picked from ${source}, only songs also in X`,
    ]);
  });

  it("a macro by its name and section, with every song it makes", () => {
    const { template, sidecar } = station('rebirth-radio');
    const macro = sidecar.macros!.find((m) => m.id === 'weighted-1')!;
    const [line] = describeQueued([{ kind: 'patchMacro', macroId: 'weighted-1', fields: { window: 3 } }], template, sidecar);
    expect(line).toMatch(new RegExp(`^${macro.name} in .+: Shuffled from ${macro.spec.source}, no artist twice in 3$`));
  });

  it("other changes use the editor's wording", () => {
    const { template, sidecar } = station('rebirth-radio');
    const [line] = describeQueued(
      [{ kind: 'renameSection', sectionId: sidecar.sections[0].id, name: 'Mornings' }], template, sidecar);
    expect(line).toContain('Mornings');
  });
});

describe('filterGroups', () => {
  const { template, sidecar } = station('rebirth-radio');
  const week = flatWeek(template, 6);
  week.slots[2] = { ...week.slots[2], track_name: 'Night Drive', track_artist: 'The Cure' };
  const groups = songGroups(week, template, sidecar);
  const positions = (gs: ReturnType<typeof filterGroups>) => gs.flatMap((g) => g.rows.map((r) => r.position));

  it('no search: everything, untouched', () => {
    expect(filterGroups(groups, '  ')).toBe(groups);
  });

  it('matches song or artist, any case, every word somewhere', () => {
    expect(positions(filterGroups(groups, 'night drive'))).toEqual([3]);
    expect(positions(filterGroups(groups, 'CURE'))).toEqual([3]);
    expect(positions(filterGroups(groups, 'cure drive'))).toEqual([3]);
    expect(filterGroups(groups, 'cure zzz')).toEqual([]);
  });

  it('matches the rule as written and the macro name', () => {
    const shuffled = positions(filterGroups(groups, 'shuffled'));
    expect(shuffled.length).toBeGreaterThan(0);
    const rows = groups.flatMap((g) => g.rows);
    expect(shuffled).toEqual(rows.filter((r) => rowRule(r).startsWith('Shuffled')).map((r) => r.position));
    expect(positions(filterGroups(groups, '200 ×'))).toEqual(
      rows.filter((r) => r.macroName === '200 × weighted').map((r) => r.position));
  });

  it('only changed songs, alone or with a search', () => {
    const saved = flatWeek(template, 6);
    const edited = trace(saved.slots.map((s, i) => (i === 1 || i === 4 ? { ...s, track_id: 900 + i } : s)));
    const marked = songGroups(edited, template, sidecar, saved);
    expect(positions(filterGroups(marked, '', true))).toEqual([2, 5]);
    expect(positions(filterGroups(marked, 'Song 4', true))).toEqual([5]);
  });
});

describe('queuedChanges', () => {
  it('the same change to identical rules is one change, said once and undone together', () => {
    const { template, sidecar } = station('rebirth-radio');
    const ids = sidecar.macros!.slice(0, 3).map((m) => m.id);
    const other: Edit = { kind: 'renameSection', sectionId: sidecar.sections[0].id, name: 'Mornings' };
    const changes = queuedChanges([
      ...ids.map((macroId) => ({ kind: 'patchMacro', macroId, fields: { window: 3 } }) as Edit),
      other,
    ], template, sidecar);
    expect(changes.map((c) => c.indices)).toEqual([[0, 1, 2], [3]]);
    expect(changes[0].text).toMatch(/, and the 2 rules just like it$/);
    expect(changes[0].text).toContain(describeQueued([{ kind: 'patchMacro', macroId: ids[0], fields: { window: 3 } }], template, sidecar)[0]);
  });

  it('different settings, or the same rule twice, stay separate changes', () => {
    const { template, sidecar } = station('rebirth-radio');
    const [a, b] = sidecar.macros!.map((m) => m.id);
    const edit = (macroId: string, window: number): Edit => ({ kind: 'patchMacro', macroId, fields: { window } });
    expect(queuedChanges([edit(a, 3), edit(b, 4)], template, sidecar).map((c) => c.indices)).toEqual([[0], [1]]);
    expect(queuedChanges([edit(a, 3), edit(a, 3)], template, sidecar).map((c) => c.indices)).toEqual([[0], [1]]);
  });
});
