import { describe, it, expect } from 'vitest';
import { templateDiff, isClean, type DiffableStation } from './templateDiff';

/**
 * Pure-function tests for the edit-mode diff comparator (DESIGN.md §16.7 D7).
 *
 * The comparator's job is to give the user a "what's actually changing
 * before I save" summary. Many edge cases worth pinning explicitly so a
 * future refactor doesn't silently drop a field from comparison.
 */

function station(over: Partial<DiffableStation['template']> = {}): DiffableStation {
  return {
    slug: 'cls',
    name: 'Classics',
    template: {
      station: 'cls',
      cadence: 'weekly',
      rng: 'off',
      sources: [
        { name: 'A', colour: '#c0392b', role: 'content' },
        { name: 'B', colour: '#8e44ad', role: 'content' },
      ],
      cursor: { base_advance_per_week: 60, cycle_weeks: 16 },
      layout: {
        unit: 'day',
        unit_labels: ['Mon', 'Tue', 'Wed'],
        row_start_offsets: [0, 10, 20],
        row_template: [{ source: 'A', strategy: 'window', size: 215 }],
      },
      ...over,
    },
  };
}

describe('templateDiff', () => {
  it('returns four sections with empty changes when before === after', () => {
    const a = station();
    const b = station();
    const diff = templateDiff(a, b);

    expect(diff.map((s) => s.name)).toEqual(['Identity', 'Sources', 'Cursor', 'Layout']);
    expect(diff.every((s) => s.changes.length === 0)).toBe(true);
    expect(isClean(diff)).toBe(true);
  });

  it('detects display-name change in Identity', () => {
    const a = station();
    const b = { ...a, name: 'Rebirth Classics Reissue' };
    const diff = templateDiff(a, b);
    const id = diff[0];

    expect(id.changes).toEqual([
      { label: 'name', before: '"Classics"', after: '"Rebirth Classics Reissue"' },
    ]);
    expect(isClean(diff)).toBe(false);
  });

  it('detects RNG flip + seed addition in Identity', () => {
    const a = station();
    const b = station({ rng: 'on', seed: 42 });
    const id = templateDiff(a, b)[0];

    expect(id.changes).toContainEqual({ label: 'rng', before: '"off"', after: '"on"' });
    expect(id.changes).toContainEqual({
      label: 'seed',
      before: '(unset)',
      after: '42',
    });
  });

  it('does not surface slug changes in normal use (immutable)', () => {
    // Defensive — slug should never change post-creation, but if the input
    // somehow does change the diff makes it visible rather than swallowing.
    const a = station();
    const b = { ...a, slug: 'classics-v2' };
    const id = templateDiff(a, b)[0];

    expect(id.changes).toContainEqual({
      label: 'slug',
      before: '"cls"',
      after: '"classics-v2"',
    });
  });

  it('matches sources by name (not array index) — reorder is no diff', () => {
    const a = station();
    const b = station({
      sources: [
        { name: 'B', colour: '#8e44ad', role: 'content' }, // swapped order
        { name: 'A', colour: '#c0392b', role: 'content' },
      ],
    });
    const sources = templateDiff(a, b)[1];
    expect(sources.changes).toEqual([]);
  });

  it('detects added / removed / modified sources independently', () => {
    const a = station();
    const b = station({
      sources: [
        // A modified (colour change)
        { name: 'A', colour: '#e74c3c', role: 'content' },
        // B removed
        // C added
        { name: 'C', colour: '#7f8c8d', role: 'reference' },
      ],
    });
    const sources = templateDiff(a, b)[1];

    expect(sources.changes).toContainEqual({
      label: 'source A',
      before: '#c0392b / content',
      after: '#e74c3c / content',
    });
    expect(sources.changes).toContainEqual({
      label: 'source B',
      before: '#8e44ad / content',
      after: '(removed)',
    });
    expect(sources.changes).toContainEqual({
      label: 'source C',
      before: '(added)',
      after: '#7f8c8d / reference',
    });
  });

  it('detects cursor changes', () => {
    const a = station();
    const b = station({ cursor: { base_advance_per_week: 50, cycle_weeks: 12 } });
    const cursor = templateDiff(a, b)[2];

    expect(cursor.changes).toEqual([
      { label: 'cursor.base_advance_per_week', before: '60', after: '50' },
      { label: 'cursor.cycle_weeks', before: '16', after: '12' },
    ]);
  });

  it('detects layout array changes (row_start_offsets)', () => {
    const a = station();
    const b = station({
      layout: {
        unit: 'day',
        unit_labels: ['Mon', 'Tue', 'Wed'],
        row_start_offsets: [0, 12, 24], // changed from [0, 10, 20]
        row_template: [{ source: 'A', strategy: 'window', size: 215 }],
      },
    });
    const layout = templateDiff(a, b)[3];

    expect(layout.changes).toContainEqual({
      label: 'layout.row_start_offsets',
      before: '[0,10,20]',
      after: '[0,12,24]',
    });
  });

  it('detects row_template edits with a compact summary', () => {
    const a = station();
    const b = station({
      layout: {
        unit: 'day',
        unit_labels: ['Mon', 'Tue', 'Wed'],
        row_start_offsets: [0, 10, 20],
        row_template: [
          { source: 'A', strategy: 'window', size: 215 },
          { source: 'B', strategy: 'window', size: 215 }, // added block
        ],
      },
    });
    const layout = templateDiff(a, b)[3];

    expect(layout.changes).toContainEqual({
      label: 'layout.row_template',
      before: 'A/window/215',
      after: 'A/window/215, B/window/215',
    });
  });

  it('handles malformed templates without crashing', () => {
    // Defensive: if the API returns a stored template missing fields,
    // the comparator returns sensible diff output rather than throwing.
    const a = { slug: 'x', name: 'X', template: {} };
    const b = { slug: 'x', name: 'X', template: { rng: 'on' } };
    const diff = templateDiff(a, b);

    // rng: undefined → "on" should surface in Identity.
    expect(diff[0].changes).toContainEqual({
      label: 'rng',
      before: '(unset)',
      after: '"on"',
    });
    // Sources / Cursor / Layout sections present but empty (default to []/{}).
    expect(diff.length).toBe(4);
  });
});
