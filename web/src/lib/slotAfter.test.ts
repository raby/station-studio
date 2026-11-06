/**
 * The editor's `slotAfter` against the server's twin
 * (engine/week_change_store.py `slot_after`): just-this-week changes
 * follow their rule through saved edits as the editor's queue does.
 * Cases: `tests/fixtures/slot_after.json`.
 */
import { describe, expect, it } from 'vitest';
import shared from '../../../tests/fixtures/slot_after.json';
import { slotAfter, type Edit } from './edit-ops';

type Case = { op: Record<string, unknown>; p: number; after: number | null };

describe('slotAfter matches the server', () => {
  const cases = shared as Case[];
  it('has cases of every kind', () => {
    const kinds = new Set(cases.map((c) => (c.op.kind === 'structuralBlockEdit' ? (c.op.edit as { op: string }).op : c.op.kind)));
    expect([...kinds].sort()).toEqual(['add', 'moveRules', 'moveSection', 'remove', 'size']);
  });
  it('agrees on every case', () => {
    const wrong = cases.filter((c) => (slotAfter(c.op as unknown as Edit, c.p) ?? null) !== c.after);
    expect(wrong).toEqual([]);
  });
});
