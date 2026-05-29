import { describe, it, expect } from 'vitest';
import {
  DEFAULT_CONTROLS,
  availableCadences,
  filterSortStations,
  lastGeneratedLabel,
  relativeTime,
  type StationListControls,
} from './stations';
import type { StationSummary } from './api';

/**
 * Pure-logic tests for the §16.12 stations-list helpers. The
 * landing component is a thin shell over these; testing the logic
 * here keeps the component test focused on rendering.
 */

function st(over: Partial<StationSummary> = {}): StationSummary {
  return {
    id: 1,
    slug: 'a-station',
    name: 'A Station',
    cadence: 'weekly',
    playlist_count: 0,
    last_generated_week: null,
    last_generated_at: null,
    updated_at: '2026-05-01T00:00:00Z',
    ...over,
  };
}

const ctrl = (over: Partial<StationListControls> = {}): StationListControls => ({
  ...DEFAULT_CONTROLS,
  ...over,
});


// ---- availableCadences -------------------------------------------------


describe('availableCadences', () => {
  it('returns distinct cadences sorted', () => {
    const out = availableCadences([
      st({ cadence: 'weekly' }),
      st({ cadence: 'monthly' }),
      st({ cadence: 'weekly' }),
    ]);
    expect(out).toEqual(['monthly', 'weekly']);
  });

  it('returns empty for no stations', () => {
    expect(availableCadences([])).toEqual([]);
  });
});


// ---- filterSortStations: search ----------------------------------------


describe('filterSortStations — search', () => {
  const rows = [
    st({ slug: 'rebirth-classics', name: 'Rebirth Classics' }),
    st({ slug: 'rebirth-radio', name: 'Rebirth Radio' }),
    st({ slug: 'jazz-fm', name: 'Jazz FM' }),
  ];

  it('matches on name (case-insensitive)', () => {
    const out = filterSortStations(rows, ctrl({ search: 'JAZZ' }));
    expect(out.map((s) => s.slug)).toEqual(['jazz-fm']);
  });

  it('matches on slug', () => {
    const out = filterSortStations(rows, ctrl({ search: 'radio' }));
    expect(out.map((s) => s.slug)).toEqual(['rebirth-radio']);
  });

  it('substring matches multiple', () => {
    const out = filterSortStations(rows, ctrl({ search: 'rebirth' }));
    expect(out.map((s) => s.slug).sort()).toEqual([
      'rebirth-classics',
      'rebirth-radio',
    ]);
  });

  it('trims + ignores empty search', () => {
    expect(filterSortStations(rows, ctrl({ search: '   ' }))).toHaveLength(3);
  });
});


// ---- filterSortStations: filters ---------------------------------------


describe('filterSortStations — filters', () => {
  const rows = [
    st({ slug: 'w-empty', cadence: 'weekly', playlist_count: 0 }),
    st({ slug: 'w-full', cadence: 'weekly', playlist_count: 5 }),
    st({ slug: 'm-full', cadence: 'monthly', playlist_count: 3 }),
  ];

  it('filters by cadence', () => {
    const out = filterSortStations(rows, ctrl({ cadence: 'monthly' }));
    expect(out.map((s) => s.slug)).toEqual(['m-full']);
  });

  it('filters to has-generations', () => {
    const out = filterSortStations(rows, ctrl({ generation: 'has' }));
    expect(out.map((s) => s.slug).sort()).toEqual(['m-full', 'w-full']);
  });

  it('filters to empty', () => {
    const out = filterSortStations(rows, ctrl({ generation: 'empty' }));
    expect(out.map((s) => s.slug)).toEqual(['w-empty']);
  });

  it('AND-combines cadence + generation', () => {
    const out = filterSortStations(
      rows, ctrl({ cadence: 'weekly', generation: 'has' }),
    );
    expect(out.map((s) => s.slug)).toEqual(['w-full']);
  });
});


// ---- filterSortStations: sort ------------------------------------------


describe('filterSortStations — sort', () => {
  const rows = [
    st({ slug: 'b', name: 'Bravo', last_generated_at: '2026-05-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' }),
    st({ slug: 'a', name: 'Alpha', last_generated_at: '2026-05-03T00:00:00Z', updated_at: '2026-03-01T00:00:00Z' }),
    st({ slug: 'c', name: 'Charlie', last_generated_at: null, updated_at: '2026-02-01T00:00:00Z' }),
  ];

  it('sorts by name (default)', () => {
    const out = filterSortStations(rows, ctrl({ sortBy: 'name' }));
    expect(out.map((s) => s.name)).toEqual(['Alpha', 'Bravo', 'Charlie']);
  });

  it('sorts by last-generated DESC with nulls last', () => {
    const out = filterSortStations(rows, ctrl({ sortBy: 'last-generated' }));
    // 05-03 (a), 05-01 (b), then null (c).
    expect(out.map((s) => s.slug)).toEqual(['a', 'b', 'c']);
  });

  it('sorts by updated DESC', () => {
    const out = filterSortStations(rows, ctrl({ sortBy: 'updated' }));
    // 03-01 (a), 02-01 (c), 01-01 (b).
    expect(out.map((s) => s.slug)).toEqual(['a', 'c', 'b']);
  });

  it('uses slug as a stable tiebreaker on equal sort keys', () => {
    const tied = [
      st({ slug: 'z', name: 'Same' }),
      st({ slug: 'a', name: 'Same' }),
    ];
    const out = filterSortStations(tied, ctrl({ sortBy: 'name' }));
    expect(out.map((s) => s.slug)).toEqual(['a', 'z']);
  });

  it('does not mutate the input array', () => {
    const input = [st({ slug: 'b' }), st({ slug: 'a' })];
    const snapshot = input.map((s) => s.slug);
    filterSortStations(input, ctrl({ sortBy: 'name' }));
    expect(input.map((s) => s.slug)).toEqual(snapshot);
  });
});


// ---- relativeTime ------------------------------------------------------


describe('relativeTime', () => {
  const now = new Date('2026-05-10T12:00:00Z');

  it('returns empty for null', () => {
    expect(relativeTime(null, now)).toBe('');
  });

  it('buckets seconds as "just now"', () => {
    expect(relativeTime('2026-05-10T11:59:30Z', now)).toBe('just now');
  });

  it('buckets minutes (singular + plural)', () => {
    expect(relativeTime('2026-05-10T11:59:00Z', now)).toBe('1 minute ago');
    expect(relativeTime('2026-05-10T11:30:00Z', now)).toBe('30 minutes ago');
  });

  it('buckets hours', () => {
    expect(relativeTime('2026-05-10T09:00:00Z', now)).toBe('3 hours ago');
  });

  it('buckets days', () => {
    expect(relativeTime('2026-05-07T12:00:00Z', now)).toBe('3 days ago');
  });

  it('buckets weeks', () => {
    expect(relativeTime('2026-04-19T12:00:00Z', now)).toBe('3 weeks ago');
  });

  it('buckets months', () => {
    expect(relativeTime('2026-02-10T12:00:00Z', now)).toBe('2 months ago');
  });

  it('buckets years', () => {
    // 2025-05-10 → 2026-05-10 is 365 days → 1 year.
    expect(relativeTime('2025-05-10T12:00:00Z', now)).toBe('1 year ago');
    // 2024-05-09 → 2026-05-10 is ~2 years.
    expect(relativeTime('2024-05-09T12:00:00Z', now)).toBe('2 years ago');
  });

  it('reads "12 months ago" in the 360-364 day gap, NOT "0 years ago"', () => {
    // Full-slice review 🟡-1 regression guard: at 360-364 days
    // `months` floors to 12 (so the old `months < 12` gate rejected
    // it) while `years = floor(days/365)` floors to 0 — producing
    // "0 years ago". 2025-05-14 → 2026-05-10 is 361 days.
    expect(relativeTime('2025-05-14T12:00:00Z', now)).toBe('12 months ago');
    // 364 days (2025-05-11) still months, not a broken year.
    expect(relativeTime('2025-05-11T12:00:00Z', now)).toBe('12 months ago');
    // The boundary holds: 365 days flips to "1 year ago".
    expect(relativeTime('2025-05-10T12:00:00Z', now)).toBe('1 year ago');
  });

  it('returns "just now" for a future timestamp (clock skew)', () => {
    expect(relativeTime('2026-05-10T12:30:00Z', now)).toBe('just now');
  });

  it('returns empty for an unparseable string', () => {
    expect(relativeTime('not-a-date', now)).toBe('');
  });
});


// ---- lastGeneratedLabel ------------------------------------------------


describe('lastGeneratedLabel', () => {
  const now = new Date('2026-05-10T12:00:00Z');

  it('formats cadence + zero-padded week + relative time', () => {
    const label = lastGeneratedLabel(
      st({ cadence: 'weekly', last_generated_week: 26, last_generated_at: '2026-05-07T12:00:00Z' }),
      now,
    );
    expect(label).toBe('WEEKLY 026 · 3 days ago');
  });

  it('returns null when never generated (null week)', () => {
    expect(lastGeneratedLabel(st({ last_generated_week: null }), now)).toBeNull();
  });

  it('returns null when timestamp is null even if week is set', () => {
    // Defensive: the backend keeps these consistent (§16.12 c1 review
    // 🟡-1), but the helper guards both.
    expect(
      lastGeneratedLabel(
        st({ last_generated_week: 5, last_generated_at: null }), now,
      ),
    ).toBeNull();
  });
});
