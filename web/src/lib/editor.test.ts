import { describe, it, expect, beforeEach } from 'vitest';
import {
  ALL_STRATEGIES,
  DEFAULT_VIEW_MODE,
  EditorMetadataError,
  REFERENCE_SAMPLING_STRATEGIES,
  UNMAPPED_BUCKET_KEY,
  aggregateBySection,
  availableCursorIds,
  chipColour,
  classifySlots,
  summarizeClassifications,
  compactStrategyLabel,
  compatibleSources,
  compatibleStrategies,
  editableFieldsForBlock,
  expandSection,
  findChipRef,
  lookupEngineBlock,
  lookupMacro,
  parseEditorSidecarResponse,
  readViewMode,
  refKey,
  sourceColour,
  validateEditableField,
  verboseStrategyLabel,
  writeViewMode,
  type EditableFieldSpec,
  type EngineBlock,
  type ExpandedRef,
  type Sidecar,
  type SidecarMacro,
  type SidecarSection,
  type SlotClassificationKind,
  type StationLayout,
  type StationTemplate,
} from './editor';

/**
 * Pin the TypeScript-side mirrors of `engine/editor_metadata.py`.
 * The pure-function helpers are the contract the c3 canvas
 * components consume; tests catch drift between Python +
 * TypeScript implementations.
 */


function makeSidecar(over: Partial<Sidecar> = {}): Sidecar {
  return {
    schema_version: 1,
    sections: [],
    macros: [],
    layout_order: [],
    ...over,
  };
}


// ---- lookupMacro -------------------------------------------------------


describe('lookupMacro', () => {
  it('returns the macro when its id matches', () => {
    const sc = makeSidecar({
      macros: [{ id: 'jingles', repeat: 3, spec: { source: 'A', strategy: 'pick', size: 1 } }],
    });
    expect(lookupMacro(sc, 'jingles')).toEqual({
      id: 'jingles',
      repeat: 3,
      spec: { source: 'A', strategy: 'pick', size: 1 },
    });
  });

  it('returns null on unknown id', () => {
    const sc = makeSidecar({ macros: [{ id: 'a', repeat: 1, spec: { source: 'A', strategy: 'pick', size: 1 } }] });
    expect(lookupMacro(sc, 'missing')).toBeNull();
  });

  it('returns null on empty macros', () => {
    expect(lookupMacro(makeSidecar(), 'x')).toBeNull();
  });

  it('handles a sidecar with macros field omitted entirely', () => {
    // Mid-slice review 🟡-3: top-level `macros` is OPTIONAL per
    // Python REQUIRED_TOP_LEVEL. The helper uses `?? []` defensively
    // so an omitted-macros sidecar doesn't crash.
    const sc: Sidecar = {
      schema_version: 1,
      sections: [],
      layout_order: [],
    };
    expect(lookupMacro(sc, 'anything')).toBeNull();
  });
});


// ---- expandSection -----------------------------------------------------


describe('expandSection — atomic blocks', () => {
  it('emits one AtomicRef per flat_slots block', () => {
    const section: SidecarSection = {
      id: 's',
      name: 'S',
      blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'atomic', slot_index: 1 },
        { kind: 'atomic', slot_index: 5 },
      ],
    };
    const out = expandSection(section, makeSidecar());
    expect(out).toEqual([
      { kind: 'atomic-ref', slot_index: 0 },
      { kind: 'atomic-ref', slot_index: 1 },
      { kind: 'atomic-ref', slot_index: 5 },
    ]);
  });

  it('emits one AtomicRef per row_template block (row_idx + block_idx)', () => {
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [
        { kind: 'atomic', row_idx: 0, block_idx: 0 },
        { kind: 'atomic', row_idx: 0, block_idx: 1 },
        { kind: 'atomic', row_idx: 1, block_idx: 0 },
      ],
    };
    const out = expandSection(section, makeSidecar());
    expect(out).toEqual([
      { kind: 'atomic-ref', row_idx: 0, block_idx: 0 },
      { kind: 'atomic-ref', row_idx: 0, block_idx: 1 },
      { kind: 'atomic-ref', row_idx: 1, block_idx: 0 },
    ]);
  });
});


describe('expandSection — macros', () => {
  it('emits `repeat` MacroPlaceholders for a macro block', () => {
    const sc = makeSidecar({
      macros: [{ id: 'jingles', repeat: 4, spec: { source: 'A', strategy: 'pick', size: 1 } }],
    });
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [{ kind: 'macro', ref: 'jingles' }],
    };
    const out = expandSection(section, sc);
    expect(out).toEqual([
      { kind: 'macro-placeholder', macro_id: 'jingles', pick_in_macro: 1 },
      { kind: 'macro-placeholder', macro_id: 'jingles', pick_in_macro: 2 },
      { kind: 'macro-placeholder', macro_id: 'jingles', pick_in_macro: 3 },
      { kind: 'macro-placeholder', macro_id: 'jingles', pick_in_macro: 4 },
    ]);
  });

  it('throws EditorMetadataError on a dangling macro ref', () => {
    const sc = makeSidecar({ macros: [{ id: 'real', repeat: 1, spec: { source: 'A', strategy: 'pick', size: 1 } }] });
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [{ kind: 'macro', ref: 'ghost' }],
    };
    expect(() => expandSection(section, sc)).toThrow(EditorMetadataError);
    expect(() => expandSection(section, sc)).toThrow(/ghost/);
  });

  it('interleaves atomic refs + macro placeholders in render order', () => {
    const sc = makeSidecar({
      macros: [{ id: 'm', repeat: 2, spec: { source: 'A', strategy: 'pick', size: 1 } }],
    });
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'macro', ref: 'm' },
        { kind: 'atomic', slot_index: 1 },
      ],
    };
    const out = expandSection(section, sc);
    // Atomic, then 2 macro placeholders, then atomic — flat list.
    expect(out.map((r) => r.kind)).toEqual([
      'atomic-ref',
      'macro-placeholder',
      'macro-placeholder',
      'atomic-ref',
    ]);
  });
});


describe('expandSection — repeat_with_offsets', () => {
  it('instantiates the per-row template once per offset', () => {
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [
        { kind: 'atomic', row_idx: 0, block_idx: 0 },
        { kind: 'atomic', row_idx: 0, block_idx: 1 },
      ],
      repeat_with_offsets: [0, 100, 200], // 3 offsets
    };
    const out = expandSection(section, makeSidecar());
    // 2 blocks × 3 offsets = 6 refs. row_idx overridden to the
    // POSITION of the offset, not its value.
    expect(out).toEqual([
      { kind: 'atomic-ref', row_idx: 0, block_idx: 0 },
      { kind: 'atomic-ref', row_idx: 0, block_idx: 1 },
      { kind: 'atomic-ref', row_idx: 1, block_idx: 0 },
      { kind: 'atomic-ref', row_idx: 1, block_idx: 1 },
      { kind: 'atomic-ref', row_idx: 2, block_idx: 0 },
      { kind: 'atomic-ref', row_idx: 2, block_idx: 1 },
    ]);
  });

  it('flat_slots block_index inside repeat_with_offsets: slot_index passes through', () => {
    // Edge case — repeat_with_offsets on a flat_slots section.
    // Each iteration emits the same slot_index (no override field
    // for flat_slots). Useful when a section is "the same single
    // block, N times" — mirrors the Python helper's behaviour.
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [{ kind: 'atomic', slot_index: 42 }],
      repeat_with_offsets: [0, 10],
    };
    const out = expandSection(section, makeSidecar());
    expect(out).toEqual([
      { kind: 'atomic-ref', slot_index: 42 },
      { kind: 'atomic-ref', slot_index: 42 },
    ]);
  });

  it('expands macros inside a repeat_with_offsets row', () => {
    const sc = makeSidecar({
      macros: [{ id: 'm', repeat: 2, spec: { source: 'A', strategy: 'pick', size: 1 } }],
    });
    const section: SidecarSection = {
      id: 's', name: 'S',
      blocks: [{ kind: 'macro', ref: 'm' }],
      repeat_with_offsets: [0, 100],
    };
    const out = expandSection(section, sc);
    expect(out.length).toBe(4); // 2 repeats × 2 offsets
    expect(out.every((r) => r.kind === 'macro-placeholder')).toBe(true);
  });
});


// ---- lookupEngineBlock -------------------------------------------------


describe('lookupEngineBlock', () => {
  const flatLayout: StationLayout = {
    unit: 'slot', unit_labels: [], row_start_offsets: [],
    flat_slots: [
      { source: 'A', strategy: 'pick', size: 1 },
      { source: 'B', strategy: 'sequential', size: 2, cursor_id: 'C' },
    ],
  };

  const rowLayout: StationLayout = {
    unit: 'day', unit_labels: ['Mon', 'Tue'], row_start_offsets: [0, 10],
    row_template: [
      { source: 'A', strategy: 'window', size: 5 },
      { source: 'B', strategy: 'weighted', size: 3, window: 10 },
    ],
  };

  it('resolves slot_index against flat_slots', () => {
    const b = lookupEngineBlock(
      { kind: 'atomic-ref', slot_index: 1 }, flatLayout,
    );
    expect(b?.source).toBe('B');
    expect(b?.strategy).toBe('sequential');
  });

  it('resolves (row_idx, block_idx) against row_template', () => {
    // row_idx is the ROW (used by the engine for offsets); block_idx
    // selects which BLOCK from the shared row_template.
    const b = lookupEngineBlock(
      { kind: 'atomic-ref', row_idx: 1, block_idx: 1 }, rowLayout,
    );
    expect(b?.source).toBe('B');
    expect(b?.strategy).toBe('weighted');
  });

  it('returns null on out-of-bounds slot_index', () => {
    expect(
      lookupEngineBlock({ kind: 'atomic-ref', slot_index: 99 }, flatLayout),
    ).toBeNull();
  });

  it('returns null on out-of-bounds block_idx', () => {
    expect(
      lookupEngineBlock(
        { kind: 'atomic-ref', row_idx: 0, block_idx: 99 }, rowLayout,
      ),
    ).toBeNull();
  });
});


// ---- compactStrategyLabel ----------------------------------------------


describe('compactStrategyLabel', () => {
  const cases: Array<[EngineBlock, string]> = [
    [{ source: 'A', strategy: 'pick', size: 1 }, 'Pk'],
    [{ source: 'A', strategy: 'window', size: 5 }, 'Wnd'],
    [{ source: 'A', strategy: 'weighted', size: 3 }, 'W'],
    [
      { source: 'A', strategy: 'sequential', size: 1, cursor_id: 'NIGHT_1' },
      'S:NIGHT_1',
    ],
    [
      { source: 'A', strategy: 'jingle', size: 1, genre: 'Day' },
      'J:Day',
    ],
    [
      { source: 'A', strategy: 'mix', size: 1, genre: '01Alphastate' },
      'M:01Alphastate',
    ],
    [
      { source: 'A', strategy: 'prev_ref', size: 1, position: 1742 },
      'P:1742',
    ],
  ];

  for (const [block, expected] of cases) {
    it(`abbreviates ${block.strategy} → ${expected}`, () => {
      expect(compactStrategyLabel(block)).toBe(expected);
    });
  }

  it('falls back to prefix-only when sequential lacks cursor_id', () => {
    expect(
      compactStrategyLabel({ source: 'A', strategy: 'sequential', size: 1 }),
    ).toBe('S');
  });

  it('falls back to prefix-only when jingle lacks genre', () => {
    expect(
      compactStrategyLabel({ source: 'A', strategy: 'jingle', size: 1 }),
    ).toBe('J');
  });

  it('falls back to prefix-only when prev_ref lacks position', () => {
    expect(
      compactStrategyLabel({ source: 'A', strategy: 'prev_ref', size: 1 }),
    ).toBe('P');
  });
});


// ---- verboseStrategyLabel ----------------------------------------------


describe('verboseStrategyLabel', () => {
  it('always includes strategy + source + size', () => {
    const out = verboseStrategyLabel({ source: 'A', strategy: 'pick', size: 5 });
    expect(out).toContain('strategy: pick');
    expect(out).toContain('source: A');
    expect(out).toContain('size: 5');
  });

  it('includes the strategy-specific fields when present', () => {
    const out = verboseStrategyLabel({
      source: 'B', strategy: 'sequential', size: 2, cursor_id: 'NIGHT_1',
    });
    expect(out).toContain('cursor_id: NIGHT_1');
  });

  it('omits fields that are undefined', () => {
    const out = verboseStrategyLabel({ source: 'A', strategy: 'pick', size: 1 });
    expect(out).not.toMatch(
      /cursor_id|window|exclusion_window|genre|position|fallback_genre/,
    );
  });

  it('surfaces fallback_genre on prev_ref blocks (mid-slice review 🟡-4)', () => {
    const out = verboseStrategyLabel({
      source: 'A', strategy: 'prev_ref', size: 1,
      position: 100, fallback_genre: 'Day',
    });
    expect(out).toContain('fallback_genre: Day');
  });

  it('formats with newline separators (multi-line tooltip-friendly)', () => {
    const out = verboseStrategyLabel({ source: 'A', strategy: 'pick', size: 1 });
    expect(out.split('\n').length).toBe(3);
  });
});


// ---- §16.2.2 c4 view-mode helpers --------------------------------------


function tinyTemplate(): StationTemplate {
  return {
    station: 'ts', name: 'Test',
    sources: [
      { name: 'A', colour: '#c0392b', role: 'content' },
      { name: 'B', role: 'content' }, // no colour declared
    ],
    layout: { unit: 'slot', unit_labels: [], row_start_offsets: [], flat_slots: [] },
  };
}


describe('sourceColour', () => {
  it('returns the declared colour for a known source', () => {
    expect(sourceColour(tinyTemplate(), 'A')).toBe('#c0392b');
  });

  it('returns the fallback when source has no colour declared', () => {
    expect(sourceColour(tinyTemplate(), 'B')).toBe('#ccc');
  });

  it('returns the fallback for an unknown source name', () => {
    expect(sourceColour(tinyTemplate(), 'ghost')).toBe('#ccc');
  });
});


describe('chipColour', () => {
  const block: EngineBlock = { source: 'A', strategy: 'pick', size: 1 };

  it('colorBy=source uses the source colour', () => {
    expect(chipColour(block, tinyTemplate(), 'source')).toBe('#c0392b');
  });

  it('colorBy=strategy uses the strategy palette', () => {
    // pick → palette entry (matches the helper's STRATEGY_COLOURS).
    expect(chipColour(block, tinyTemplate(), 'strategy')).toMatch(/^#/);
    // Different strategies get different colours.
    const window: EngineBlock = { source: 'A', strategy: 'window', size: 5 };
    expect(chipColour(block, tinyTemplate(), 'strategy')).not.toBe(
      chipColour(window, tinyTemplate(), 'strategy'),
    );
  });
});


// ---- parseEditorSidecarResponse (§16.2.2.t1 runtime validation) -------


describe('parseEditorSidecarResponse', () => {
  function validResponse(): {
    template: Record<string, unknown>;
    sidecar: Record<string, unknown>;
    sidecar_error: string | null;
  } {
    return {
      template: {
        station: 'cls',
        name: 'Classics',
        sources: [{ name: 'A', colour: '#abc', role: 'content' }],
        layout: { unit: 'slot', unit_labels: [], row_start_offsets: [] },
      },
      sidecar: {
        schema_version: 1,
        sections: [],
        macros: [],
        layout_order: [],
      },
      sidecar_error: null,
    };
  }

  it('returns the typed bundle on a valid response', () => {
    const out = parseEditorSidecarResponse(validResponse());
    expect(out.template.station).toBe('cls');
    expect(out.sidecar.schema_version).toBe(1);
    expect(out.sidecar_error).toBeNull();
  });

  it('accepts a populated sidecar_error string', () => {
    const r = validResponse();
    r.sidecar_error = 'something broke at line 3';
    const out = parseEditorSidecarResponse(r);
    expect(out.sidecar_error).toBe('something broke at line 3');
  });

  it.each([
    ['null root', null],
    ['undefined root', undefined],
    ['string root', 'not an object'],
    ['array root', []],
  ])('throws EditorMetadataError on %s', (_label, raw) => {
    expect(() => parseEditorSidecarResponse(raw)).toThrow(EditorMetadataError);
  });

  it('throws when template is missing', () => {
    const r = validResponse() as Record<string, unknown>;
    delete r.template;
    expect(() => parseEditorSidecarResponse(r)).toThrow(/template/);
  });

  it('throws when template.station is not a string', () => {
    const r = validResponse();
    (r.template as Record<string, unknown>).station = 42;
    expect(() => parseEditorSidecarResponse(r)).toThrow(/template\.station/);
  });

  it('throws when template.sources is not an array', () => {
    const r = validResponse();
    (r.template as Record<string, unknown>).sources = {};
    expect(() => parseEditorSidecarResponse(r)).toThrow(/template\.sources/);
  });

  it('throws when template.layout is missing', () => {
    const r = validResponse();
    delete (r.template as Record<string, unknown>).layout;
    expect(() => parseEditorSidecarResponse(r)).toThrow(/template\.layout/);
  });

  it('throws when sidecar.schema_version is not 1', () => {
    const r = validResponse();
    (r.sidecar as Record<string, unknown>).schema_version = 2;
    expect(() => parseEditorSidecarResponse(r)).toThrow(/schema_version/);
  });

  it('throws when sidecar.sections is missing', () => {
    const r = validResponse();
    delete (r.sidecar as Record<string, unknown>).sections;
    expect(() => parseEditorSidecarResponse(r)).toThrow(/sidecar\.sections/);
  });

  it('throws when sidecar.layout_order is not an array', () => {
    const r = validResponse();
    (r.sidecar as Record<string, unknown>).layout_order = 'wrong';
    expect(() => parseEditorSidecarResponse(r)).toThrow(/layout_order/);
  });

  it('throws when sidecar_error is wrong type', () => {
    const r = validResponse();
    (r as Record<string, unknown>).sidecar_error = 42;
    expect(() => parseEditorSidecarResponse(r)).toThrow(/sidecar_error/);
  });

  // ---- per-element drift (review 🟡-2 + 🟡-3) -----------------------

  it('throws when template.sources contains a non-object element', () => {
    // §16.2.2.t1 review 🟡-2: an array of non-objects passed the
    // earlier Array.isArray check; the per-element guard catches it.
    const r = validResponse();
    (r.template as Record<string, unknown>).sources = [42];
    expect(() => parseEditorSidecarResponse(r)).toThrow(
      /template\.sources\[0\]/,
    );
  });

  it('throws when template.sources[i].name is missing', () => {
    const r = validResponse();
    (r.template as Record<string, unknown>).sources = [
      { colour: '#abc', role: 'content' }, // no `name`
    ];
    expect(() => parseEditorSidecarResponse(r)).toThrow(
      /template\.sources\[0\]\.name/,
    );
  });

  it('throws when sidecar.sections contains a non-object element', () => {
    // §16.2.2.t1 review 🟡-3.
    const r = validResponse();
    (r.sidecar as Record<string, unknown>).sections = ['not-an-object'];
    expect(() => parseEditorSidecarResponse(r)).toThrow(
      /sidecar\.sections\[0\]/,
    );
  });

  it('throws when sidecar.sections[i].id is not a string', () => {
    const r = validResponse();
    (r.sidecar as Record<string, unknown>).sections = [
      { id: 42, name: 'S', blocks: [] },
    ];
    expect(() => parseEditorSidecarResponse(r)).toThrow(
      /sidecar\.sections\[0\]\.id/,
    );
  });

  it('throws when sidecar.sections[i].blocks is not an array', () => {
    const r = validResponse();
    (r.sidecar as Record<string, unknown>).sections = [
      { id: 's', name: 'S', blocks: 'wrong' },
    ];
    expect(() => parseEditorSidecarResponse(r)).toThrow(
      /sidecar\.sections\[0\]\.blocks/,
    );
  });

  it('throws when sidecar.layout_order contains a non-string', () => {
    const r = validResponse();
    (r.sidecar as Record<string, unknown>).layout_order = ['ok', 42, 'also-ok'];
    expect(() => parseEditorSidecarResponse(r)).toThrow(
      /sidecar\.layout_order\[1\]/,
    );
  });
});


describe('readViewMode / writeViewMode', () => {
  beforeEach(() => {
    if (typeof window !== 'undefined') window.localStorage.clear();
  });

  it('returns DEFAULT_VIEW_MODE when nothing is persisted', () => {
    expect(readViewMode()).toEqual(DEFAULT_VIEW_MODE);
  });

  it('round-trips through writeViewMode + readViewMode', () => {
    writeViewMode({
      labelMode: 'verbose', colorBy: 'strategy', macroVisual: 'count-badge',
    });
    expect(readViewMode()).toEqual({
      labelMode: 'verbose', colorBy: 'strategy', macroVisual: 'count-badge',
    });
  });

  it('falls back to defaults on malformed JSON', () => {
    if (typeof window === 'undefined') return; // SSR guard
    window.localStorage.setItem('station-studio.viewMode.v1', 'not-json{');
    expect(readViewMode()).toEqual(DEFAULT_VIEW_MODE);
  });

  it('fills missing fields with defaults (partial blob in storage)', () => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(
      'station-studio.viewMode.v1',
      JSON.stringify({ labelMode: 'verbose' }),
    );
    expect(readViewMode()).toEqual({
      labelMode: 'verbose',
      colorBy: DEFAULT_VIEW_MODE.colorBy,
      macroVisual: DEFAULT_VIEW_MODE.macroVisual,
    });
  });
});


// ---- §16.2.3 c1 — editable-field schema ---------------------------------


describe('editableFieldsForBlock', () => {
  it('pick + window strategies have only allowlist_sources (§16.2.4 D3)', () => {
    // §16.2.4 D3: pick + window pick up `allowlist_sources` (the
    // CONTENT_SAMPLING_STRATEGIES opt-in set). They previously had
    // NO editable params at all (§16.2.3).
    const pick = editableFieldsForBlock({ source: 'A', strategy: 'pick', size: 1 });
    expect(pick.map((f) => f.name)).toEqual(['allowlist_sources']);
    expect(pick[0]).toMatchObject({
      required: false, type: 'string_list', sourceRoleFilter: 'reference',
    });
    const win = editableFieldsForBlock({ source: 'A', strategy: 'window', size: 5 });
    expect(win.map((f) => f.name)).toEqual(['allowlist_sources']);
  });

  it('sequential → cursor_id (required, enumFromCursors) + allowlist_sources', () => {
    const fields = editableFieldsForBlock({ source: 'A', strategy: 'sequential', size: 1, cursor_id: 'C' });
    // §16.2.4 D3: sequential gains allowlist_sources too.
    expect(fields.map((f) => f.name)).toEqual(['cursor_id', 'allowlist_sources']);
    expect(fields.find((f) => f.name === 'cursor_id')).toMatchObject({
      required: true, enumFromCursors: true,
    });
    expect(fields.find((f) => f.name === 'allowlist_sources')).toMatchObject({
      type: 'string_list', required: false,
    });
  });

  it('jingle → genre (required)', () => {
    const fields = editableFieldsForBlock({ source: 'A', strategy: 'jingle', size: 1, genre: 'Day' });
    expect(fields.map((f) => f.name)).toEqual(['genre']);
    expect(fields[0].required).toBe(true);
  });

  it('mix → genre (required) + exclusion_window (optional, int)', () => {
    const fields = editableFieldsForBlock({ source: 'A', strategy: 'mix', size: 1, genre: 'M' });
    expect(fields.map((f) => f.name)).toEqual(['genre', 'exclusion_window']);
    expect(fields.find((f) => f.name === 'exclusion_window')).toMatchObject({
      required: false, type: 'int', min: 0,
    });
  });

  it('weighted → window (optional, int ≥ 0) + allowlist_sources', () => {
    const fields = editableFieldsForBlock({ source: 'A', strategy: 'weighted', size: 1 });
    // §16.2.4 D3: weighted gains allowlist_sources too.
    expect(fields.map((f) => f.name)).toEqual(['window', 'allowlist_sources']);
    expect(fields.find((f) => f.name === 'window')).toMatchObject({
      required: false, type: 'int', min: 0,
    });
    expect(fields.find((f) => f.name === 'allowlist_sources')).toMatchObject({
      type: 'string_list', required: false, sourceRoleFilter: 'reference',
    });
  });

  it('prev_ref → position (required, ≥1) + fallback_genre (optional)', () => {
    const fields = editableFieldsForBlock({ source: 'A', strategy: 'prev_ref', size: 1, position: 5 });
    expect(fields.map((f) => f.name)).toEqual(['position', 'fallback_genre']);
    expect(fields.find((f) => f.name === 'position')).toMatchObject({
      required: true, type: 'int', min: 1,
    });
    expect(fields.find((f) => f.name === 'fallback_genre')).toMatchObject({ required: false });
  });
});


describe('availableCursorIds', () => {
  function tmpl(cursorsInit?: Record<string, number>): StationTemplate {
    return {
      station: 's', name: 'S',
      sources: [{ name: 'A', role: 'content' }],
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [],
        cursors_init: cursorsInit, flat_slots: [],
      },
    };
  }
  it('returns the cursors_init keys', () => {
    expect(availableCursorIds(tmpl({ NIGHT_1: 0, DAY: 10 }))).toEqual(['NIGHT_1', 'DAY']);
  });
  it('returns [] when cursors_init is absent', () => {
    expect(availableCursorIds(tmpl(undefined))).toEqual([]);
  });
});


describe('validateEditableField', () => {
  const intReq = { name: 'position' as const, label: 'Position', type: 'int' as const, required: true, min: 1 };
  const intOpt = { name: 'window' as const, label: 'Window', type: 'int' as const, required: false, min: 0 };
  const strReq = { name: 'genre' as const, label: 'Genre', type: 'string' as const, required: true };
  const cursor = { name: 'cursor_id' as const, label: 'Cursor', type: 'string' as const, required: true, enumFromCursors: true };

  it('required field rejects empty', () => {
    expect(validateEditableField(intReq, undefined)).toMatch(/required/);
    expect(validateEditableField(strReq, '')).toMatch(/required/);
  });

  it('optional field accepts empty (null/clear)', () => {
    expect(validateEditableField(intOpt, undefined)).toBeNull();
    expect(validateEditableField(intOpt, '')).toBeNull();
  });

  it('int field rejects non-integers + below min', () => {
    expect(validateEditableField(intReq, 1.5)).toMatch(/whole number/);
    expect(validateEditableField(intReq, 0)).toMatch(/≥ 1/);
    expect(validateEditableField(intOpt, -1)).toMatch(/≥ 0/);
  });

  it('int field accepts valid', () => {
    expect(validateEditableField(intReq, 1)).toBeNull();
    expect(validateEditableField(intOpt, 0)).toBeNull();
  });

  it('cursor field must be one of cursors_init (enum check)', () => {
    const template: StationTemplate = {
      station: 's', name: 'S', sources: [],
      layout: { unit: 'slot', unit_labels: [], row_start_offsets: [], cursors_init: { C: 0 } },
    };
    expect(validateEditableField(cursor, 'C', template)).toBeNull();
    expect(validateEditableField(cursor, 'NOPE', template)).toMatch(/must be one of/);
  });
});


// ---- §16.2.4 c2 — role-aware bidirectional dropdowns (D4) ----------


describe('compatibleStrategies', () => {
  it('content role gets every strategy EXCEPT archive_ref', () => {
    const result = compatibleStrategies('content');
    expect(result).toContain('pick');
    expect(result).toContain('weighted');
    expect(result).toContain('jingle');
    expect(result).toContain('prev_ref');
    expect(result).not.toContain('archive_ref');
  });

  it('reference role gets ONLY archive_ref (today)', () => {
    const result = compatibleStrategies('reference');
    expect([...result]).toEqual(['archive_ref']);
  });

  it('covers every supported strategy across both roles', () => {
    // The union must equal ALL_STRATEGIES — no strategy should be
    // unreachable from any role. Pins the invariant when new
    // strategies join the engine.
    const union = new Set([
      ...compatibleStrategies('content'),
      ...compatibleStrategies('reference'),
    ]);
    expect(union).toEqual(new Set(ALL_STRATEGIES));
  });
});


describe('compatibleSources', () => {
  const sources: StationTemplate['sources'] = [
    { name: 'A', role: 'content', colour: '#abc' },
    { name: 'B', role: 'content', colour: '#def' },
    { name: 'REF', role: 'reference', colour: '#fff' },
  ];

  it('content-sampling strategies → content sources only', () => {
    expect(compatibleSources('pick', sources).map((s) => s.name)).toEqual(['A', 'B']);
    expect(compatibleSources('weighted', sources).map((s) => s.name)).toEqual(['A', 'B']);
    expect(compatibleSources('jingle', sources).map((s) => s.name)).toEqual(['A', 'B']);
  });

  it('archive_ref → reference sources only', () => {
    expect(compatibleSources('archive_ref', sources).map((s) => s.name)).toEqual(['REF']);
  });

  it('preserves declaration order (operator intent)', () => {
    const reordered: StationTemplate['sources'] = [
      { name: 'Z', role: 'content', colour: '#000' },
      { name: 'A', role: 'content', colour: '#fff' },
    ];
    expect(compatibleSources('pick', reordered).map((s) => s.name)).toEqual(['Z', 'A']);
  });
});


describe('REFERENCE_SAMPLING_STRATEGIES + ALL_STRATEGIES', () => {
  it('REFERENCE_SAMPLING_STRATEGIES is a subset of ALL_STRATEGIES', () => {
    for (const s of REFERENCE_SAMPLING_STRATEGIES) {
      expect(ALL_STRATEGIES).toContain(s);
    }
  });

  it('ALL_STRATEGIES covers exactly the EngineBlock["strategy"] union', () => {
    expect(new Set(ALL_STRATEGIES)).toEqual(new Set([
      'pick', 'window', 'sequential', 'jingle', 'mix', 'weighted',
      'prev_ref', 'archive_ref',
    ]));
  });
});


// ---- c2 review #3/#4: validateEditableField string_list branch ------


describe('validateEditableField — string_list', () => {
  it('null + empty array are valid for an OPTIONAL string_list', () => {
    const spec: EditableFieldSpec = {
      name: 'allowlist_sources', label: 'Allowlist',
      type: 'string_list', required: false,
    };
    expect(validateEditableField(spec, [], undefined)).toBeNull();
    expect(validateEditableField(spec, undefined, undefined)).toBeNull();
  });

  it('empty array fails a REQUIRED string_list with "is required"', () => {
    // No required string_list field exists today; this pins the
    // contract so a future required string_list (e.g. denylist_sources)
    // wires through without a special case at the call sites.
    const spec: EditableFieldSpec = {
      name: 'allowlist_sources', label: 'Allowlist',
      type: 'string_list', required: true,
    };
    expect(validateEditableField(spec, [], undefined)).toMatch(/is required/);
    expect(validateEditableField(spec, undefined, undefined)).toMatch(/is required/);
  });

  it('non-empty array passes a REQUIRED string_list', () => {
    const spec: EditableFieldSpec = {
      name: 'allowlist_sources', label: 'Allowlist',
      type: 'string_list', required: true,
    };
    expect(validateEditableField(spec, ['A'], undefined)).toBeNull();
    expect(validateEditableField(spec, ['A', 'B'], undefined)).toBeNull();
  });

  it('rejects an array passed to a non-list spec with a clear error', () => {
    // Catches a bug class where editValues for a string field
    // accidentally holds an array (e.g. a future component swap).
    const spec: EditableFieldSpec = {
      name: 'genre', label: 'Genre',
      type: 'string', required: true,
    };
    expect(validateEditableField(spec, ['Day'], undefined)).toMatch(/single value/);
  });
});


// ---- §16.2.6.t3 c1 — refKey + findChipRef -------------------------------


describe('refKey', () => {
  it('serializes a flat_slots AtomicRef as atomic:slot=N', () => {
    const ref: ExpandedRef = { kind: 'atomic-ref', slot_index: 7 };
    expect(refKey(ref)).toBe('atomic:slot=7');
  });

  it('serializes a row_template AtomicRef as atomic:row=R:block=B', () => {
    const ref: ExpandedRef = { kind: 'atomic-ref', row_idx: 2, block_idx: 5 };
    expect(refKey(ref)).toBe('atomic:row=2:block=5');
  });

  it('serializes a MacroPlaceholder as macro:ID:pick=K', () => {
    const ref: ExpandedRef = {
      kind: 'macro-placeholder', macro_id: 'jingles', pick_in_macro: 3,
    };
    expect(refKey(ref)).toBe('macro:jingles:pick=3');
  });

  it('is round-trip distinguishable across atomic flat / atomic row / macro', () => {
    // The query selector relies on keys being injective across the
    // three ref kinds. A drift where a row+block atomic collided with
    // a slot atomic (e.g. due to a sloppy formatter swap) would break
    // chip-jump silently.
    const keys = [
      refKey({ kind: 'atomic-ref', slot_index: 0 }),
      refKey({ kind: 'atomic-ref', row_idx: 0, block_idx: 0 }),
      refKey({ kind: 'macro-placeholder', macro_id: 'm', pick_in_macro: 1 }),
    ];
    expect(new Set(keys).size).toBe(3);
  });
});


describe('findChipRef', () => {
  function flatTemplate(): StationTemplate {
    return {
      station: 'ts', name: 'Test',
      sources: [{ name: 'A', role: 'content' }],
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [],
        flat_slots: [
          { source: 'A', strategy: 'sequential', size: 1, cursor_id: 'C' },
          { source: 'A', strategy: 'sequential', size: 1, cursor_id: 'C' },
          { source: 'A', strategy: 'sequential', size: 1, cursor_id: 'C' },
          { source: 'A', strategy: 'sequential', size: 1, cursor_id: 'C' },
          { source: 'A', strategy: 'sequential', size: 1, cursor_id: 'C' },
        ],
      },
    };
  }

  function rowTemplate(): StationTemplate {
    return {
      station: 'rt', name: 'RowTest',
      sources: [{ name: 'A', role: 'content' }],
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [0, 10],
        row_template: [
          { source: 'A', strategy: 'pick', size: 1 },
          { source: 'A', strategy: 'pick', size: 1 },
          { source: 'A', strategy: 'pick', size: 1 },
        ],
      },
    };
  }

  it('case 1: flat_slots atomic — direct slot_index match', () => {
    const sc: Sidecar = {
      schema_version: 1,
      sections: [{
        id: 'morning', blocks: [
          { kind: 'atomic', slot_index: 0 },
          { kind: 'atomic', slot_index: 1 },
          { kind: 'atomic', slot_index: 2 },
        ],
      }],
      layout_order: ['morning'],
    };
    expect(findChipRef(flatTemplate(), sc, { slot: 1, row: 0, block: 1 }))
      .toEqual({ kind: 'atomic-ref', slot_index: 1 });
  });

  it('case 2: row_template atomic — match (row_idx, block_idx)', () => {
    const sc: Sidecar = {
      schema_version: 1,
      sections: [{
        id: 'row', blocks: [
          { kind: 'atomic', block_idx: 0 },
          { kind: 'atomic', block_idx: 1 },
          { kind: 'atomic', block_idx: 2 },
        ],
        repeat_with_offsets: [0, 10],
      }],
      layout_order: ['row'],
    };
    expect(findChipRef(rowTemplate(), sc, { slot: 11, row: 1, block: 2 }))
      .toEqual({ kind: 'atomic-ref', row_idx: 1, block_idx: 2 });
  });

  it('case 3: flat_slots inside macro — returns the macro placeholder (pick=1)', () => {
    // Section: atomic@0, macro(repeat=3)@[1,2,3], atomic@4.
    // Target slot 2 → inside the macro.
    const sc: Sidecar = {
      schema_version: 1,
      sections: [{
        id: 'mixed', blocks: [
          { kind: 'atomic', slot_index: 0 },
          { kind: 'macro', ref: 'jingles' },
          { kind: 'atomic', slot_index: 4 },
        ],
      }],
      macros: [{
        id: 'jingles', repeat: 3,
        spec: { source: 'A', strategy: 'jingle', size: 1, genre: 'Day' },
      }],
      layout_order: ['mixed'],
    };
    expect(findChipRef(flatTemplate(), sc, { slot: 2, row: 0, block: 2 }))
      .toEqual({ kind: 'macro-placeholder', macro_id: 'jingles', pick_in_macro: 1 });
  });

  it('case 3b: macro before first atomic — section_start computed from anchor', () => {
    // Section: macro(repeat=2), atomic@2, atomic@3.
    // section_start = 2 - 2 = 0. Macro spans slots [0,1].
    // Target slot 1 → inside the macro.
    const sc: Sidecar = {
      schema_version: 1,
      sections: [{
        id: 'leading-macro', blocks: [
          { kind: 'macro', ref: 'm' },
          { kind: 'atomic', slot_index: 2 },
          { kind: 'atomic', slot_index: 3 },
        ],
      }],
      macros: [{
        id: 'm', repeat: 2,
        spec: { source: 'A', strategy: 'pick', size: 1 },
      }],
      layout_order: ['leading-macro'],
    };
    expect(findChipRef(flatTemplate(), sc, { slot: 1, row: 0, block: 1 }))
      .toEqual({ kind: 'macro-placeholder', macro_id: 'm', pick_in_macro: 1 });
  });

  it('case 4: row_template inside macro — returns the macro placeholder', () => {
    // Section blocks: atomic@block_idx=0, macro(repeat=2), atomic@3.
    // Target block 1 → inside the macro span [1, 2].
    const sc: Sidecar = {
      schema_version: 1,
      sections: [{
        id: 'row-mixed', blocks: [
          { kind: 'atomic', block_idx: 0 },
          { kind: 'macro', ref: 'm' },
          { kind: 'atomic', block_idx: 3 },
        ],
        repeat_with_offsets: [0],
      }],
      macros: [{
        id: 'm', repeat: 2,
        spec: { source: 'A', strategy: 'pick', size: 1 },
      }],
      layout_order: ['row-mixed'],
    };
    expect(findChipRef(rowTemplate(), sc, { slot: 2, row: 0, block: 2 }))
      .toEqual({ kind: 'macro-placeholder', macro_id: 'm', pick_in_macro: 1 });
  });

  it('case 5: out-of-canvas slot — returns null (D6 drift fallback)', () => {
    // Sidecar only has slot 0; trace asks for slot 4. No match.
    const sc: Sidecar = {
      schema_version: 1,
      sections: [{
        id: 'tiny', blocks: [{ kind: 'atomic', slot_index: 0 }],
      }],
      layout_order: ['tiny'],
    };
    expect(findChipRef(flatTemplate(), sc, { slot: 4, row: 0, block: 4 }))
      .toBeNull();
  });

  it('case 6: section with no atomic anchor — skipped silently', () => {
    // All-macro section can't anchor section_start; skip it. Target
    // matches the next section's atomic.
    const sc: Sidecar = {
      schema_version: 1,
      sections: [
        { id: 'orphan', blocks: [{ kind: 'macro', ref: 'm' }] },
        { id: 'real', blocks: [{ kind: 'atomic', slot_index: 2 }] },
      ],
      macros: [{
        id: 'm', repeat: 1,
        spec: { source: 'A', strategy: 'pick', size: 1 },
      }],
      layout_order: ['orphan', 'real'],
    };
    expect(findChipRef(flatTemplate(), sc, { slot: 2, row: 0, block: 2 }))
      .toEqual({ kind: 'atomic-ref', slot_index: 2 });
  });

  it('case 7: negative section_start — skipped (would be MacroResolutionError on server)', () => {
    // macro(repeat=5) before atomic@2 → section_start = 2 - 5 = -3.
    // Engine would raise; lookup skips the section.
    const sc: Sidecar = {
      schema_version: 1,
      sections: [{
        id: 'drift', blocks: [
          { kind: 'macro', ref: 'm' },
          { kind: 'atomic', slot_index: 2 },
        ],
      }],
      macros: [{
        id: 'm', repeat: 5,
        spec: { source: 'A', strategy: 'pick', size: 1 },
      }],
      layout_order: ['drift'],
    };
    expect(findChipRef(flatTemplate(), sc, { slot: 0, row: 0, block: 0 }))
      .toBeNull();
  });
});


// ---- §16.2.7 c1 — classifySlots (compare-against-saved) -----------------


describe('classifySlots', () => {
  // Helper: build a minimal trace-shaped object with just the
  // track_id field the classifier reads.
  function tr(track_ids: Array<number | null>) {
    return { slots: track_ids.map((track_id) => ({ track_id })) };
  }

  it('identical traces → all slots unchanged', () => {
    const a = tr([10, 20, 30]);
    const b = tr([10, 20, 30]);
    const result = classifySlots(a, b);
    expect(result.length).toBe(3);
    expect(result.every((r) => r.kind === 'unchanged')).toBe(true);
    expect(result[1]).toEqual({
      index: 1, kind: 'unchanged', savedTrackId: 20, stagedTrackId: 20,
    });
  });

  it('one mid-slot changed → emits exactly one CHANGED + UNCHANGED neighbours', () => {
    const saved = tr([10, 20, 30]);
    const staged = tr([10, 99, 30]);
    const result = classifySlots(saved, staged);
    expect(result.map((r) => r.kind)).toEqual([
      'unchanged', 'changed', 'unchanged',
    ]);
    expect(result[1]).toEqual({
      index: 1, kind: 'changed', savedTrackId: 20, stagedTrackId: 99,
    });
  });

  it('staged longer than saved → trailing slots are ADDED', () => {
    const saved = tr([10, 20]);
    const staged = tr([10, 20, 30, 40]);
    const result = classifySlots(saved, staged);
    expect(result.map((r) => r.kind)).toEqual([
      'unchanged', 'unchanged', 'added', 'added',
    ]);
    // ADDED entries carry savedTrackId=null (saved has nothing there)
    // + the stagedTrackId so the c2 renderer can show the new content.
    expect(result[2]).toEqual({
      index: 2, kind: 'added', savedTrackId: null, stagedTrackId: 30,
    });
  });

  it('saved longer than staged → trailing slots are REMOVED', () => {
    const saved = tr([10, 20, 30, 40]);
    const staged = tr([10, 20]);
    const result = classifySlots(saved, staged);
    expect(result.map((r) => r.kind)).toEqual([
      'unchanged', 'unchanged', 'removed', 'removed',
    ]);
    // REMOVED entries carry stagedTrackId=null + the savedTrackId
    // so the c2 renderer shows what's about to disappear.
    expect(result[3]).toEqual({
      index: 3, kind: 'removed', savedTrackId: 40, stagedTrackId: null,
    });
  });

  it('mixed: changed in middle + added at end + removed handling — discriminator is track_id', () => {
    // Cover the full kind matrix in one walk: slot 0 same id;
    // slot 1 different id (changed); slots 2-3 added (staged longer).
    const saved = tr([1, 2]);
    const staged = tr([1, 99, 3, 4]);
    const kinds: SlotClassificationKind[] =
      classifySlots(saved, staged).map((r) => r.kind);
    expect(kinds).toEqual(['unchanged', 'changed', 'added', 'added']);
  });

  it('both track_ids null at same index → UNCHANGED (synthetic / dry-run trace contract)', () => {
    // Engine reserves track_id null for synthetic / dry-run generations.
    // Two synthetic traces with parallel nulls should classify as
    // unchanged, not changed.
    const saved = tr([null]);
    const staged = tr([null]);
    expect(classifySlots(saved, staged)).toEqual([
      { index: 0, kind: 'unchanged', savedTrackId: null, stagedTrackId: null },
    ]);
  });

  it('one null + one not null at same index → CHANGED (transition is observable)', () => {
    // A real track replaced by a synthetic null (or vice versa)
    // should surface as CHANGED so the operator notices the
    // unexpected swap.
    const saved = tr([10]);
    const staged = tr([null]);
    const result = classifySlots(saved, staged);
    expect(result[0].kind).toBe('changed');
  });

  it('null saved trace → all staged slots classify as ADDED', () => {
    // Defensive: saved trace hasn't been fetched yet (cache empty).
    // The c2 renderer would normally hide the diff in this state,
    // but classifySlots stays callable without throwing.
    const result = classifySlots(null, tr([1, 2, 3]));
    expect(result.map((r) => r.kind)).toEqual(['added', 'added', 'added']);
  });

  it('null staged trace → all saved slots classify as REMOVED', () => {
    const result = classifySlots(tr([1, 2]), null);
    expect(result.map((r) => r.kind)).toEqual(['removed', 'removed']);
  });

  it('both null → empty array', () => {
    expect(classifySlots(null, null)).toEqual([]);
  });

  it('both empty slots arrays → empty array', () => {
    expect(classifySlots(tr([]), tr([]))).toEqual([]);
  });
});


// ---- §16.2.7.t4 — summarizeClassifications ----------------------------


describe('summarizeClassifications', () => {
  function classifications(kinds: Array<'unchanged' | 'changed' | 'added' | 'removed'>) {
    return kinds.map((kind, index) => ({
      index, kind,
      savedTrackId: kind === 'added' ? null : index,
      stagedTrackId: kind === 'removed' ? null : index,
    }));
  }

  it('empty list → zero counts', () => {
    expect(summarizeClassifications([]))
      .toEqual({ changed: 0, added: 0, removed: 0 });
  });

  it('all unchanged → zero counts (UNCHANGED not surfaced)', () => {
    expect(summarizeClassifications(classifications([
      'unchanged', 'unchanged', 'unchanged',
    ]))).toEqual({ changed: 0, added: 0, removed: 0 });
  });

  it('mixed kinds → per-kind counts (UNCHANGED ignored)', () => {
    expect(summarizeClassifications(classifications([
      'unchanged', 'changed', 'changed', 'added', 'removed',
      'unchanged', 'changed',
    ]))).toEqual({ changed: 3, added: 1, removed: 1 });
  });

  it('matches inlined-loop semantics across all-changed / all-added / all-removed', () => {
    expect(summarizeClassifications(classifications([
      'changed', 'changed', 'changed',
    ]))).toEqual({ changed: 3, added: 0, removed: 0 });
    expect(summarizeClassifications(classifications([
      'added', 'added',
    ]))).toEqual({ changed: 0, added: 2, removed: 0 });
    expect(summarizeClassifications(classifications([
      'removed', 'removed', 'removed', 'removed',
    ]))).toEqual({ changed: 0, added: 0, removed: 4 });
  });
});


// ---- §16.2.7.t5 — aggregateBySection ---------------------------------


describe('aggregateBySection', () => {
  // Helpers: build a flat_slots station template + sidecar with N
  // sections covering disjoint slot ranges. Used to drive the
  // aggregator through findChipRef + findSectionIdForRef.
  function flatTemplate(slotCount: number): StationTemplate {
    return {
      station: 'ts', name: 'Test',
      sources: [{ name: 'A', role: 'content' }],
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [],
        flat_slots: Array.from({ length: slotCount }, () =>
          ({ source: 'A', strategy: 'sequential', size: 1, cursor_id: 'C' })),
      },
    };
  }
  // Two sections: morning = slots [0,1,2], evening = slots [3,4,5].
  function twoSectionSidecar(): Sidecar {
    return {
      schema_version: 1,
      sections: [
        { id: 'morning', name: 'Morning', blocks: [
          { kind: 'atomic', slot_index: 0 },
          { kind: 'atomic', slot_index: 1 },
          { kind: 'atomic', slot_index: 2 },
        ]},
        { id: 'evening', name: 'Evening', blocks: [
          { kind: 'atomic', slot_index: 3 },
          { kind: 'atomic', slot_index: 4 },
          { kind: 'atomic', slot_index: 5 },
        ]},
      ],
      macros: [],
      layout_order: ['morning', 'evening'],
    };
  }
  // Build a trace with the given track_ids; row/block always 1.
  function tr(track_ids: Array<number>) {
    return {
      slots: track_ids.map((track_id, i) => ({
        slot: i, row: 1, block: i, track_id,
      })),
    };
  }
  // Build classifications from a kind list (matching the indexes).
  function cls(kinds: Array<'unchanged' | 'changed' | 'added' | 'removed'>) {
    return kinds.map((kind, index) => ({
      index, kind,
      savedTrackId: kind === 'added' ? null : index,
      stagedTrackId: kind === 'removed' ? null : index,
    }));
  }

  it('all-same-section CHANGED → bucketed under that one section', () => {
    const template = flatTemplate(6);
    const sidecar = twoSectionSidecar();
    // Only slot 1 (in morning) changed.
    const classifications = cls(['unchanged', 'changed', 'unchanged',
                                 'unchanged', 'unchanged', 'unchanged']);
    const trace = tr([10, 20, 30, 40, 50, 60]);
    const result = aggregateBySection(
      classifications, trace, trace, template, sidecar, sidecar,
    );
    expect(result.get('morning'))
      .toEqual({ changed: 1, added: 0, removed: 0 });
    // evening WAS touched by unchanged slots (no-op counts), but
    // counts stay zero.
    expect(result.get('evening'))
      .toEqual({ changed: 0, added: 0, removed: 0 });
  });

  it('cross-section: CHANGED in morning + CHANGED in evening → buckets separately', () => {
    const template = flatTemplate(6);
    const sidecar = twoSectionSidecar();
    const classifications = cls(['unchanged', 'changed', 'unchanged',
                                 'changed', 'changed', 'unchanged']);
    const trace = tr([10, 20, 30, 40, 50, 60]);
    const result = aggregateBySection(
      classifications, trace, trace, template, sidecar, sidecar,
    );
    expect(result.get('morning'))
      .toEqual({ changed: 1, added: 0, removed: 0 });
    expect(result.get('evening'))
      .toEqual({ changed: 2, added: 0, removed: 0 });
  });

  it('ADDED reads from STAGED side; REMOVED reads from SAVED side', () => {
    const template = flatTemplate(6);
    const sidecar = twoSectionSidecar();
    // Index 0 = ADDED (saved side has nothing; staged side puts it
    // in morning). Index 5 = REMOVED (staged side gone; saved side
    // had it in evening).
    const classifications = cls(['added', 'unchanged', 'unchanged',
                                 'unchanged', 'unchanged', 'removed']);
    const trace = tr([10, 20, 30, 40, 50, 60]);
    const result = aggregateBySection(
      classifications, trace, trace, template, sidecar, sidecar,
    );
    expect(result.get('morning'))
      .toEqual({ changed: 0, added: 1, removed: 0 });
    expect(result.get('evening'))
      .toEqual({ changed: 0, added: 0, removed: 1 });
  });

  it('out-of-canvas slot → UNMAPPED bucket', () => {
    const template = flatTemplate(6);
    // Sidecar only has morning (slots 0,1,2); evening absent.
    const sidecar: Sidecar = {
      schema_version: 1,
      sections: [{ id: 'morning', name: 'Morning', blocks: [
        { kind: 'atomic', slot_index: 0 },
        { kind: 'atomic', slot_index: 1 },
        { kind: 'atomic', slot_index: 2 },
      ]}],
      macros: [], layout_order: ['morning'],
    };
    // Slot 4 changed → no section contains slot 4 → UNMAPPED.
    const classifications = cls(['unchanged', 'unchanged', 'unchanged',
                                 'unchanged', 'changed', 'unchanged']);
    const trace = tr([10, 20, 30, 40, 50, 60]);
    const result = aggregateBySection(
      classifications, trace, trace, template, sidecar, sidecar,
    );
    expect(result.get('morning'))
      .toEqual({ changed: 0, added: 0, removed: 0 });
    expect(result.get(UNMAPPED_BUCKET_KEY))
      .toEqual({ changed: 1, added: 0, removed: 0 });
  });

  it('null sidecar / null template → empty Map (no aggregation possible)', () => {
    const template = flatTemplate(3);
    const sidecar = twoSectionSidecar();
    const classifications = cls(['changed', 'changed', 'changed']);
    const trace = tr([1, 2, 3]);
    expect(aggregateBySection(classifications, trace, trace, null, sidecar, sidecar).size).toBe(0);
    // Null sidecar on the STAGED side (non-removed kinds read staged) →
    // all classifications fall to UNMAPPED.
    const result = aggregateBySection(classifications, trace, trace, template, sidecar, null);
    expect(result.size).toBe(1);
    expect(result.get(UNMAPPED_BUCKET_KEY))
      .toEqual({ changed: 3, added: 0, removed: 0 });
  });

  it('all-unchanged → all sections present with zero counts (renderer filters)', () => {
    const template = flatTemplate(6);
    const sidecar = twoSectionSidecar();
    const classifications = cls(['unchanged', 'unchanged', 'unchanged',
                                 'unchanged', 'unchanged', 'unchanged']);
    const trace = tr([1, 2, 3, 4, 5, 6]);
    const result = aggregateBySection(
      classifications, trace, trace, template, sidecar, sidecar,
    );
    // Both sections touched + present in the map with all-zero
    // counts. The renderer decides whether to display zero-only
    // buckets.
    expect(result.get('morning'))
      .toEqual({ changed: 0, added: 0, removed: 0 });
    expect(result.get('evening'))
      .toEqual({ changed: 0, added: 0, removed: 0 });
  });
});
