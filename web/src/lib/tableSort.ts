/**
 * Sortable tables: which column a list is sorted by, which way, and
 * remembering that choice in localStorage.
 *
 * A column says which way its first click sorts (`firstDir`): newest or
 * most first where that's what you'd look for (Last imported, Tracks),
 * A to Z for names. Clicking the sorted column again flips it.
 *
 * Empty values (null, undefined, an unparseable date) always sort last,
 * whichever way the column runs, so "never imported" never crowds the
 * top. Ties keep a stable order through `tiebreak` (by name, usually).
 *
 * Storage is wrapped as in stationPrefs.ts: localStorage can be missing
 * (SSR), blocked (privacy mode) or full. A failed read gives the
 * default; a failed write just doesn't persist.
 */

export type SortDir = 'asc' | 'desc';

export interface SortState<K extends string = string> {
  key: K;
  dir: SortDir;
}

export interface SortColumn<T> {
  /** The direction of the first click on this column. */
  firstDir: SortDir;
  /** What to compare. null/undefined/NaN sort last either way. */
  value: (row: T) => string | number | null | undefined;
}

export type SortColumns<T, K extends string> = Record<K, SortColumn<T>>;

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

/** Compare two names as people read them: case-blind, 2 before 10. */
export const compareText = (a: string, b: string): number => collator.compare(a, b);

/** An ISO date as a number to sort by; null when missing or unparseable. */
export function dateValue(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

function isEmpty(v: string | number | null | undefined): boolean {
  return v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v));
}

/** A sorted copy of `rows`. The original isn't touched. */
export function sortRows<T, K extends string>(
  rows: readonly T[],
  state: SortState<K>,
  columns: SortColumns<T, K>,
  tiebreak: (a: T, b: T) => number = () => 0,
): T[] {
  const column = columns[state.key];
  const sign = state.dir === 'asc' ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i, v: column.value(row) }))
    .sort((a, b) => {
      const ae = isEmpty(a.v);
      const be = isEmpty(b.v);
      if (ae || be) {
        if (ae && be) return tiebreak(a.row, b.row) || a.i - b.i;
        return ae ? 1 : -1; // empty last, whichever way
      }
      const c =
        typeof a.v === 'number' && typeof b.v === 'number'
          ? a.v - b.v
          : compareText(String(a.v), String(b.v));
      return sign * c || tiebreak(a.row, b.row) || a.i - b.i;
    })
    .map((x) => x.row);
}

/** The sort after a click on `key`: the same column flips, another
 *  starts its own first way. */
export function nextSort<T, K extends string>(
  current: SortState<K>,
  key: K,
  columns: SortColumns<T, K>,
): SortState<K> {
  if (current.key === key) return { key, dir: current.dir === 'asc' ? 'desc' : 'asc' };
  return { key, dir: columns[key].firstDir };
}

/** The `aria-sort` for a column's header: only the sorted one says. */
export function ariaSort<K extends string>(
  state: SortState<K>,
  key: K,
): 'ascending' | 'descending' | undefined {
  if (state.key !== key) return undefined;
  return state.dir === 'asc' ? 'ascending' : 'descending';
}

/** The remembered sort, or `fallback` when there's none, it can't be
 *  read, or it names a column or direction this table doesn't have. */
export function readSort<T, K extends string>(
  storageKey: string,
  columns: SortColumns<T, K>,
  fallback: SortState<K>,
): SortState<K> {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (raw === null) return fallback;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return fallback;
    const { key, dir } = parsed as { key?: unknown; dir?: unknown };
    if (typeof key !== 'string' || !Object.prototype.hasOwnProperty.call(columns, key)) return fallback;
    if (dir !== 'asc' && dir !== 'desc') return fallback;
    return { key: key as K, dir };
  } catch {
    return fallback;
  }
}

export function writeSort(storageKey: string, state: SortState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(storageKey, JSON.stringify({ key: state.key, dir: state.dir }));
  } catch {
    // Quota or privacy-mode block: the choice just isn't remembered.
  }
}
