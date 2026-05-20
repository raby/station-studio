import { describe, it, expect } from 'vitest';
import {
  defaults,
  unpack,
  buildTemplate,
  preSubmitProblem,
  parseIntList,
  parseStringList,
} from './template';
import type { FormState, StationFormPayload } from './template';

// ---------------------------------------------------------------------------
// parseIntList / parseStringList
// ---------------------------------------------------------------------------

describe('parseIntList', () => {
  it('parses comma-separated integers', () => {
    expect(parseIntList('0, 10, 20')).toEqual([0, 10, 20]);
  });
  it('filters out non-finite values', () => {
    expect(parseIntList('1, foo, 3')).toEqual([1, 3]);
  });
  it('handles empty string', () => {
    expect(parseIntList('')).toEqual([]);
  });
  it('handles trailing comma', () => {
    expect(parseIntList('5, 10,')).toEqual([5, 10]);
  });
});

describe('parseStringList', () => {
  it('parses comma-separated strings and trims whitespace', () => {
    expect(parseStringList('Mon, Tue, Wed')).toEqual(['Mon', 'Tue', 'Wed']);
  });
  it('filters empty segments', () => {
    expect(parseStringList('Mon,,Wed')).toEqual(['Mon', 'Wed']);
  });
  it('handles empty string', () => {
    expect(parseStringList('')).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// defaults
// ---------------------------------------------------------------------------

describe('defaults', () => {
  it('returns a fresh FormState with expected shape', () => {
    const d = defaults();
    expect(d.slug).toBe('');
    expect(d.cadence).toBe('weekly');
    expect(d.rng).toBe('off');
    expect(d.sources).toHaveLength(1);
    expect(d.rowTemplate).toHaveLength(1);
  });

  it('returns a new object each call (no shared references)', () => {
    const a = defaults();
    const b = defaults();
    expect(a).toEqual(b);
    expect(a).not.toBe(b);
    expect(a.sources).not.toBe(b.sources);
  });
});

// ---------------------------------------------------------------------------
// buildTemplate
// ---------------------------------------------------------------------------

describe('buildTemplate', () => {
  it('produces the expected template shape from form state', () => {
    const state: FormState = {
      slug: 'cls',
      name: 'Classics',
      cadence: 'weekly',
      rng: 'off',
      seedStr: '',
      sources: [{ name: 'RG_RADIO', colour: '#c0392b', role: 'content' }],
      baseAdvancePerWeek: 60,
      cycleWeeks: 16,
      unit: 'day',
      unitLabels: ['Mon', 'Tue'],
      rowStartOffsets: [0, 10],
      rowTemplate: [{ source: 'RG_RADIO', strategy: 'window', size: 215 }],
    };

    const t = buildTemplate(state);
    expect(t.station).toBe('cls');
    expect(t.name).toBe('Classics');
    expect(t.cadence).toBe('weekly');
    expect(t.rng).toBe('off');
    expect(t).not.toHaveProperty('seed');
    expect((t.cursor as any).base_advance_per_week).toBe(60);
    expect((t.layout as any).unit_labels).toEqual(['Mon', 'Tue']);
    expect((t.layout as any).row_start_offsets).toEqual([0, 10]);
  });

  it('includes seed when rng is on and seedStr is non-empty', () => {
    const state = { ...defaults(), slug: 'x', name: 'X', rng: 'on' as const, seedStr: '42' };
    const t = buildTemplate(state);
    expect(t.seed).toBe(42);
  });

  it('omits seed when rng is off even if seedStr is populated', () => {
    const state = { ...defaults(), slug: 'x', name: 'X', rng: 'off' as const, seedStr: '42' };
    const t = buildTemplate(state);
    expect(t).not.toHaveProperty('seed');
  });

  it('trims whitespace from slug, name, and source names', () => {
    const state: FormState = {
      ...defaults(),
      slug: '  abc  ',
      name: '  Name  ',
      sources: [{ name: '  SRC  ', colour: '#000', role: 'content' }],
    };
    const t = buildTemplate(state);
    expect(t.station).toBe('abc');
    expect(t.name).toBe('Name');
    expect((t.sources as any[])[0].name).toBe('SRC');
  });
});

// ---------------------------------------------------------------------------
// unpack
// ---------------------------------------------------------------------------

describe('unpack', () => {
  const payload: StationFormPayload = {
    slug: 'cls',
    name: 'Classics',
    template: {
      station: 'cls',
      cadence: 'weekly',
      rng: 'on',
      seed: 99,
      sources: [
        { name: 'A', colour: '#c0392b', role: 'content' },
        { name: 'B', colour: '#8e44ad', role: 'reference' },
      ],
      cursor: { base_advance_per_week: 60, cycle_weeks: 16 },
      layout: {
        unit: 'day',
        unit_labels: ['Mon', 'Tue', 'Wed'],
        row_start_offsets: [0, 10, 20],
        row_template: [{ source: 'A', strategy: 'window', size: 215 }],
      },
    },
  };

  it('populates all fields from a well-formed template', () => {
    const s = unpack(payload);
    expect(s.slug).toBe('cls');
    expect(s.name).toBe('Classics');
    expect(s.cadence).toBe('weekly');
    expect(s.rng).toBe('on');
    expect(s.seedStr).toBe('99');
    expect(s.sources).toHaveLength(2);
    expect(s.baseAdvancePerWeek).toBe(60);
    expect(s.cycleWeeks).toBe(16);
    expect(s.unitLabels).toEqual(['Mon', 'Tue', 'Wed']);
    expect(s.rowStartOffsets).toEqual([0, 10, 20]);
    expect(s.rowTemplate).toHaveLength(1);
  });

  it('falls back to defaults for missing template fields', () => {
    const minimal: StationFormPayload = {
      slug: 'x',
      name: 'X',
      template: {},
    };
    const s = unpack(minimal);
    const d = defaults();
    expect(s.cadence).toBe(d.cadence);
    expect(s.rng).toBe(d.rng);
    expect(s.sources).toEqual(d.sources);
    expect(s.unitLabels).toEqual(d.unitLabels);
  });
});

// ---------------------------------------------------------------------------
// round-trip: unpack(buildTemplate(state)) ≈ identity
// ---------------------------------------------------------------------------

describe('round-trip: unpack ↔ buildTemplate', () => {
  it('unpack(payload-from-buildTemplate) reproduces the original state', () => {
    const original: FormState = {
      slug: 'rt',
      name: 'Round Trip',
      cadence: 'weekly',
      rng: 'on',
      seedStr: '12345',
      sources: [
        { name: 'A', colour: '#c0392b', role: 'content' },
        { name: 'B', colour: '#8e44ad', role: 'content' },
        { name: 'C', colour: '#7f8c8d', role: 'reference' },
      ],
      baseAdvancePerWeek: 60,
      cycleWeeks: 16,
      unit: 'day',
      unitLabels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
      rowStartOffsets: [0, 10, 20, 30, 40, 50, 50],
      rowTemplate: [
        { source: 'A', strategy: 'window', size: 215 },
        { source: 'B', strategy: 'window', size: 215 },
      ],
    };

    const template = buildTemplate(original);
    const payload: StationFormPayload = {
      slug: original.slug,
      name: original.name,
      template,
    };
    const roundTripped = unpack(payload);

    expect(roundTripped).toEqual(original);
  });

  it('round-trips cleanly with rng:off (seed omitted from JSON)', () => {
    // Review N4 (§14.20): the rng:on case above exercises the seed path;
    // this pins the rng:off path where buildTemplate omits `seed` entirely
    // and unpack must produce seedStr='' from the absence.
    const original: FormState = {
      slug: 'no-rng',
      name: 'No RNG',
      cadence: 'weekly',
      rng: 'off',
      seedStr: '',
      sources: [{ name: 'A', colour: '#c0392b', role: 'content' }],
      baseAdvancePerWeek: 60,
      cycleWeeks: 16,
      unit: 'day',
      unitLabels: ['Mon', 'Tue'],
      rowStartOffsets: [0, 10],
      rowTemplate: [{ source: 'A', strategy: 'window', size: 215 }],
    };

    const template = buildTemplate(original);
    expect(template).not.toHaveProperty('seed');

    const payload: StationFormPayload = {
      slug: original.slug,
      name: original.name,
      template,
    };
    const roundTripped = unpack(payload);

    expect(roundTripped).toEqual(original);
  });
});

// ---------------------------------------------------------------------------
// preSubmitProblem
// ---------------------------------------------------------------------------

describe('preSubmitProblem', () => {
  it('returns null for a valid state', () => {
    const state: FormState = {
      slug: 'cls',
      name: 'Classics',
      cadence: 'weekly',
      rng: 'off',
      seedStr: '',
      sources: [{ name: 'A', colour: '#000', role: 'content' }],
      baseAdvancePerWeek: 60,
      cycleWeeks: 16,
      unit: 'day',
      unitLabels: ['Mon'],
      rowStartOffsets: [0],
      rowTemplate: [{ source: 'A', strategy: 'window', size: 1 }],
    };
    expect(preSubmitProblem(state)).toBeNull();
  });

  it('catches empty slug', () => {
    const state = { ...defaults(), name: 'X' };
    expect(preSubmitProblem(state)).toMatch(/slug/i);
  });

  it('catches invalid slug format', () => {
    const state = { ...defaults(), slug: 'UPPER', name: 'X' };
    expect(preSubmitProblem(state)).toMatch(/slug/i);
  });

  it('catches empty name', () => {
    const state = { ...defaults(), slug: 'x' };
    expect(preSubmitProblem(state)).toMatch(/name/i);
  });

  it('catches no content sources', () => {
    const state: FormState = {
      ...defaults(),
      slug: 'x',
      name: 'X',
      sources: [{ name: 'A', colour: '#000', role: 'reference' }],
    };
    expect(preSubmitProblem(state)).toMatch(/content source/i);
  });

  it('catches empty row template', () => {
    const state: FormState = {
      ...defaults(),
      slug: 'x',
      name: 'X',
      sources: [{ name: 'A', colour: '#000', role: 'content' }],
      rowTemplate: [],
    };
    expect(preSubmitProblem(state)).toMatch(/row-template/i);
  });

  it('catches block without source reference', () => {
    const state: FormState = {
      ...defaults(),
      slug: 'x',
      name: 'X',
      sources: [{ name: 'A', colour: '#000', role: 'content' }],
      rowTemplate: [{ source: '', strategy: 'pick', size: 1 }],
    };
    expect(preSubmitProblem(state)).toMatch(/source/i);
  });
});


// §14.33 — isAdvancedTemplate (review fix from whole-project audit)
//
// The StationForm UI handles only v1 strategies (pick/window) + the
// row_template layout form. isAdvancedTemplate flags templates that
// would be silently truncated on save via buildTemplate(). Sub-slice
// #16 (template-JSON import) lands users on the Edit route for
// advanced templates; the guard blocks the lossy form path.

import { isAdvancedTemplate } from './template';


describe('isAdvancedTemplate', () => {
  const v1Template = {
    station: 'cls',
    cadence: 'weekly',
    rng: 'off',
    sources: [{ name: 'A', colour: '#111', role: 'content' }],
    cursor: { base_advance_per_week: 60, cycle_weeks: 4 },
    layout: {
      unit: 'day',
      unit_labels: ['Mon'],
      row_start_offsets: [0],
      row_template: [{ source: 'A', strategy: 'pick', size: 1 }],
    },
  };

  it('returns false for null/undefined/empty template', () => {
    expect(isAdvancedTemplate(null)).toBe(false);
    expect(isAdvancedTemplate(undefined)).toBe(false);
    expect(isAdvancedTemplate({})).toBe(false);
  });

  it('returns false for a v1 Classics-shaped template', () => {
    expect(isAdvancedTemplate(v1Template)).toBe(false);
  });

  it('flags a template with flat_slots set', () => {
    const tpl = {
      ...v1Template,
      layout: {
        ...v1Template.layout,
        flat_slots: [{ source: 'A', strategy: 'jingle', size: 1, genre: 'D' }],
      },
    };
    expect(isAdvancedTemplate(tpl)).toBe(true);
  });

  it('does not flag an empty flat_slots array (XOR no-op)', () => {
    const tpl = {
      ...v1Template,
      layout: { ...v1Template.layout, flat_slots: [] },
    };
    expect(isAdvancedTemplate(tpl)).toBe(false);
  });

  it('flags a template with cursor.persist=true', () => {
    const tpl = {
      ...v1Template,
      cursor: { ...v1Template.cursor, persist: true },
    };
    expect(isAdvancedTemplate(tpl)).toBe(true);
  });

  it('does NOT flag cursor.persist=false (default)', () => {
    const tpl = {
      ...v1Template,
      cursor: { ...v1Template.cursor, persist: false },
    };
    expect(isAdvancedTemplate(tpl)).toBe(false);
  });

  it('flags a template with non-empty cursors_init', () => {
    const tpl = {
      ...v1Template,
      layout: { ...v1Template.layout, cursors_init: { NIGHT_1: 0 } },
    };
    expect(isAdvancedTemplate(tpl)).toBe(true);
  });

  it('flags a row_template block with a non-v1 strategy', () => {
    const tpl = {
      ...v1Template,
      layout: {
        ...v1Template.layout,
        row_template: [{ source: 'A', strategy: 'weighted', size: 1 }],
      },
    };
    expect(isAdvancedTemplate(tpl)).toBe(true);
  });

  it.each([
    'cursor_id', 'genre', 'exclusion_window', 'window', 'position', 'fallback_genre',
  ])('flags a row_template block carrying an advanced field: %s', (field) => {
    const tpl = {
      ...v1Template,
      layout: {
        ...v1Template.layout,
        row_template: [{ source: 'A', strategy: 'pick', size: 1, [field]: 'x' }],
      },
    };
    expect(isAdvancedTemplate(tpl)).toBe(true);
  });
});
