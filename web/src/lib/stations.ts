/**
 * §16.12 c2 — pure filter / sort / format helpers for the stations
 * list. Extracted from the landing component so the logic is
 * unit-testable without rendering Svelte.
 */
import type { StationSummary } from '$lib/api';


export type CadenceFilter = 'all' | string; // 'all' or a specific cadence
export type GenerationFilter = 'all' | 'has' | 'empty';
export type SortBy = 'name' | 'last-generated' | 'updated';


export interface StationListControls {
  search: string;
  cadence: CadenceFilter;
  generation: GenerationFilter;
  sortBy: SortBy;
}


export const DEFAULT_CONTROLS: StationListControls = {
  search: '',
  cadence: 'all',
  generation: 'all',
  sortBy: 'name',
};


/**
 * The distinct cadences present in the list, sorted — drives the
 * cadence filter chips. Only cadences that actually appear are
 * offered (no empty "monthly" chip if no monthly stations exist).
 */
export function availableCadences(stations: StationSummary[]): string[] {
  return [...new Set(stations.map((s) => s.cadence))].sort();
}


/**
 * Apply search + filters + sort to the station list. Pure: returns
 * a new array, doesn't mutate the input.
 *
 * Search: case-insensitive substring on name OR slug.
 * Cadence: 'all' or exact match.
 * Generation: 'all' | 'has' (playlist_count > 0) | 'empty' (== 0).
 * Sort:
 *   - 'name'           → locale compare on name (default)
 *   - 'last-generated' → last_generated_at DESC, nulls last
 *   - 'updated'        → updated_at DESC, nulls last
 * All sorts use slug as a stable tiebreaker so equal keys don't
 * reorder between renders.
 */
export function filterSortStations(
  stations: StationSummary[],
  controls: StationListControls,
): StationSummary[] {
  const q = controls.search.trim().toLowerCase();

  const filtered = stations.filter((s) => {
    if (q && !s.name.toLowerCase().includes(q) && !s.slug.toLowerCase().includes(q)) {
      return false;
    }
    if (controls.cadence !== 'all' && s.cadence !== controls.cadence) {
      return false;
    }
    if (controls.generation === 'has' && s.playlist_count <= 0) return false;
    if (controls.generation === 'empty' && s.playlist_count > 0) return false;
    return true;
  });

  const bySlug = (a: StationSummary, b: StationSummary) =>
    a.slug.localeCompare(b.slug);

  // ISO timestamps compare correctly as strings (lexicographic ==
  // chronological for same-format UTC). Null → treated as "oldest"
  // so it sorts to the end on DESC.
  const descNullsLast = (av: string | null, bv: string | null) => {
    if (av === bv) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    return bv.localeCompare(av); // DESC
  };

  const sorted = [...filtered];
  if (controls.sortBy === 'name') {
    sorted.sort((a, b) => a.name.localeCompare(b.name) || bySlug(a, b));
  } else if (controls.sortBy === 'last-generated') {
    sorted.sort(
      (a, b) =>
        descNullsLast(a.last_generated_at, b.last_generated_at) || bySlug(a, b),
    );
  } else {
    sorted.sort(
      (a, b) => descNullsLast(a.updated_at, b.updated_at) || bySlug(a, b),
    );
  }
  return sorted;
}


/**
 * Human-relative time string for the "Last generated … (3 days ago)"
 * line. Pure: takes `now` so tests don't depend on the clock.
 * Returns '' for null. Coarse buckets — the operator wants "roughly
 * when", not precision.
 */
export function relativeTime(iso: string | null, now: Date = new Date()): string {
  if (iso === null) return '';
  const then = new Date(iso);
  const ms = now.getTime() - then.getTime();
  if (Number.isNaN(ms)) return '';
  if (ms < 0) return 'just now'; // clock skew / future timestamp
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return 'just now';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? '' : 's'} ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? '' : 's'} ago`;
  const days = Math.floor(hr / 24);
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks} week${weeks === 1 ? '' : 's'} ago`;
  const months = Math.floor(days / 30);
  // Gate on the DAY count, not `months < 12` (full-slice review 🟡-1):
  // at days 360-364 `months` floors to 12 (rejecting the months
  // branch) while `years = floor(days/365)` floors to 0 — producing
  // "0 years ago". Gating on `days < 365` keeps 360-364 reading
  // "12 months ago" and hands ≥365 to the years branch.
  if (days < 365) return `${months} month${months === 1 ? '' : 's'} ago`;
  const years = Math.floor(days / 365);
  return `${years} year${years === 1 ? '' : 's'} ago`;
}


/**
 * Compose the per-row "last generated" label. Returns null when the
 * station has never been generated (the component renders nothing /
 * a muted "never generated" instead).
 *
 * Example: cadence "weekly" + week 26 + 3-days-ago →
 *   "WEEKLY 026 · 3 days ago"
 */
export function lastGeneratedLabel(
  s: StationSummary,
  now: Date = new Date(),
): string | null {
  if (s.last_generated_week === null || s.last_generated_at === null) {
    return null;
  }
  const week = String(s.last_generated_week).padStart(3, '0');
  const rel = relativeTime(s.last_generated_at, now);
  const cadenceLabel = s.cadence.toUpperCase();
  return rel ? `${cadenceLabel} ${week} · ${rel}` : `${cadenceLabel} ${week}`;
}
