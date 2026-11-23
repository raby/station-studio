import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  ariaSort,
  compareText,
  dateValue,
  nextSort,
  readSort,
  sortRows,
  writeSort,
  type SortColumns,
} from './tableSort';

interface Row {
  name: string;
  tracks: number;
  at: string | null;
}
type Key = 'name' | 'tracks' | 'at';
const columns: SortColumns<Row, Key> = {
  name: { firstDir: 'asc', value: (r) => r.name },
  tracks: { firstDir: 'desc', value: (r) => r.tracks },
  at: { firstDir: 'desc', value: (r) => dateValue(r.at) },
};
const byName = (a: Row, b: Row) => compareText(a.name, b.name);

const rows: Row[] = [
  { name: 'night', tracks: 10, at: '2026-11-01T09:00:00Z' },
  { name: 'Daytime', tracks: 200, at: null },
  { name: 'JINGLES 10', tracks: 10, at: '2026-11-20T09:00:00Z' },
  { name: 'JINGLES 2', tracks: 5, at: 'not a date' },
];
const names = (rs: Row[]) => rs.map((r) => r.name);

describe('sortRows', () => {
  it('sorts names as people read them: case-blind, 2 before 10', () => {
    expect(names(sortRows(rows, { key: 'name', dir: 'asc' }, columns))).toEqual([
      'Daytime',
      'JINGLES 2',
      'JINGLES 10',
      'night',
    ]);
  });

  it('puts the newest first, and the never-imported and unreadable last either way', () => {
    expect(names(sortRows(rows, { key: 'at', dir: 'desc' }, columns, byName))).toEqual([
      'JINGLES 10',
      'night',
      'Daytime',
      'JINGLES 2',
    ]);
    expect(names(sortRows(rows, { key: 'at', dir: 'asc' }, columns, byName))).toEqual([
      'night',
      'JINGLES 10',
      'Daytime',
      'JINGLES 2',
    ]);
  });

  it('breaks ties with the tiebreak, whichever way the column runs', () => {
    expect(names(sortRows(rows, { key: 'tracks', dir: 'desc' }, columns, byName))).toEqual([
      'Daytime',
      'JINGLES 10',
      'night',
      'JINGLES 2',
    ]);
    expect(names(sortRows(rows, { key: 'tracks', dir: 'asc' }, columns, byName))).toEqual([
      'JINGLES 2',
      'JINGLES 10',
      'night',
      'Daytime',
    ]);
  });

  it("leaves the rows it's given alone", () => {
    const before = names(rows);
    sortRows(rows, { key: 'tracks', dir: 'asc' }, columns);
    expect(names(rows)).toEqual(before);
  });
});

describe('nextSort and ariaSort', () => {
  it("starts another column its own way, and flips the sorted one", () => {
    const start = { key: 'name' as Key, dir: 'asc' as const };
    const newest = nextSort(start, 'at', columns);
    expect(newest).toEqual({ key: 'at', dir: 'desc' });
    expect(nextSort(newest, 'at', columns)).toEqual({ key: 'at', dir: 'asc' });
    expect(nextSort(start, 'name', columns)).toEqual({ key: 'name', dir: 'desc' });
  });

  it('marks only the sorted column', () => {
    const s = { key: 'at' as Key, dir: 'desc' as const };
    expect(ariaSort(s, 'at')).toBe('descending');
    expect(ariaSort({ ...s, dir: 'asc' }, 'at')).toBe('ascending');
    expect(ariaSort(s, 'name')).toBeUndefined();
  });
});

describe('readSort and writeSort', () => {
  const fallback = { key: 'name' as Key, dir: 'asc' as const };
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it('remembers the choice', () => {
    writeSort('t:sort', { key: 'at', dir: 'desc' });
    expect(readSort('t:sort', columns, fallback)).toEqual({ key: 'at', dir: 'desc' });
  });

  it.each([
    ['nothing stored', null],
    ['not JSON', '{oops'],
    ['not an object', '"at"'],
    ['a column this table lacks', '{"key":"genre","dir":"asc"}'],
    ["a key that's only on Object's prototype", '{"key":"toString","dir":"asc"}'],
    ['a direction that isn’t one', '{"key":"at","dir":"up"}'],
  ])('falls back on %s', (_why, stored) => {
    if (stored !== null) window.localStorage.setItem('t:sort', stored);
    expect(readSort('t:sort', columns, fallback)).toEqual(fallback);
  });

  it('carries on when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => writeSort('t:sort', { key: 'at', dir: 'desc' })).not.toThrow();
    expect(readSort('t:sort', columns, fallback)).toEqual(fallback);
  });
});
