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
