/**
 * §16.2.2 c2 — frontend types + helpers for the read-only visual block
 * editor.
 *
 * Types here are HAND-TRANSLATED from `engine/editor_metadata.py`'s
 * dataclasses + the sidecar grammar (§16.2.1). Keep in sync — if you
 * change a field name in `engine/editor_metadata.py`, mirror it here.
 * The c1 API endpoint serialises both halves as `dict`/JSON, so this
 * file is the only place TypeScript-side consumers learn the shape.
 *
 * Pure-function module: no fetch, no DOM. Components import + render
 * against these types.
 */


// ---- station template shape (what the editor needs to render chips) ----
//
// We intentionally type ONLY the fields the canvas reads. The full
// template grammar lives in `engine/template.py`; capturing every
// field here would be a maintenance trap. New fields appear as
// optional + the canvas ignores what it doesn't render.


/** Engine block from the template's flat_slots or row_template. The
 *  chip renderer reads `strategy` + `source` + the strategy-specific
 *  parameter that drives the compact-label abbreviation. */
export interface EngineBlock {
  source: string;
  strategy:
    | 'pick'
    | 'window'
    | 'sequential'
    | 'jingle'
    | 'mix'
    | 'weighted'
    | 'prev_ref';
  size: number;
  // Strategy-specific optional fields. Hand-mirrored from
  // engine.template.BlockSpec. Pinned by tests/test_editor_ts_schema_parity.py
  // — adding a BlockSpec field here without mirroring it there fails
  // the parity guard.
  cursor_id?: string;       // sequential
  window?: number;          // window / weighted
  exclusion_window?: number; // mix
  genre?: string;           // jingle / mix
  position?: number;        // prev_ref
  fallback_genre?: string;  // prev_ref (optional fallback when mirror misses)
}


/** Station-level template. Only the fields the canvas needs. */
export interface StationTemplate {
  station: string;
  name: string;
  rng?: string;
  sources: Array<{ name: string; colour?: string; role: 'content' | 'reference' }>;
  layout: StationLayout;
}


/** XOR-encoded layout: EITHER flat_slots OR row_template — never
 *  both, never neither (engine enforces). */
export interface StationLayout {
  unit: string;
  unit_labels: string[];
  row_start_offsets: number[];
  cursors_init?: Record<string, number>;
  flat_slots?: EngineBlock[];
  row_template?: EngineBlock[];
}


// ---- sidecar shape ------------------------------------------------------


/** Atomic-block reference inside a section. EITHER slot_index OR
 *  (row_idx, block_idx). Never both (validate_sidecar enforces). */
export interface SidecarAtomicBlock {
  kind: 'atomic';
  slot_index?: number;
  row_idx?: number;
  block_idx?: number;
}


/** Macro reference inside a section — points at `Sidecar.macros[i]`
 *  whose id matches `ref`. */
export interface SidecarMacroBlock {
  kind: 'macro';
  ref: string;
}


export type SidecarBlock = SidecarAtomicBlock | SidecarMacroBlock;


export interface SidecarSection {
  id: string;
  /** OPTIONAL per Python `REQUIRED_SECTION_KEYS = {"id", "blocks"}`.
   *  Most hand-authored sidecars supply it; a section that omits it
   *  is still valid grammar (the canvas falls back to `id`). */
  name?: string;
  blocks: SidecarBlock[];
  /** If present, the section's `blocks` list is a per-row template
   *  instantiated once per offset. Used by row_template-style
   *  stations (Classics) to express "this same row at these
   *  start offsets". */
  repeat_with_offsets?: number[];
}


export interface SidecarMacro {
  id: string;
  /** OPTIONAL per Python grammar. Defaults to falling back to `id`
   *  in the chip renderer. */
  name?: string;
  repeat: number;
  /** Single engine block spec that gets repeated `repeat` times.
   *  Mirrors Python `Macro.spec` (NOT `blocks` — a Macro represents
   *  one repeated BlockSpec, not a list). c3's chip renderer reads
   *  this via `compactStrategyLabel(macro.spec)` to show the
   *  macro's strategy/source on the grouped chip. */
  spec: EngineBlock;
}


export interface Sidecar {
  schema_version: number;
  sections: SidecarSection[];
  /** OPTIONAL per Python `REQUIRED_TOP_LEVEL = {"schema_version",
   *  "sections", "layout_order"}`. Most stations have no macros;
   *  callers use `sidecar.macros ?? []` defensively. */
  macros?: SidecarMacro[];
  layout_order: string[];
  _comment?: string; // synthesized sidecars include one
}


// ---- expansion output (rendered chips) ----------------------------------


/** Single-slot address after expansion. Mirrors the Python AtomicRef
 *  dataclass. XOR encoding preserved. */
export interface AtomicRef {
  kind: 'atomic-ref';
  slot_index?: number;
  row_idx?: number;
  block_idx?: number;
}


/** One repeat-instance of a macro. Macros expand to N placeholders
 *  (one per `repeat`); the editor renders them as a single grouped
 *  chip with a count badge (§16.2.2 Decision macro-visual). */
export interface MacroPlaceholder {
  kind: 'macro-placeholder';
  macro_id: string;
  pick_in_macro: number; // 1-indexed within this macro's repeat span
}


export type ExpandedRef = AtomicRef | MacroPlaceholder;


// ---- helpers ------------------------------------------------------------


/**
 * Look up a macro by id. Returns null if unknown — a
 * `validate_sidecar`-passing sidecar won't have dangling refs, but
 * the helper is defensive for hand-edited / in-flight data.
 *
 * Mirror of Python `engine.editor_metadata.lookup_macro`.
 */
export function lookupMacro(sidecar: Sidecar, macroId: string): SidecarMacro | null {
  for (const m of sidecar.macros ?? []) {
    if (m.id === macroId) return m;
  }
  return null;
}


/**
 * Resolve a section into a flat list of `ExpandedRef`s the canvas
 * renders one chip per. Mirror of Python `expand_section`.
 *
 * Handling per the design:
 *   - Atomic block → emit one AtomicRef.
 *   - Macro block → look up the macro; emit `macro.repeat`
 *     MacroPlaceholders (one per repeat instance).
 *   - `repeat_with_offsets` — if set, the section's blocks are a
 *     per-row template, instantiated once per offset. Each
 *     instantiation emits one ref per block; atomic blocks' `row_idx`
 *     is overridden with the offset's POSITION in the list (not the
 *     numeric value of the offset).
 *
 * Returns the flat list in render order.
 *
 * Throws `EditorMetadataError` if a macro ref is dangling — same as
 * the Python side. `validate_sidecar` on the server catches this at
 * load time; the throw is defensive for misuse.
 */
export function expandSection(
  section: SidecarSection,
  sidecar: Sidecar,
): ExpandedRef[] {
  const macrosById = new Map((sidecar.macros ?? []).map((m) => [m.id, m]));

  function expandAtomic(
    b: SidecarAtomicBlock,
    rowOverride: number | null,
  ): AtomicRef {
    if (b.slot_index !== undefined) {
      return { kind: 'atomic-ref', slot_index: b.slot_index };
    }
    const rowIdx = rowOverride !== null ? rowOverride : b.row_idx!;
    return {
      kind: 'atomic-ref',
      row_idx: rowIdx,
      block_idx: b.block_idx!,
    };
  }

  function expandMacro(b: SidecarMacroBlock): MacroPlaceholder[] {
    const macro = macrosById.get(b.ref);
    if (macro === undefined) {
      throw new EditorMetadataError(
        `macro ref ${JSON.stringify(b.ref)} not found at expansion time`,
      );
    }
    const out: MacroPlaceholder[] = [];
    for (let i = 0; i < macro.repeat; i++) {
      out.push({
        kind: 'macro-placeholder',
        macro_id: macro.id,
        pick_in_macro: i + 1,
      });
    }
    return out;
  }

  function expandBlock(
    b: SidecarBlock,
    rowOverride: number | null,
  ): ExpandedRef[] {
    if (b.kind === 'atomic') return [expandAtomic(b, rowOverride)];
    return expandMacro(b);
  }

  if (section.repeat_with_offsets === undefined) {
    const out: ExpandedRef[] = [];
    for (const b of section.blocks) out.push(...expandBlock(b, null));
    return out;
  }

  const out: ExpandedRef[] = [];
  for (let rowIdx = 0; rowIdx < section.repeat_with_offsets.length; rowIdx++) {
    for (const b of section.blocks) out.push(...expandBlock(b, rowIdx));
  }
  return out;
}


/** Look up the underlying engine block from an `AtomicRef` + the
 *  station's layout. Returns null if the ref doesn't address a
 *  block (out of bounds — defensive; a validated sidecar wouldn't
 *  produce this). */
export function lookupEngineBlock(
  ref: AtomicRef,
  layout: StationLayout,
): EngineBlock | null {
  if (ref.slot_index !== undefined) {
    const blocks = layout.flat_slots ?? [];
    if (ref.slot_index < 0 || ref.slot_index >= blocks.length) return null;
    return blocks[ref.slot_index];
  }
  // row_template addressing — row_idx selects which ROW, but the
  // template is shared (it's a per-row template). So we read
  // `layout.row_template[block_idx]`.
  const blocks = layout.row_template ?? [];
  if (
    ref.block_idx === undefined ||
    ref.block_idx < 0 ||
    ref.block_idx >= blocks.length
  ) {
    return null;
  }
  return blocks[ref.block_idx];
}


/**
 * Short abbreviation for the chip's compact label mode.
 * Vocabulary fixed by DESIGN.md §16.2.2 Decision C:
 *
 *   pick       → "Pk"
 *   window     → "Wnd"
 *   sequential → "S:{cursor_id}"            e.g. "S:NIGHT_1"
 *   jingle     → "J:{genre}"                e.g. "J:Day"
 *   mix        → "M:{genre}"                e.g. "M:01Alphastate"
 *   weighted   → "W"
 *   prev_ref   → "P:{position}"             e.g. "P:1742"
 *
 * Missing strategy-specific params (engine block lacks the field)
 * fall back to a plain prefix without the colon — keeps the chip
 * non-empty even on degenerate templates.
 */
export function compactStrategyLabel(block: EngineBlock): string {
  switch (block.strategy) {
    case 'pick':
      return 'Pk';
    case 'window':
      return 'Wnd';
    case 'weighted':
      return 'W';
    case 'sequential':
      return block.cursor_id ? `S:${block.cursor_id}` : 'S';
    case 'jingle':
      return block.genre ? `J:${block.genre}` : 'J';
    case 'mix':
      return block.genre ? `M:${block.genre}` : 'M';
    case 'prev_ref':
      return block.position !== undefined ? `P:${block.position}` : 'P';
    default: {
      // Exhaustiveness guard — TS narrows `strategy` to `never` if
      // every case is handled. A new strategy added to EngineBlock
      // will fail to compile here.
      const _exhaustive: never = block.strategy;
      return _exhaustive;
    }
  }
}


/**
 * Look up the colour declared for a source in the station's
 * `sources[]`. Returns the colour string (CSS-valid; templates use
 * `#abc` / `#aabbcc` per convention) or a neutral fallback when the
 * source name is unknown or has no colour set.
 *
 * §16.2.2 c3 — drives the chip's color-by-source default view.
 */
export function sourceColour(
  template: StationTemplate,
  sourceName: string,
): string {
  for (const s of template.sources) {
    if (s.name === sourceName) return s.colour ?? '#ccc';
  }
  return '#ccc';
}


/**
 * Verbose label including the strategy + every parameter that
 * influences the slot. Used in the tooltip + the click-detail
 * panel. Multi-line via newline; consumers can render with
 * `white-space: pre`.
 */
export function verboseStrategyLabel(block: EngineBlock): string {
  const lines: string[] = [`strategy: ${block.strategy}`, `source: ${block.source}`, `size: ${block.size}`];
  if (block.cursor_id !== undefined) lines.push(`cursor_id: ${block.cursor_id}`);
  if (block.window !== undefined) lines.push(`window: ${block.window}`);
  if (block.exclusion_window !== undefined) {
    lines.push(`exclusion_window: ${block.exclusion_window}`);
  }
  if (block.genre !== undefined) lines.push(`genre: ${block.genre}`);
  if (block.position !== undefined) lines.push(`position: ${block.position}`);
  if (block.fallback_genre !== undefined) {
    lines.push(`fallback_genre: ${block.fallback_genre}`);
  }
  return lines.join('\n');
}


// ---- §16.2.2 c4 — view-mode store ---------------------------------------


/** Three orthogonal display dimensions per DESIGN.md §16.2.2
 *  Decision C. Each value persists to localStorage so the operator's
 *  preferences survive page reloads.
 *
 *  Defaults match the design's "first-impression contract":
 *    - labelMode    = "compact"   (Pk / Wnd / S:NIGHT_1 abbreviations)
 *    - colorBy      = "source"    (each pool's declared colour)
 *    - macroVisual  = "wider-chip" (single chip + ×N count badge)
 */
export type LabelMode = 'compact' | 'verbose';
export type ColorBy = 'source' | 'strategy';
export type MacroVisual = 'wider-chip' | 'stacked' | 'count-badge';


export interface ViewModeState {
  labelMode: LabelMode;
  colorBy: ColorBy;
  macroVisual: MacroVisual;
}


export const DEFAULT_VIEW_MODE: ViewModeState = {
  labelMode: 'compact',
  colorBy: 'source',
  macroVisual: 'wider-chip',
};


// localStorage key for the view-mode prefs blob.
const VIEW_MODE_LS_KEY = 'station-studio.viewMode.v1';


/**
 * Strategy-color palette — used when `colorBy === 'strategy'`. The
 * design doc punts on exact colors (DESIGN.md §16.2.2 Decision C:
 * "implementation detail"). These are picked for visual distinction
 * + decent dark-on-light contrast; refine post-feedback.
 */
const STRATEGY_COLOURS: Record<string, string> = {
  pick: '#7d3c98',
  window: '#1abc9c',
  sequential: '#e67e22',
  jingle: '#f39c12',
  mix: '#16a085',
  weighted: '#2980b9',
  prev_ref: '#8e44ad',
};


/** Resolve the chip's display colour given the view mode. Falls back
 *  to source colour when colorBy='source' (existing behaviour).
 *  Used by `<Chip>` to drive the border / background tint. */
export function chipColour(
  block: EngineBlock,
  template: StationTemplate,
  colorBy: ColorBy,
): string {
  if (colorBy === 'source') {
    return sourceColour(template, block.source);
  }
  return STRATEGY_COLOURS[block.strategy] ?? '#888';
}


/**
 * Read the persisted view mode from localStorage, or return defaults
 * if nothing is set / SSR (no window). Defensive against malformed
 * JSON + missing fields — partial reads use defaults for the
 * unknown half.
 */
export function readViewMode(): ViewModeState {
  if (typeof window === 'undefined') return { ...DEFAULT_VIEW_MODE };
  try {
    const raw = window.localStorage.getItem(VIEW_MODE_LS_KEY);
    if (raw === null) return { ...DEFAULT_VIEW_MODE };
    const parsed = JSON.parse(raw) as Partial<ViewModeState>;
    return {
      labelMode: parsed.labelMode ?? DEFAULT_VIEW_MODE.labelMode,
      colorBy: parsed.colorBy ?? DEFAULT_VIEW_MODE.colorBy,
      macroVisual: parsed.macroVisual ?? DEFAULT_VIEW_MODE.macroVisual,
    };
  } catch {
    return { ...DEFAULT_VIEW_MODE };
  }
}


/** Persist the view mode to localStorage. No-op under SSR. */
export function writeViewMode(state: ViewModeState): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(VIEW_MODE_LS_KEY, JSON.stringify(state));
  } catch {
    // Quota errors / disabled storage — ignore silently; the
    // session-level reactive state still works.
  }
}


// ---- §16.2.2.t1 — runtime response validation ---------------------------


/** Shape returned by `parseEditorSidecarResponse` — the post-
 *  validation typed projection of the wire response. Mirrors
 *  the api.ts `EditorSidecarResponse` but with concrete types
 *  on `template` + `sidecar` rather than opaque `Record`s. */
export interface ParsedEditorSidecar {
  template: StationTemplate;
  sidecar: Sidecar;
  sidecar_error: string | null;
}


/**
 * Validate + narrow the raw `/api/stations/{slug}/sidecar` response
 * to typed shapes. Throws `EditorMetadataError` (string-only error;
 * no traceback noise) when the response doesn't match what
 * `<EditorCanvas>` needs to render.
 *
 * Decision E (DESIGN.md §16.2.2): the FRONTEND never handles a
 * "no sidecar" case — the server's synthesize-trivial fallback
 * means `sidecar` is always non-null. This validator pins the
 * MINIMUM shape the canvas reads:
 *
 *   * `template.station: string` + `template.name: string` +
 *     `template.sources: array` + `template.layout: object`
 *   * `sidecar.schema_version: 1` + `sidecar.sections: array` +
 *     `sidecar.layout_order: array`
 *   * `sidecar_error: string | null`
 *
 * Deeper structural drift (e.g. one section's blocks malformed)
 * is handled by the existing component defensive renders — the
 * `?` chip + DetailPanel "not found" warning. This validator's
 * job is to catch TOP-LEVEL shape drift loudly, NOT to re-run
 * the full engine grammar (that's the server's job + the
 * tests/test_editor_ts_schema_parity.py guard).
 */
export function parseEditorSidecarResponse(raw: unknown): ParsedEditorSidecar {
  function fail(field: string, expected: string, got: unknown): never {
    throw new EditorMetadataError(
      `editor sidecar response: ${field} expected ${expected}, ` +
        `got ${typeof got === 'object' ? JSON.stringify(got).slice(0, 60) : typeof got}`,
    );
  }

  if (raw === null || typeof raw !== 'object') {
    return fail('response', 'object', raw);
  }
  const r = raw as Record<string, unknown>;

  // template
  if (r.template === null || typeof r.template !== 'object') {
    return fail('template', 'object', r.template);
  }
  const t = r.template as Record<string, unknown>;
  if (typeof t.station !== 'string') return fail('template.station', 'string', t.station);
  if (typeof t.name !== 'string') return fail('template.name', 'string', t.name);
  if (!Array.isArray(t.sources)) return fail('template.sources', 'array', t.sources);
  if (t.layout === null || typeof t.layout !== 'object') {
    return fail('template.layout', 'object', t.layout);
  }

  // sidecar
  if (r.sidecar === null || typeof r.sidecar !== 'object') {
    return fail('sidecar', 'object', r.sidecar);
  }
  const s = r.sidecar as Record<string, unknown>;
  if (s.schema_version !== 1) {
    return fail('sidecar.schema_version', '1', s.schema_version);
  }
  if (!Array.isArray(s.sections)) return fail('sidecar.sections', 'array', s.sections);
  if (!Array.isArray(s.layout_order)) return fail('sidecar.layout_order', 'array', s.layout_order);

  // sidecar_error
  if (r.sidecar_error !== null && typeof r.sidecar_error !== 'string') {
    return fail('sidecar_error', 'string | null', r.sidecar_error);
  }

  // Narrowed structurally; full grammar already validated server-side.
  return {
    template: r.template as StationTemplate,
    sidecar: r.sidecar as Sidecar,
    sidecar_error: r.sidecar_error as string | null,
  };
}


// ---- error type ---------------------------------------------------------


/**
 * Thrown by `expandSection` if a macro ref is dangling.
 * Mirrors Python `engine.editor_metadata.EditorMetadataError` —
 * but TypeScript-side this is a defensive-only error class; the
 * server's `validate_sidecar` catches dangling refs at API-load
 * time, so a sidecar arriving via `/api/stations/{slug}/sidecar`
 * never triggers this.
 */
export class EditorMetadataError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'EditorMetadataError';
  }
}
