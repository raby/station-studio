/**
 * §16.2.6 c3 — edit-op log + local applyEdit.
 *
 * Live-preview's staging mode (toggle ON) intercepts edits before
 * they hit the server. Each edit becomes an `Edit` op queued in the
 * page's `opLog`. The page renders the cumulative effect via
 * `applyEdit` (a pure function — same op log applied to the same
 * baseTemplate produces an identical stagedTemplate). On Save the
 * page replays the opLog as sequential PATCHes; on Discard it
 * clears the log.
 *
 * **Why op-log primary, sidecar derived (DESIGN §16.2.6 c3)**
 *
 * Save needs to replay each edit as the matching PATCH endpoint —
 * that requires the EDIT-level granularity. A cloned-sidecar-as-
 * primary design would lose the op boundaries ("rename A→B then B→C"
 * vs "rename A→C" collapse to the same final state but produce
 * different replay traces). Keeping the op log canonical + the
 * derived sidecar/template recomputed gives Save what it needs
 * without double-bookkeeping.
 *
 * **c3 MVP scope: patchBlock only**
 *
 * Only `patchBlock` (the chip-edit Save path) is intercepted in c3.
 * The other 8 edit kinds (patchMacro, renameSection, sectionCreate,
 * sectionDelete, reorderSections, moveBlock, retypeBlock,
 * structuralEdit) still fall through to immediate PATCH even when
 * the toggle is ON — c3b extends applyEdit + the page handlers to
 * cover them. The same opLog data structure carries them; only the
 * applyEdit dispatch + handler interception is added per kind.
 *
 * Documented as a c3 limitation in the page; the deferred kinds
 * still produce correct end results (immediate-PATCH path is the
 * §16.2.5 baseline) — they just don't appear in the trace pane's
 * staged preview until c3b lands.
 */
import type { BlockAddress } from './api';
import type { StationTemplate } from './editor';

// Re-export so consumers that import the Edit union from here also
// have access to the address shape. The `BlockAddress` interface
// lives in `./api` because the PATCH helpers (patchBlock,
// moveBlock, structuralBlockEdit) consume it directly — single
// source of truth for the address shape.
//
// c3 review #1 finding #6: prior version duplicated the interface
// locally with a comment claiming a cyclic import existed.
// Verified: no cycle (`./editor` doesn't import from here or
// `./api`); the duplication was drift hazard. Importing from `./api`
// keeps the address shape in one place.
export type { BlockAddress };

/**
 * Edit op produced by the chip-edit DetailPanel Save flow. Replayed
 * by the page's Save handler as `patchBlock(slug, address, fields, ...)`.
 */
export interface PatchBlockOp {
  kind: 'patchBlock';
  address: BlockAddress;
  fields: Record<string, string | number | string[] | null>;
}

/**
 * §16.2.6 c3b — macro-edit DetailPanel Save flow. The replay calls
 * `patchMacro(slug, macroId, fields, ...)`. Field merge mirrors the
 * backend's parameter-only contract on the macro's spec (same null=
 * remove / value=set semantic as patchBlock).
 */
export interface PatchMacroOp {
  kind: 'patchMacro';
  macroId: string;
  fields: Record<string, string | number | string[] | null>;
}

/**
 * §16.2.6 c3b — section-rename via inline edit (SectionHeader's
 * rename input + commit). Replayed as
 * `renameSection(slug, sectionId, name, ...)`. The section id stays
 * immutable; only `name` changes — matches the backend PATCH
 * /sections/{id} contract.
 */
export interface RenameSectionOp {
  kind: 'renameSection';
  sectionId: string;
  name: string;
}

export type Edit = PatchBlockOp | PatchMacroOp | RenameSectionOp;


/**
 * Apply a single edit op to a (template, sidecar) snapshot. PURE:
 * never mutates its inputs — returns new objects so reactive Svelte
 * derivations see the change. For patchBlock the sidecar is
 * unchanged (parameters live on the template's flat_slots /
 * row_template entries, not on the sidecar's atomic refs).
 *
 * The returned (template, sidecar) pair is what the preview
 * endpoint receives — `applyEdits(base, [op1, op2, ...])`
 * composes them in order.
 *
 * **patchBlock semantics** mirror the backend `/blocks` endpoint:
 *
 * - For `flat_slots` layouts: address is `{slot_index}`; the field
 *   updates apply to `template.layout.flat_slots[slot_index]`.
 * - For `row_template` layouts: address is `{block_idx}`; the field
 *   updates apply to `template.layout.row_template[block_idx]`
 *   (row_template stations edit the SHARED block — every row's
 *   instance inherits — see §16.2.3 Decision B).
 * - Field values are merged: `null` removes the key, anything else
 *   sets it. Matches the backend's parameter-only contract.
 * - Unknown address → no-op (the op log MUST not have invalid
 *   addresses because the canvas's address-resolution refToAddress
 *   already filtered them — defensive no-op rather than throw).
 */
export function applyEdit(
  template: StationTemplate,
  sidecar: unknown,
  op: Edit,
): { template: StationTemplate; sidecar: unknown } {
  switch (op.kind) {
    case 'patchBlock':
      return {
        template: _patchBlock(template, op.address, op.fields),
        sidecar,
      };
    case 'patchMacro':
      return {
        template,
        sidecar: _patchMacro(sidecar, op.macroId, op.fields),
      };
    case 'renameSection':
      return {
        template,
        sidecar: _renameSection(sidecar, op.sectionId, op.name),
      };
    default: {
      // §16.2.6 c3b: exhaustiveness pin. The Edit union now has
      // 3+ kinds so TS narrows correctly to `never` after the
      // exhaustive switch. A future kind added without an
      // applyEdit case fails at compile time here.
      //
      // Review #1 fix #6: dropped the pointless `_exhaustive ??
      // sidecar` — `_exhaustive` is `never` at type level, so the
      // `??` was dead code. Return the original snapshot unchanged
      // if the default ever runs at runtime (which it can't under
      // a sound union — the assignment fails compile if not).
      const _exhaustive: never = op;
      void _exhaustive;
      return { template, sidecar };
    }
  }
}


/** Compose a list of edits in order against a base snapshot. */
export function applyEdits(
  baseTemplate: StationTemplate,
  baseSidecar: unknown,
  ops: Edit[],
): { template: StationTemplate; sidecar: unknown } {
  let template = baseTemplate;
  let sidecar = baseSidecar;
  for (const op of ops) {
    ({ template, sidecar } = applyEdit(template, sidecar, op));
  }
  return { template, sidecar };
}


function _patchBlock(
  template: StationTemplate,
  address: BlockAddress,
  fields: Record<string, string | number | string[] | null>,
): StationTemplate {
  const layout = template.layout;
  const flat = layout.flat_slots;
  const rows = layout.row_template;

  // c3 review #1 fix #10: defensive XOR guard. The engine enforces
  // flat_slots XOR row_template via parse_template; a corrupted
  // station with BOTH populated would otherwise get silently
  // misrouted by the if-else below (flat wins) — operator sees the
  // edit reflected locally, then the preview endpoint 400s when
  // parse_template rejects the template. No-op + let the engine
  // surface the XOR error explicitly.
  if (flat !== undefined && rows !== undefined) {
    return template;
  }

  // Locate the target block — flat or row_template. Defensive
  // no-op on out-of-range or wrong-kind address.
  let targetIdx: number;
  let targetList: Record<string, unknown>[] | undefined;
  if (flat !== undefined && address.slot_index !== undefined) {
    if (address.slot_index < 0 || address.slot_index >= flat.length) {
      return template;
    }
    targetIdx = address.slot_index;
    targetList = flat as unknown as Record<string, unknown>[];
  } else if (rows !== undefined && address.block_idx !== undefined) {
    if (address.block_idx < 0 || address.block_idx >= rows.length) {
      return template;
    }
    targetIdx = address.block_idx;
    targetList = rows as unknown as Record<string, unknown>[];
  } else {
    // Address shape doesn't match layout form (e.g. flat_slots
    // station with a block_idx-only address). Defensive no-op.
    return template;
  }

  // Build the new block dict by merging fields. Review #1 fix #5:
  // use the shared `_mergeFields` helper instead of an inline
  // copy of the same merge loop — same null=remove / value=set
  // semantic as the patchMacro path, kept in one place so a
  // future change to the parameter-only contract updates both.
  const oldBlock = targetList[targetIdx];
  const newBlock = _mergeFields(oldBlock, fields);

  // Reconstruct the template with the new block spliced in. Deep-
  // copy only the surrounding structure (the rest of the template is
  // unchanged + can share references safely — applyEdit's pure
  // contract holds as long as we don't mutate the inputs).
  const nextList = targetList.slice();
  nextList[targetIdx] = newBlock;
  const nextLayout = { ...layout };
  if (targetList === (flat as unknown as Record<string, unknown>[])) {
    (nextLayout as Record<string, unknown>).flat_slots = nextList;
  } else {
    (nextLayout as Record<string, unknown>).row_template = nextList;
  }
  return { ...template, layout: nextLayout } as StationTemplate;
}


/**
 * §16.2.6 c3b — apply field updates to a macro's spec inside the
 * sidecar. Mirrors the backend `/macros/{macro_id}` PATCH semantics:
 * fields merge into `macros[i].spec`; null deletes a key; anything
 * else sets it. The macro's `id` + `name` + `repeat` stay
 * top-level + are NOT touched by patchMacro (they have their own
 * mutation paths).
 *
 * Defensive no-op when:
 *   - sidecar isn't a dict
 *   - macros is missing or not an array
 *   - no macro matches macroId
 *
 * Returns a new sidecar dict (PURE — does not mutate input).
 */
function _patchMacro(
  sidecar: unknown,
  macroId: string,
  fields: Record<string, string | number | string[] | null>,
): unknown {
  if (!_isSidecarLike(sidecar)) return sidecar;
  const macros = (sidecar as Record<string, unknown>).macros;
  if (!Array.isArray(macros)) return sidecar;
  let found = false;
  let bailed = false;
  const nextMacros = macros.map((m) => {
    if (!m || typeof m !== 'object') return m;
    const macro = m as Record<string, unknown>;
    if (macro.id !== macroId) return m;
    found = true;
    // Review #1 fix #2 + #4: bail when spec is missing OR is an
    // array. Pre-fix the defensive default `{}` produced a
    // synthetic partial spec (e.g. `{size: 5}` with no strategy /
    // source) — the replayed PATCH would 400 at the engine OR
    // (worse) commit a fragment. Bail leaves the macro untouched
    // + lets validate_sidecar surface the corruption at the next
    // mutation. `typeof [] === 'object'` so the prior `&& typeof
    // === 'object'` check ACCEPTED arrays + would have spread
    // them into an index-keyed dict — same fix covers both.
    if (
      !macro.spec ||
      typeof macro.spec !== 'object' ||
      Array.isArray(macro.spec)
    ) {
      bailed = true;
      return m;
    }
    return {
      ...macro,
      spec: _mergeFields(macro.spec as Record<string, unknown>, fields),
    };
  });
  // If the target was found but bailed on a malformed spec, return
  // the original sidecar (matches the "no observable change"
  // contract for non-applicable edits).
  if (!found || bailed) return sidecar;
  return { ...(sidecar as Record<string, unknown>), macros: nextMacros };
}


/**
 * §16.2.6 c3b — rename a section in the sidecar. Section id is
 * immutable per the backend PATCH /sections/{id} contract; only
 * `name` changes. Defensive no-op on missing section or non-array
 * sections.
 *
 * Returns a new sidecar dict (PURE).
 */
function _renameSection(
  sidecar: unknown,
  sectionId: string,
  name: string,
): unknown {
  if (!_isSidecarLike(sidecar)) return sidecar;
  const sections = (sidecar as Record<string, unknown>).sections;
  if (!Array.isArray(sections)) return sidecar;
  let found = false;
  const nextSections = sections.map((s) => {
    if (!s || typeof s !== 'object') return s;
    const section = s as Record<string, unknown>;
    if (section.id !== sectionId) return s;
    found = true;
    return { ...section, name };
  });
  if (!found) return sidecar;
  return { ...(sidecar as Record<string, unknown>), sections: nextSections };
}


/** Merge fields into a target dict — null removes the key, anything
 *  else sets it. Matches the backend's parameter-only contract used
 *  by patchBlock + patchMacro. Returns a new dict (PURE). */
function _mergeFields(
  target: Record<string, unknown>,
  fields: Record<string, string | number | string[] | null>,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...target };
  for (const [k, v] of Object.entries(fields)) {
    if (v === null) delete next[k];
    else next[k] = v;
  }
  return next;
}


function _isSidecarLike(value: unknown): boolean {
  // Review #1 fix #7: arrays are typeof === 'object' so the prior
  // check accepted them. Downstream `Array.isArray(.macros)` /
  // `Array.isArray(.sections)` reads catch the array case
  // defensively, but the helper's contract is "looks like a
  // sidecar dict" — tighten so the name matches the check.
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
