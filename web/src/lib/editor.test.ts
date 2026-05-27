import { describe, it, expect, beforeEach } from 'vitest';
import {
  DEFAULT_VIEW_MODE,
  EditorMetadataError,
  chipColour,
  compactStrategyLabel,
  expandSection,
  lookupEngineBlock,
  lookupMacro,
  parseEditorSidecarResponse,
  readViewMode,
  sourceColour,
  verboseStrategyLabel,
  writeViewMode,
  type EngineBlock,
  type Sidecar,
  type SidecarMacro,
  type SidecarSection,
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
