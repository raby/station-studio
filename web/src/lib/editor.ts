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
    | 'prev_ref'
    // §16.14.t6: position-based lookup into a role: reference pool.
    // Reuses the `position` field (1-indexed). No `fallback_genre`
    // — pure deterministic, no fallback path.
    | 'archive_ref';
  size: number;
  // Strategy-specific optional fields. Hand-mirrored from
  // engine.template.BlockSpec. Pinned by tests/test_editor_ts_schema_parity.py
  // — adding a BlockSpec field here without mirroring it there fails
  // the parity guard.
  cursor_id?: string;       // sequential (required; must be a cursors_init key)
  window?: number;          // weighted ONLY (optional, default 10) — NOT the `window` strategy
  exclusion_window?: number; // mix (optional)
  genre?: string;           // jingle (required) / mix (required)
  position?: number;        // prev_ref (required, 1-indexed)
  fallback_genre?: string;  // prev_ref (optional fallback when mirror misses)
  // §16.14.t7: per-block allowlist (positive filter). Optional for
  // all content-sampling strategies (weighted, window, pick,
  // sequential); forbidden for non-sampling strategies (jingle,
  // mix, prev_ref, archive_ref). When set, the strategy is
  // restricted to tracks whose persistent_id appears in AT LEAST
  // ONE named role: reference pool (union semantics). Empty list
  // collapses to None at parse time (= no constraint).
  allowlist_sources?: string[];
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
    case 'archive_ref':
      // §16.14.t6: archive_ref shares prev_ref's `position` field
      // semantics; abbreviate distinctly ("A:" vs "P:") so the
      // chip surface tells the operator WHICH reference strategy
      // a slot uses without expanding to the full strategy name.
      return block.position !== undefined ? `A:${block.position}` : 'A';
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
  /** §16.2.3 — optimistic-concurrency token. The editor sends it back
   *  in a block PATCH's `expected_updated_at`. Null only on legacy
   *  rows with no timestamp. */
  updated_at: string | null;
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
  // Per-source-element check (review 🟡-2). Only `name` is load-
  // bearing — `colour` / `role` have defensive fallbacks. A
  // degenerate `sources: [42]` would silently render '?' chips
  // without this guard.
  for (let i = 0; i < t.sources.length; i++) {
    const src = t.sources[i] as unknown;
    if (src === null || typeof src !== 'object') {
      return fail(`template.sources[${i}]`, 'object', src);
    }
    if (typeof (src as Record<string, unknown>).name !== 'string') {
      return fail(
        `template.sources[${i}].name`, 'string',
        (src as Record<string, unknown>).name,
      );
    }
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
  // Per-section-element check (review 🟡-3). Each section needs at
  // minimum `id: string` (drives layout_order lookups) +
  // `blocks: array` (drives expandSection). Drilling deeper into
  // each block stays the server's job — validate_sidecar's drift
  // rules cover the inner grammar.
  for (let i = 0; i < s.sections.length; i++) {
    const sec = s.sections[i] as unknown;
    if (sec === null || typeof sec !== 'object') {
      return fail(`sidecar.sections[${i}]`, 'object', sec);
    }
    const so = sec as Record<string, unknown>;
    if (typeof so.id !== 'string') {
      return fail(`sidecar.sections[${i}].id`, 'string', so.id);
    }
    if (!Array.isArray(so.blocks)) {
      return fail(`sidecar.sections[${i}].blocks`, 'array', so.blocks);
    }
  }
  // Per-layout_order-element check. Each entry is the id of a
  // section to render at that position; a non-string would silently
  // miss the lookup + render nothing.
  for (let i = 0; i < s.layout_order.length; i++) {
    const id = s.layout_order[i];
    if (typeof id !== 'string') {
      return fail(`sidecar.layout_order[${i}]`, 'string', id);
    }
  }

  // sidecar_error
  if (r.sidecar_error !== null && typeof r.sidecar_error !== 'string') {
    return fail('sidecar_error', 'string | null', r.sidecar_error);
  }

  // updated_at (§16.2.3) — string | null | absent (treat absent as null).
  if (
    r.updated_at !== undefined &&
    r.updated_at !== null &&
    typeof r.updated_at !== 'string'
  ) {
    return fail('updated_at', 'string | null', r.updated_at);
  }

  // Narrowed structurally; full grammar already validated server-side.
  return {
    template: r.template as StationTemplate,
    sidecar: r.sidecar as Sidecar,
    sidecar_error: r.sidecar_error as string | null,
    updated_at: (r.updated_at as string | null | undefined) ?? null,
  };
}


// ---- §16.2.3 c1 — editable-field schema (atomic edit) -------------------
//
// Which block fields the editor lets you change, per strategy. Locked
// to PARAMETER fields only (§16.2.3 Decision C): strategy / source /
// size are NOT editable here (structural — §16.2.4/§16.2.5). The
// schema MIRRORS engine/template.py's `_require_strategy_field` table
// — keep the two in sync. The PATCH endpoint (c2) re-validates server-
// side; these specs drive the form + instant client feedback.


export type EditableFieldType = 'string' | 'int' | 'string_list';


export interface EditableFieldSpec {
  /** Block field name. One of the EngineBlock parameter fields. */
  name:
    | 'cursor_id'
    | 'genre'
    | 'window'
    | 'exclusion_window'
    | 'position'
    | 'fallback_genre'
    // §16.2.4 D3: per-block allowlist (§16.14.t7). Editable on the
    // four content-sampling strategies (weighted, window, pick,
    // sequential).
    | 'allowlist_sources';
  /** Human label for the form. */
  label: string;
  type: EditableFieldType;
  /** Required for this strategy (matches engine `required_for`). When
   *  false the field is optional (engine `optional_for`) — clearing it
   *  removes the key from the block. */
  required: boolean;
  /** Minimum value for int fields (engine validators: window/
   *  exclusion_window ≥ 0, position ≥ 1). */
  min?: number;
  /** cursor_id only: the allowed values are the station's
   *  `layout.cursors_init` keys, not free text — the form renders a
   *  <select>. Mirrors the engine validator
   *  `v in known_cursor_ids`. */
  enumFromCursors?: boolean;
  /** string_list only (§16.2.4 D3): autocomplete from the station's
   *  sources, filtered to the named role. `allowlist_sources` uses
   *  'reference' (§16.14.t7 D3 — only reference pools are valid
   *  allowlist sources). The form renders a chip-style multi-input
   *  reusing the §16.14.t11 ListRuleRow pattern (free-form input +
   *  autocomplete suggestions). Empty list / clearing the field
   *  removes the key from the block (= "no constraint", matching
   *  the engine's empty-list-collapses-to-None contract). */
  sourceRoleFilter?: 'content' | 'reference';
}


/**
 * Per-strategy editable parameter fields. Empty array = nothing to
 * edit (pick / window strategies have no parameters).
 *
 * Source-of-truth cross-check: engine/template.py `_parse_block`:
 *   sequential → cursor_id (required, ∈ cursors_init) + allowlist_sources
 *   jingle     → genre (required, non-empty)
 *   mix        → genre (required) + exclusion_window (optional, ≥0)
 *   weighted   → window (optional, ≥0; default 10) + allowlist_sources
 *   prev_ref   → position (required, ≥1) + fallback_genre (optional)
 *   pick       → allowlist_sources (§16.2.4 D3)
 *   window     → allowlist_sources (§16.2.4 D3)
 *
 * §16.2.4 D3: `allowlist_sources` (§16.14.t7) joins on the four
 * content-sampling strategies (weighted, window, pick, sequential
 * — the engine's CONTENT_SAMPLING_STRATEGIES opt-in set). Closes
 * the §16.14.t7 c3 docs promise that "the block visualiser does
 * not yet expose per-block fields for editing." Source-pool
 * autocomplete filters to `role: reference` per §16.14.t7 D3.
 */
// §16.2.4 c1: inline the allowlist_sources spec (vs extracting a
// shared constant). The editable-fields parity test
// (tests/test_editable_fields_parity.py) parses `name: 'X'` literals
// inside each strategy's array — a shared constant hides from the
// regex. Inlining keeps the source-of-truth in the literal form
// the parity test expects. Small duplication, big simplicity win.
export const EDITABLE_FIELDS: Record<EngineBlock['strategy'], EditableFieldSpec[]> = {
  pick: [
    { name: 'allowlist_sources', label: 'Allowlist sources (per-block positive filter)', type: 'string_list', required: false, sourceRoleFilter: 'reference' },
  ],
  window: [
    { name: 'allowlist_sources', label: 'Allowlist sources (per-block positive filter)', type: 'string_list', required: false, sourceRoleFilter: 'reference' },
  ],
  sequential: [
    { name: 'cursor_id', label: 'Cursor', type: 'string', required: true, enumFromCursors: true },
    { name: 'allowlist_sources', label: 'Allowlist sources (per-block positive filter)', type: 'string_list', required: false, sourceRoleFilter: 'reference' },
  ],
  jingle: [
    { name: 'genre', label: 'Genre', type: 'string', required: true },
  ],
  mix: [
    { name: 'genre', label: 'Genre', type: 'string', required: true },
    { name: 'exclusion_window', label: 'Exclusion window (weeks)', type: 'int', required: false, min: 0 },
  ],
  weighted: [
    { name: 'window', label: 'Artist-dedup window', type: 'int', required: false, min: 0 },
    { name: 'allowlist_sources', label: 'Allowlist sources (per-block positive filter)', type: 'string_list', required: false, sourceRoleFilter: 'reference' },
  ],
  prev_ref: [
    { name: 'position', label: 'Position (1-indexed)', type: 'int', required: true, min: 1 },
    { name: 'fallback_genre', label: 'Fallback genre', type: 'string', required: false },
  ],
  // §16.14.t6: archive_ref shares prev_ref's `position` field
  // (1-indexed). NO `fallback_genre` — archive_ref has no
  // fallback path (D5: pure deterministic, fail-loud on OOB).
  // NO `allowlist_sources` — archive_ref samples from a reference
  // pool already (would be meaningless; parser rejects).
  archive_ref: [
    { name: 'position', label: 'Position (1-indexed)', type: 'int', required: true, min: 1 },
  ],
};


/** The editable-field specs for a block, by its strategy. Empty when
 *  the strategy has no parameters (pick / window). */
export function editableFieldsForBlock(block: EngineBlock): EditableFieldSpec[] {
  return EDITABLE_FIELDS[block.strategy] ?? [];
}


// ---- §16.2.4 c2 — role-aware bidirectional dropdowns (D4) -----------


/** All supported strategy names. The literal union from EngineBlock
 *  isn't iterable; this is the runtime mirror used by retype helpers. */
export type Strategy = EngineBlock['strategy'];

export const ALL_STRATEGIES: readonly Strategy[] = [
  'pick', 'window', 'sequential', 'jingle', 'mix', 'weighted',
  'prev_ref', 'archive_ref',
] as const;

/** Strategies that sample from a `role: reference` pool (mirrors the
 *  engine's `REFERENCE_SAMPLING_STRATEGIES`). archive_ref is the only
 *  one today; new reference-sampling strategies join here. */
export const REFERENCE_SAMPLING_STRATEGIES: readonly Strategy[] = [
  'archive_ref',
] as const;

/** Single source of truth for "what source role does this strategy
 *  need" (c3 review #9 cleanup). The pair `compatibleStrategies` /
 *  `compatibleSources` are now both expressed in terms of this
 *  function — a future second reference-sampling strategy joins
 *  `REFERENCE_SAMPLING_STRATEGIES` and both filters update via this
 *  single helper. */
export function roleForStrategy(strategy: Strategy): 'content' | 'reference' {
  return REFERENCE_SAMPLING_STRATEGIES.includes(strategy)
    ? 'reference'
    : 'content';
}

/** Strategies compatible with the given source role.
 *
 *  D4 (§16.2.4): the strategy dropdown filters by the CURRENT source's
 *  role so retype options that would role-conflict are hidden up front
 *  — prevents save-then-400 surprises. Server still validates via
 *  parse_template's strategy-aware role cross-check.
 *
 *    content   → all strategies EXCEPT archive_ref
 *    reference → ONLY archive_ref (today)
 */
export function compatibleStrategies(
  sourceRole: 'content' | 'reference',
): readonly Strategy[] {
  return ALL_STRATEGIES.filter((s) => roleForStrategy(s) === sourceRole);
}

/** Sources compatible with the given strategy.
 *
 *  D4 (§16.2.4): symmetric to compatibleStrategies. The source
 *  dropdown shows ONLY sources with a role the new strategy can
 *  consume:
 *
 *    archive_ref      → reference sources only
 *    any other        → content sources only
 *
 *  Returns the input sources filtered + in declaration order (the
 *  template's `sources[]` order — preserves operator intent for
 *  alphabetical/grouped lists).
 */
export function compatibleSources(
  strategy: Strategy,
  sources: StationTemplate['sources'],
): StationTemplate['sources'] {
  const required = roleForStrategy(strategy);
  return sources.filter((s) => s.role === required);
}


/** The cursor ids a `sequential` block may point at — the keys of the
 *  station's `layout.cursors_init`. Drives the cursor_id <select>. */
export function availableCursorIds(template: StationTemplate): string[] {
  return Object.keys(template.layout.cursors_init ?? {});
}


/**
 * Validate one editable field's prospective value, mirroring the
 * engine's `_require_strategy_field` validators. Returns an error
 * string or null. `value` is the form value: `string` for string
 * fields, `number` (or NaN from an empty number input) for int
 * fields, or `undefined`/'' when the user cleared an optional field.
 *
 * The PATCH endpoint re-runs the real engine validation; this gives
 * instant inline feedback before the round-trip.
 */
export function validateEditableField(
  spec: EditableFieldSpec,
  value: string | number | string[] | undefined,
  template?: StationTemplate,
): string | null {
  // c2 review #3 + #4: explicit string_list branch — previously the
  // call sites special-cased string_list to always-null which lied
  // about validity for any future required string_list field. Now
  // the type checks here against the spec.type and respects spec.required.
  if (spec.type === 'string_list') {
    const arr = Array.isArray(value) ? value : [];
    if (arr.length === 0 && spec.required) {
      return `${spec.label} is required`;
    }
    return null;
  }

  const empty =
    value === undefined ||
    value === '' ||
    (typeof value === 'number' && Number.isNaN(value));

  if (empty) {
    return spec.required ? `${spec.label} is required` : null;
  }

  if (spec.type === 'int') {
    const n = typeof value === 'number' ? value : Number(value);
    if (!Number.isInteger(n)) return `${spec.label} must be a whole number`;
    if (spec.min !== undefined && n < spec.min) {
      return `${spec.label} must be ≥ ${spec.min}`;
    }
    return null;
  }

  // string (or anything that survived the int branch). Reject
  // accidental array values for non-list specs.
  if (Array.isArray(value)) {
    return `${spec.label} must be a single value, got a list`;
  }
  const s = String(value);
  if (spec.enumFromCursors && template) {
    const ids = availableCursorIds(template);
    if (!ids.includes(s)) {
      return `${spec.label} must be one of: ${ids.join(', ') || '(none defined)'}`;
    }
  }
  return null;
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
