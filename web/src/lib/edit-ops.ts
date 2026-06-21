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
import { isReservedIdPrefix, isSystemSection } from './drag-drop';
import { editableFieldNamesForStrategy } from './editor';
import type { EngineBlock, StationTemplate } from './editor';

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

/**
 * §16.2.6 c3c — section-create (POST /sections). Backend allocates
 * a new section with `id` + optional `name` + an empty `blocks`
 * array, inserted into `layout_order` just BEFORE the system bin
 * `_unsorted` (or appended at the end if `_unsorted` is absent).
 * Replayed as `createSection(slug, sectionId, name, ...)`.
 */
export interface SectionCreateOp {
  kind: 'sectionCreate';
  sectionId: string;
  name: string | null;
}

/**
 * §16.2.6 c3c — section-delete (DELETE /sections/{id}). Per §16.2.5
 * D2, non-empty sections re-home their blocks to the `_unsorted`
 * system bin (created on demand if absent). applyEdit mirrors this
 * locally so the preview reflects the post-delete state accurately.
 * Replayed as `deleteSection(slug, sectionId, ...)`.
 */
export interface SectionDeleteOp {
  kind: 'sectionDelete';
  sectionId: string;
}

/**
 * §16.2.6 c3c — sections reorder (PUT /sections/order). The new
 * `layoutOrder` must contain exactly the same ids as the current
 * `sidecar.layout_order` (backend validates); applyEdit no-ops on
 * mismatch as a defensive guard. Replayed as
 * `reorderSections(slug, layoutOrder, ...)`.
 */
export interface ReorderSectionsOp {
  kind: 'reorderSections';
  layoutOrder: string[];
}

/**
 * §16.2.6 c3d — block move across sections (PATCH /blocks/move).
 * `address` identifies the source block via slot_index (flat_slots)
 * or block_idx (row_template); the block relocates to
 * `targetSectionId` at `targetIndex` (or appended at end if null).
 * Replayed as `moveBlock(slug, address, targetSectionId,
 * targetIndex, ...)`.
 *
 * applyEdit walks every section to find the block matching the
 * address — atomic ids are unique across sections per the §16.2.5
 * canonical-layout invariant.
 */
export interface MoveBlockOp {
  kind: 'moveBlock';
  address: BlockAddress;
  targetSectionId: string;
  targetIndex: number | null;
}

/**
 * §16.2.6 c3d — block retype (PATCH /blocks/retype). Changes the
 * template entry's `strategy` + `source` + replaces the parameter
 * fields. Affects the TEMPLATE (not the sidecar) — sidecar atomic
 * refs by slot_index stay valid because slot_index is unchanged.
 * Replayed as `retypeBlock(slug, address, newStrategy, newSource,
 * newFields, ...)`.
 */
export interface RetypeBlockOp {
  kind: 'retypeBlock';
  address: BlockAddress;
  newStrategy: string | null;
  newSource: string | null;
  newFields: Record<string, string | number | string[] | null>;
}

/**
 * §16.2.6 c3d-follow-up — structural block edit (PATCH /blocks/structure).
 * Three sub-ops discriminated by `edit.op`:
 *
 *   - `size`   — change `flat_slots[slot_index].size`. No slot-count
 *     change, no sidecar shift.
 *   - `add`    — insert `new_block` at `flat_slots[slot_index]`. Every
 *     sidecar atomic ref at slot_index >= idx shifts by +1. The new
 *     block auto-files to `_unsorted` (D2 philosophy — never leave a
 *     slot orphaned).
 *   - `remove` — pop `flat_slots[slot_index]`. The sidecar atomic ref
 *     at that slot_index is cascade-dropped first; remaining refs at
 *     slot_index > idx shift by -1. Rejected if the cascade would
 *     orphan macros (no atomic anchor would remain in a macro-only
 *     section — `resolve_macro_addresses` can't compute positions).
 *
 * row_template stations are 400'd by the backend (the block_idx-
 * domain analogue is a future slice); the frontend mirror is a no-op
 * for symmetry.
 *
 * Replayed as `structuralBlockEdit(slug, edit, ...)`.
 */
export interface StructuralBlockEditOp {
  kind: 'structuralBlockEdit';
  edit: {
    op: 'size' | 'add' | 'remove';
    slot_index: number;
    /** Required when op === 'size'. */
    new_size?: number;
    /** Required when op === 'add'. */
    new_block?: Record<string, unknown>;
  };
}

export type Edit =
  | PatchBlockOp
  | PatchMacroOp
  | RenameSectionOp
  | SectionCreateOp
  | SectionDeleteOp
  | ReorderSectionsOp
  | MoveBlockOp
  | RetypeBlockOp
  | StructuralBlockEditOp;


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
    case 'sectionCreate':
      return {
        template,
        sidecar: _sectionCreate(sidecar, op.sectionId, op.name),
      };
    case 'sectionDelete':
      return {
        template,
        sidecar: _sectionDelete(sidecar, op.sectionId),
      };
    case 'reorderSections':
      return {
        template,
        sidecar: _reorderSections(sidecar, op.layoutOrder),
      };
    case 'moveBlock':
      return {
        template,
        sidecar: _moveBlock(sidecar, op.address, op.targetSectionId, op.targetIndex),
      };
    case 'retypeBlock':
      return {
        template: _retypeBlock(template, op.address, op.newStrategy, op.newSource, op.newFields),
        sidecar,
      };
    case 'structuralBlockEdit':
      return _structuralBlockEdit(template, sidecar, op.edit);
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


/**
 * §16.2.6 c3c — append a new section to sidecar.sections + insert
 * its id into layout_order. New sections land BEFORE the system
 * bin `_unsorted` (or appended at the end if `_unsorted` is
 * absent) — mirrors the §16.2.5 `create_section` backend semantic.
 *
 * Defensive no-ops:
 *   - sidecar isn't a dict
 *   - sections or layout_order isn't an array
 *   - sectionId already exists (duplicate id)
 *
 * Returns a new sidecar dict (PURE).
 */
function _sectionCreate(
  sidecar: unknown,
  sectionId: string,
  name: string | null,
): unknown {
  if (!_isSidecarLike(sidecar)) return sidecar;
  // c3c review #1 fix #6: reject reserved-prefix ids (leading
  // underscore) — those are claimed for system bins per §16.2.5.t2.
  // EditorCanvas's Add Section input already gates on this at the
  // input layer; the local applyEdit mirroring the rejection keeps
  // the preview honest if a manual op ever bypasses the input
  // gate (e.g. a future programmatic-stage path).
  if (isReservedIdPrefix(sectionId)) return sidecar;
  const dict = sidecar as Record<string, unknown>;
  const sections = dict.sections;
  const layoutOrder = dict.layout_order;
  if (!Array.isArray(sections) || !Array.isArray(layoutOrder)) return sidecar;
  if (sections.some(
    (s) => s && typeof s === 'object' && (s as Record<string, unknown>).id === sectionId,
  )) {
    return sidecar;
  }
  const newSection: Record<string, unknown> = { id: sectionId, blocks: [] };
  if (name !== null) newSection.name = name;
  const unsortedIdx = layoutOrder.indexOf('_unsorted');
  const nextLayoutOrder = unsortedIdx >= 0
    ? [
        ...layoutOrder.slice(0, unsortedIdx),
        sectionId,
        ...layoutOrder.slice(unsortedIdx),
      ]
    : [...layoutOrder, sectionId];
  return {
    ...dict,
    sections: [...sections, newSection],
    layout_order: nextLayoutOrder,
  };
}


/**
 * §16.2.6 c3c — remove a section from sidecar.sections + layout_order.
 * Per §16.2.5 D2, non-empty sections re-home their blocks to the
 * `_unsorted` system bin (created on demand at the END of
 * layout_order if absent). Mirrored locally so preview reflects the
 * post-delete state.
 *
 * **Backend cross-check (c3c review #1 fix #2):** the local mirror
 * matches `api.routers.editor.delete_section` +
 * `engine.editor_metadata.ensure_unsorted_section` in two key
 * details:
 *   - Block order: re-homed blocks are APPENDED to the END of any
 *     existing `_unsorted.blocks` (backend uses
 *     `bin_section["blocks"].extend(moved_section.get("blocks",
 *     []))` — list-extend is append-to-end).
 *   - Auto-created `_unsorted` carries the `name` field
 *     `"Unsorted (moved from a deleted section)"`
 *     (engine.editor_metadata.UNSORTED_SECTION_NAME constant).
 *
 * If the backend semantic ever changes, the mirror needs the same
 * change — there is no auto-sync. A backend pytest pin on the order
 * + name (in test_editor_api.py) would catch drift early; not added
 * here because the constants live on the backend's side of the
 * boundary.
 *
 * Defensive no-ops:
 *   - sidecar isn't a dict
 *   - sections or layout_order isn't an array
 *   - target sectionId not found
 *   - target IS `_unsorted` (the system bin is undeletable per §16.2.5.t2;
 *     backend rejects + applyEdit must agree)
 *
 * Returns a new sidecar dict (PURE).
 */
function _sectionDelete(sidecar: unknown, sectionId: string): unknown {
  if (!_isSidecarLike(sidecar)) return sidecar;
  // The system bin is undeletable.
  if (sectionId === '_unsorted') return sidecar;
  const dict = sidecar as Record<string, unknown>;
  const sections = dict.sections;
  const layoutOrder = dict.layout_order;
  if (!Array.isArray(sections) || !Array.isArray(layoutOrder)) return sidecar;
  const target = sections.find(
    (s) => s && typeof s === 'object' && (s as Record<string, unknown>).id === sectionId,
  ) as Record<string, unknown> | undefined;
  if (!target) return sidecar;

  const targetBlocks = Array.isArray(target.blocks)
    ? (target.blocks as unknown[])
    : [];
  let nextSections = sections.filter(
    (s) => !(s && typeof s === 'object' && (s as Record<string, unknown>).id === sectionId),
  );
  let nextLayoutOrder = layoutOrder.filter((id) => id !== sectionId);

  if (targetBlocks.length > 0) {
    // Auto-_unsorted-bin: append the deleted section's blocks to
    // `_unsorted`. Create the bin if it doesn't exist.
    const unsortedIdx = nextSections.findIndex(
      (s) => s && typeof s === 'object' && (s as Record<string, unknown>).id === '_unsorted',
    );
    if (unsortedIdx >= 0) {
      const unsorted = nextSections[unsortedIdx] as Record<string, unknown>;
      const unsortedBlocks = Array.isArray(unsorted.blocks)
        ? (unsorted.blocks as unknown[])
        : [];
      const updatedUnsorted = {
        ...unsorted,
        blocks: [...unsortedBlocks, ...targetBlocks],
      };
      nextSections = [
        ...nextSections.slice(0, unsortedIdx),
        updatedUnsorted,
        ...nextSections.slice(unsortedIdx + 1),
      ];
    } else {
      // Create the bin at the end of sections + layout_order.
      // c3c review #1 fix #8: include the `name` field that the
      // backend's `ensure_unsorted_section` sets — pre-fix the
      // newly-created bin lacked a display name + the preview
      // showed an unlabeled chunk between Save attempts. Pinned
      // string matches engine.editor_metadata.UNSORTED_SECTION_NAME;
      // if the backend constant changes the frontend mirror
      // diverges until the next change here.
      nextSections = [
        ...nextSections,
        {
          id: '_unsorted',
          name: 'Unsorted (moved from a deleted section)',
          blocks: targetBlocks,
        },
      ];
      nextLayoutOrder = [...nextLayoutOrder, '_unsorted'];
    }
  }

  return {
    ...dict,
    sections: nextSections,
    layout_order: nextLayoutOrder,
  };
}


/**
 * §16.2.6 c3c — replace sidecar.layout_order with the supplied
 * order. Backend validates that the new order contains EXACTLY the
 * same ids as the current order; applyEdit no-ops on mismatch.
 *
 * Defensive no-ops:
 *   - sidecar isn't a dict
 *   - sections or layout_order isn't an array
 *   - length differs or the id set differs from current
 *
 * Returns a new sidecar dict (PURE).
 */
function _reorderSections(
  sidecar: unknown,
  layoutOrder: string[],
): unknown {
  if (!_isSidecarLike(sidecar)) return sidecar;
  const dict = sidecar as Record<string, unknown>;
  const oldOrder = dict.layout_order;
  if (!Array.isArray(oldOrder)) return sidecar;
  if (oldOrder.length !== layoutOrder.length) return sidecar;
  // c3c review #1 fix #1: the prior length + set-membership check
  // ACCEPTED an ordering with duplicate ids — e.g.
  // `['a','a','b']` against current `['a','b','c']` (same length,
  // all members in oldSet) silently dropped 'c' + duplicated 'a'.
  // Tighten to: same id SET (no dupes + exactly the old members).
  const newSet = new Set(layoutOrder);
  if (newSet.size !== layoutOrder.length) return sidecar;  // had dupes
  const oldSet = new Set(oldOrder);
  if (newSet.size !== oldSet.size) return sidecar;
  for (const id of newSet) {
    if (!oldSet.has(id)) return sidecar;
  }
  return { ...dict, layout_order: [...layoutOrder] };
}


/**
 * §16.2.6 c3d — relocate a block from its current section into
 * `targetSectionId` at `targetIndex` (or append at end if null).
 *
 * Block identity uses `address` (slot_index for flat_slots,
 * block_idx for row_template). Per §16.2.5's canonical-layout
 * invariant, each atomic ref appears in EXACTLY ONE section so the
 * address uniquely identifies the source section + block. applyEdit
 * walks sections to find the match.
 *
 * Backend semantic mirror (api.routers.editor.move_block):
 *   - Reject `_unsorted` (and any leading-underscore system bin) as
 *     a manual move target — system bins are auto-filled by section
 *     delete + structural add only (D2; backend editor.py:1140).
 *   - Reject negative `targetIndex` (backend editor.py:1208).
 *   - Find the source section by address-matching against
 *     section.blocks.
 *   - Reject when the address matches blocks in MULTIPLE sections
 *     (drift; backend editor.py:1184).
 *   - Remove the matched block from source.blocks.
 *   - Insert the block into targetSection.blocks at targetIndex
 *     (or append at end when targetIndex is null).
 *   - Moving to the SAME section + a different index is the
 *     "within-section reorder" path; backend supports it + so do we.
 *
 * Defensive no-ops (each mirrors a backend 400 so the optimistic
 * preview never stages an op that Save will reject):
 *   - sidecar isn't a dict
 *   - sections isn't an array
 *   - targetSectionId is a system bin (leading underscore)
 *   - targetIndex < 0
 *   - target section doesn't exist
 *   - source block not found in any section (address mismatch)
 *   - source block matches more than one section (sidecar drift)
 *
 * Returns a new sidecar dict (PURE).
 */
/** §16.2.6.t9 — typed local shape for the sidecar walk inside
 *  `_moveBlock`. Pre-fix the function chained `Record<string,
 *  unknown>` casts at every step (each section + block access
 *  needed a cast). These local types mirror the runtime invariants
 *  the function ALREADY checks (sec.id is a string, sec.blocks is
 *  an array, etc.) so the walk type-checks naturally without
 *  per-access widening. `_BlockDict` allows arbitrary extra fields
 *  via the index signature — defensive against future block kinds
 *  carrying new keys. */
interface _BlockDict {
  kind?: string;
  slot_index?: number;
  block_idx?: number;
  [key: string]: unknown;
}
interface _SectionDict {
  id: string;
  name?: string;
  blocks: _BlockDict[];
  [key: string]: unknown;
}

function _moveBlock(
  sidecar: unknown,
  address: BlockAddress,
  targetSectionId: string,
  targetIndex: number | null,
): unknown {
  if (!_isSidecarLike(sidecar)) return sidecar;
  // c3d sweep S1: use the narrower membership helper, mirroring the
  // backend's `is_system_section` (editor.py:1140). Earlier Batch B
  // used `startsWith('_')` (drag-drop.ts's `isReservedIdPrefix`), but
  // that helper is for CREATE-time forward-compat — at runtime it
  // would trap legacy hand-authored sidecars containing `_archive` /
  // `_specials` etc. The design (drag-drop.ts:59-71) explicitly keeps
  // those operator-manageable; only `_unsorted` is reserved today.
  if (isSystemSection(targetSectionId)) return sidecar;
  // c3d review #1 batch B finding #4: backend 400s on negative idx.
  if (targetIndex !== null && targetIndex < 0) return sidecar;
  const dict = sidecar as Record<string, unknown>;
  const rawSections = dict.sections;
  if (!Array.isArray(rawSections)) return sidecar;
  // §16.2.6.t9: narrow the sections array to `_SectionDict[]` ONCE
  // here. `applyEdit` forwards `sidecar: unknown` straight in
  // WITHOUT a shape-validation pass, so this `as` is type-level
  // convenience, NOT a load-bearing runtime assertion. The actual
  // safety net is the per-iteration runtime checks below
  // (`!sec || typeof sec !== 'object'` and `!Array.isArray(blocks)`)
  // which preserve the original defensive walk against legacy /
  // malformed sidecars. Don't drop those checks thinking the
  // narrowing protects you — it doesn't.
  const sections = rawSections as _SectionDict[];

  // c3d sweep S3: presence-only pre-check for the target section.
  if (!sections.some((s) => s && typeof s === 'object' && s.id === targetSectionId)) {
    return sidecar;
  }

  // Walk every section (NO early-out on first hit). c3d review #1
  // batch B finding #5: backend 400s when the address matches
  // multiple sections (drift rule 8). Continue scanning so we can
  // detect + no-op a multi-match — Save would otherwise reject.
  let sourceSectionIdx = -1;
  let sourceBlockIdx = -1;
  let movedBlock: _BlockDict | null = null;
  let matchCount = 0;
  for (let i = 0; i < sections.length; i++) {
    const sec = sections[i];
    if (!sec || typeof sec !== 'object') continue;
    const blocks = sec.blocks;
    if (!Array.isArray(blocks)) continue;
    for (let j = 0; j < blocks.length; j++) {
      const block = blocks[j];
      if (!block || typeof block !== 'object') continue;
      // c3d sweep S5: ATOMIC ONLY (mirrors backend
      // _matches at editor.py:1173).
      if (block.kind !== 'atomic') continue;
      // address.slot_index matches block.slot_index (flat_slots);
      // address.block_idx matches block.block_idx (row_template).
      if (
        (address.slot_index !== undefined &&
          block.slot_index === address.slot_index) ||
        (address.block_idx !== undefined &&
          block.block_idx === address.block_idx)
      ) {
        matchCount += 1;
        if (matchCount > 1) return sidecar; // drift — backend would 400.
        sourceSectionIdx = i;
        sourceBlockIdx = j;
        movedBlock = block;
      }
    }
  }
  if (movedBlock === null) return sidecar;

  // Reconstruct each section. For source: remove the block. For
  // target: insert. For same-section reorder, both happen on the
  // SAME section's blocks (handle via a single transform).
  const sourceSection = sections[sourceSectionIdx];
  const sourceBlocks = sourceSection.blocks;

  // Identify whether source === target by id (not by index — the
  // walk gave us indices but the comparison is id-based).
  const sourceSectionId = sourceSection.id;
  const isSameSection = sourceSectionId === targetSectionId;

  const nextSections = sections.map((sec, i) => {
    if (!sec || typeof sec !== 'object') return sec;
    if (isSameSection && i === sourceSectionIdx) {
      // Same-section reorder: remove + reinsert on this section's blocks.
      const withoutSource = [
        ...sourceBlocks.slice(0, sourceBlockIdx),
        ...sourceBlocks.slice(sourceBlockIdx + 1),
      ];
      const insertAt =
        targetIndex === null
          ? withoutSource.length
          : Math.max(0, Math.min(withoutSource.length, targetIndex));
      const next = [
        ...withoutSource.slice(0, insertAt),
        movedBlock!,
        ...withoutSource.slice(insertAt),
      ];
      return { ...sec, blocks: next };
    }
    if (i === sourceSectionIdx) {
      // Cross-section move: remove the block from source.
      return {
        ...sec,
        blocks: [
          ...sourceBlocks.slice(0, sourceBlockIdx),
          ...sourceBlocks.slice(sourceBlockIdx + 1),
        ],
      };
    }
    if (sec.id === targetSectionId) {
      // Cross-section move: insert into target at index.
      const tb = Array.isArray(sec.blocks) ? sec.blocks : [];
      const insertAt =
        targetIndex === null
          ? tb.length
          : Math.max(0, Math.min(tb.length, targetIndex));
      return {
        ...sec,
        blocks: [
          ...tb.slice(0, insertAt),
          movedBlock!,
          ...tb.slice(insertAt),
        ],
      };
    }
    return sec;
  });

  return { ...dict, sections: nextSections };
}


/** Structural block keys preserved across the retype's allowlist
 *  filter (mirrors backend `STRUCTURAL` set at editor.py:550). */
const _RETYPE_STRUCTURAL_KEYS: ReadonlySet<string> = new Set([
  'strategy', 'source', 'size',
]);


/**
 * §16.2.6 c3d — retype a block (PATCH /blocks/retype). Mirrors the
 * backend's full 5-step sequence (api.routers.editor.patch_block_retype):
 *
 *   1. Start from a SHALLOW COPY of the existing block (preserves
 *      `strategy`/`source`/`size`/all existing parameter fields).
 *   2. Apply `newStrategy` IF non-null — null means "don't change".
 *   3. Apply `newSource` IF non-null — same null contract.
 *   4. Merge `newFields`: null clears the key, non-null sets.
 *   5. Drop keys not in (structural ∪ EDITABLE_FIELDS[strategy]) —
 *      mirrors editor.py:551-554 so the optimistic preview drops the
 *      same stale-for-new-strategy fields the backend will.
 *
 * Batch C correction (this commit): the previous c3d-batch-A version
 * skipped step 5, which left "stale" fields (e.g. `position` on a
 * retype to `pick`) sitting in the optimistic preview until Save,
 * when the backend dropped them. Step 5 now mirrors backend behaviour
 * via `editableFieldNamesForStrategy` — both surfaces share
 * `editor.ts:EDITABLE_FIELDS` so they stay in lock-step automatically.
 *
 * Sidecar is untouched — slot_index identity is preserved through
 * the retype so existing atomic refs stay valid.
 *
 * Defensive no-ops:
 *   - layout sets both flat_slots + row_template (XOR violation)
 *   - address shape doesn't match layout form
 *   - address out of range
 *
 * Returns a new template dict (PURE).
 */
function _retypeBlock(
  template: StationTemplate,
  address: BlockAddress,
  newStrategy: string | null,
  newSource: string | null,
  newFields: Record<string, string | number | string[] | null>,
): StationTemplate {
  const layout = template.layout;
  const flat = layout.flat_slots;
  const rows = layout.row_template;

  // XOR guard — same as _patchBlock.
  if (flat !== undefined && rows !== undefined) return template;

  // Resolve the target list + index in a single typed pass — no
  // double-cast through Record<string, unknown>[] (c3d batch C
  // finding #8: both flat_slots and row_template are typed
  // EngineBlock[] already).
  let targetIdx: number;
  let targetKind: 'flat_slots' | 'row_template';
  let targetList: EngineBlock[];
  if (flat !== undefined && address.slot_index !== undefined) {
    if (address.slot_index < 0 || address.slot_index >= flat.length) {
      return template;
    }
    targetIdx = address.slot_index;
    targetKind = 'flat_slots';
    targetList = flat;
  } else if (rows !== undefined && address.block_idx !== undefined) {
    if (address.block_idx < 0 || address.block_idx >= rows.length) {
      return template;
    }
    targetIdx = address.block_idx;
    targetKind = 'row_template';
    targetList = rows;
  } else {
    return template;
  }

  // 1. Start from a shallow copy of the existing block.
  const newBlock: Record<string, unknown> = { ...targetList[targetIdx] };
  // 2-3. Apply newStrategy / newSource if non-null.
  if (newStrategy !== null) newBlock.strategy = newStrategy;
  if (newSource !== null) newBlock.source = newSource;
  // 4. Merge newFields: null clears, non-null sets.
  for (const [k, v] of Object.entries(newFields)) {
    if (v === null) delete newBlock[k];
    else newBlock[k] = v;
  }
  // 5. Drop keys not in (structural ∪ allowlist). Empty allowlist
  //    for unknown strategies (mirrors the backend's `frozenset()`
  //    default at editor.py:549) — every non-structural field drops.
  const allowed = editableFieldNamesForStrategy(
    typeof newBlock.strategy === 'string' ? newBlock.strategy : undefined,
  );
  for (const k of Object.keys(newBlock)) {
    if (_RETYPE_STRUCTURAL_KEYS.has(k) || allowed.has(k)) continue;
    delete newBlock[k];
  }

  const nextList = targetList.slice();
  // The merge built `newBlock` as a structural-shape Record (so the
  // dynamic strategy / source / field writes don't fight the
  // EngineBlock literal-union types). At assignment time the runtime
  // shape matches EngineBlock as long as `parse_template` would
  // accept it — the backend re-validates on Save, so a transient
  // mid-edit shape that doesn't fit isn't a correctness break here.
  nextList[targetIdx] = newBlock as unknown as EngineBlock;
  const nextLayout: StationTemplate['layout'] = { ...layout, [targetKind]: nextList };
  return { ...template, layout: nextLayout };
}


/**
 * §16.2.6 c3d-follow-up — shift every atomic block's `slot_index`
 * (where `slot_index >= threshold`) by `delta`. Mirrors backend
 * `engine.editor_metadata.shift_atomic_slot_indices`.
 *
 * Used by structural add (delta=+1) + remove (delta=-1) paths to
 * keep sidecar atomic refs in lockstep with the template's
 * flat_slots reshuffle. Macros derive positions implicitly via
 * `resolve_macro_addresses` (no slot_index field to shift).
 *
 * Returns a new sidecar dict (PURE).
 */
function _shiftAtomicSlotIndices(
  sidecar: unknown,
  threshold: number,
  delta: number,
): unknown {
  if (!_isSidecarLike(sidecar)) return sidecar;
  const dict = sidecar as Record<string, unknown>;
  const sections = dict.sections;
  if (!Array.isArray(sections)) return sidecar;
  const nextSections = sections.map((s) => {
    if (!s || typeof s !== 'object') return s;
    const sec = s as Record<string, unknown>;
    const blocks = sec.blocks;
    if (!Array.isArray(blocks)) return sec;
    const nextBlocks = blocks.map((b) => {
      if (!b || typeof b !== 'object') return b;
      const block = b as Record<string, unknown>;
      if (block.kind !== 'atomic') return block;
      const si = block.slot_index;
      if (typeof si !== 'number' || si < threshold) return block;
      return { ...block, slot_index: si + delta };
    });
    return { ...sec, blocks: nextBlocks };
  });
  return { ...dict, sections: nextSections };
}


/**
 * §16.2.6 c3d-follow-up — ensure `_unsorted` section exists in BOTH
 * `sections[]` AND `layout_order`. Mirrors backend
 * `engine.editor_metadata.ensure_unsorted_section`.
 *
 * Used by structural add to auto-rescue the new block's atomic ref
 * (D2 philosophy — new structural blocks land in `_unsorted` until
 * the operator moves them). The `name` matches the backend
 * `UNSORTED_SECTION_NAME` constant — see the cross-check comment in
 * `_sectionDelete` for the drift consequence.
 *
 * Heals both invariants unconditionally (matches backend c1 review
 * #5: prior version only checked sections[] presence, leaving
 * partially-mutated sidecars with orphan layout_order entries).
 *
 * Returns a new sidecar dict (PURE).
 */
function _ensureUnsortedSection(sidecar: unknown): unknown {
  if (!_isSidecarLike(sidecar)) return sidecar;
  const dict = sidecar as Record<string, unknown>;
  const sections = Array.isArray(dict.sections) ? dict.sections : [];
  const layoutOrder = Array.isArray(dict.layout_order) ? dict.layout_order : [];
  const hasSection = sections.some(
    (s) => s && typeof s === 'object' && (s as Record<string, unknown>).id === '_unsorted',
  );
  const hasOrderEntry = layoutOrder.includes('_unsorted');
  if (hasSection && hasOrderEntry) return sidecar;
  const nextSections = hasSection
    ? sections
    : [
        ...sections,
        {
          id: '_unsorted',
          name: 'Unsorted (moved from a deleted section)',
          blocks: [],
        },
      ];
  const nextLayoutOrder = hasOrderEntry
    ? layoutOrder
    : [...layoutOrder, '_unsorted'];
  return { ...dict, sections: nextSections, layout_order: nextLayoutOrder };
}


/**
 * §16.2.6 c3d-follow-up — apply one structural edit (size / add /
 * remove) to the template's flat_slots, shifting sidecar atomic
 * refs in lockstep (D5). Mirrors `api.routers.editor.structural_block_edit`.
 *
 * Three sub-ops:
 *   - `size`   — set `flat_slots[idx].size = new_size`. No sidecar shift.
 *   - `add`    — insert `new_block` at idx; shift atomic refs at
 *     slot_index >= idx by +1; ensure `_unsorted` exists; append
 *     `{kind:'atomic', slot_index: idx}` to it.
 *   - `remove` — cascade-drop the matching sidecar atomic ref;
 *     reject if removing leaves a macro-only section (orphan); pop
 *     flat_slots[idx]; shift atomic refs at slot_index > idx by -1.
 *
 * row_template stations are rejected (backend 400 — block_idx-domain
 * structural edits land in a future slice). Frontend mirrors that as
 * a no-op so the staged op doesn't lie about a successful preview
 * the Save will reject.
 *
 * Defensive no-ops (all mirror backend errors):
 *   - template.layout.flat_slots is absent (row_template station)
 *   - size: idx out of range, new_size missing or < 1
 *   - add: idx out of range [0, len], new_block missing
 *   - remove: idx out of range, orphan-macro guard fires
 *
 * Returns `{template, sidecar}` (both potentially new — PURE).
 */
function _structuralBlockEdit(
  template: StationTemplate,
  sidecar: unknown,
  edit: StructuralBlockEditOp['edit'],
): { template: StationTemplate; sidecar: unknown } {
  const layout = template.layout;
  const flat = layout.flat_slots;
  // Frontend mirror of backend's row_template 400. Whole slice no-op.
  if (!Array.isArray(flat)) return { template, sidecar };

  const idx = edit.slot_index;

  if (edit.op === 'size') {
    if (!(Number.isInteger(idx) && idx >= 0 && idx < flat.length)) {
      return { template, sidecar };
    }
    if (typeof edit.new_size !== 'number' || edit.new_size < 1) {
      return { template, sidecar };
    }
    const nextFlat = flat.slice();
    nextFlat[idx] = { ...nextFlat[idx], size: edit.new_size };
    return {
      template: { ...template, layout: { ...layout, flat_slots: nextFlat } },
      sidecar,
    };
  }

  if (edit.op === 'add') {
    if (!(Number.isInteger(idx) && idx >= 0 && idx <= flat.length)) {
      return { template, sidecar };
    }
    if (edit.new_block === undefined || edit.new_block === null) {
      return { template, sidecar };
    }
    // c3d-follow-up review F1: gate on the FULL sidecar-walk
    // precondition (object-like + sections is an array) BEFORE the
    // template insert. The cheaper `_isSidecarLike` check accepts
    // shapes like `{sections: 'not an array'}` that `_shiftAtomicSlotIndices`
    // would silently no-op on — leaving the template ahead of stale
    // refs. Other paths follow the same all-or-nothing pattern (see
    // _moveBlock). `size` is exempt because it doesn't touch sidecar.
    if (!_isSidecarLike(sidecar)) return { template, sidecar };
    if (!Array.isArray((sidecar as Record<string, unknown>).sections)) {
      return { template, sidecar };
    }
    const nextFlat = [
      ...flat.slice(0, idx),
      edit.new_block as unknown as EngineBlock,
      ...flat.slice(idx),
    ];
    // c3d-follow-up review F2: collapse the three sequential
    // sections[] rebuilds (shift / ensure / auto-rescue append)
    // into a single pass. Backend does it in one in-place mutation
    // pass; the previous functional version walked sections.length
    // three times (~3x allocations vs the minimum). At scale (50+
    // sections) the perf difference is small, but the single pass
    // also reads better: each section gets ONE transform that does
    // everything it needs.
    const sd = sidecar as Record<string, unknown>;
    const sections = sd.sections as unknown[];
    const layoutOrder = Array.isArray(sd.layout_order)
      ? (sd.layout_order as unknown[])
      : [];
    let sawUnsorted = false;
    const newRef = { kind: 'atomic', slot_index: idx };
    const nextSections = sections.map((s) => {
      if (!s || typeof s !== 'object') return s;
      const sec = s as Record<string, unknown>;
      const bs = Array.isArray(sec.blocks) ? (sec.blocks as unknown[]) : [];
      // Shift atomic refs at slot_index >= idx by +1.
      const shifted = bs.map((b) => {
        if (!b || typeof b !== 'object') return b;
        const block = b as Record<string, unknown>;
        if (block.kind !== 'atomic') return block;
        const si = block.slot_index;
        if (typeof si !== 'number' || si < idx) return block;
        return { ...block, slot_index: si + 1 };
      });
      // _unsorted: also append the new ref in the same pass.
      if (sec.id === '_unsorted') {
        sawUnsorted = true;
        return { ...sec, blocks: [...shifted, newRef] };
      }
      return { ...sec, blocks: shifted };
    });
    // _unsorted absent → create it at end of sections + layout_order
    // (mirrors _ensureUnsortedSection, but folded into this pass).
    const finalSections = sawUnsorted
      ? nextSections
      : [
          ...nextSections,
          {
            id: '_unsorted',
            name: 'Unsorted (moved from a deleted section)',
            blocks: [newRef],
          },
        ];
    const finalLayoutOrder = layoutOrder.includes('_unsorted')
      ? layoutOrder
      : [...layoutOrder, '_unsorted'];
    return {
      template: { ...template, layout: { ...layout, flat_slots: nextFlat } },
      sidecar: { ...sd, sections: finalSections, layout_order: finalLayoutOrder },
    };
  }

  // op === 'remove'
  if (!(Number.isInteger(idx) && idx >= 0 && idx < flat.length)) {
    return { template, sidecar };
  }
  // c3d-follow-up review F1: same all-or-nothing gate as `add` —
  // both _isSidecarLike + sections-is-array. Without this, a
  // non-sidecar input would still pop flat_slots while every
  // sidecar-side adjustment silently no-oped.
  if (!_isSidecarLike(sidecar)) return { template, sidecar };
  if (!Array.isArray((sidecar as Record<string, unknown>).sections)) {
    return { template, sidecar };
  }
  // Cascade-drop the matching sidecar atomic ref. Track the touched
  // section so we can run the orphan-macro guard against its post-
  // cascade state (mirrors backend editor.py:1311-1325).
  let touchedSectionId: string | null = null;
  let nextSidecar = sidecar;
  if (_isSidecarLike(nextSidecar)) {
    const sd = nextSidecar as Record<string, unknown>;
    const secs = sd.sections as unknown[];
    if (Array.isArray(secs)) {
      let foundOnce = false;
      const nextSecs = secs.map((s) => {
        if (foundOnce || !s || typeof s !== 'object') return s;
        const sec = s as Record<string, unknown>;
        const bs = Array.isArray(sec.blocks) ? (sec.blocks as unknown[]) : [];
        const hitIdx = bs.findIndex(
          (b) =>
            b !== null &&
            typeof b === 'object' &&
            (b as Record<string, unknown>).kind === 'atomic' &&
            (b as Record<string, unknown>).slot_index === idx,
        );
        if (hitIdx < 0) return sec;
        foundOnce = true;
        touchedSectionId = sec.id as string;
        return {
          ...sec,
          blocks: [...bs.slice(0, hitIdx), ...bs.slice(hitIdx + 1)],
        };
      });
      nextSidecar = { ...sd, sections: nextSecs };
    }
  }
  // Orphan-macro guard: if the cascade emptied the section of atomic
  // anchors but left macros, those macros would become unreachable
  // (resolve_macro_addresses silently skips macro-only sections).
  // Mirrors backend editor.py:1337-1360 — the op no-ops here so the
  // staged preview doesn't show a remove that Save will 400.
  if (touchedSectionId !== null && _isSidecarLike(nextSidecar)) {
    const sd = nextSidecar as Record<string, unknown>;
    const touched = (sd.sections as unknown[]).find(
      (s) => s && typeof s === 'object' && (s as Record<string, unknown>).id === touchedSectionId,
    ) as Record<string, unknown> | undefined;
    if (touched) {
      // c3d-follow-up review F4: widen the element type so the
      // `b?.kind` optional chaining stops looking redundant — block
      // entries CAN be null after a partial-walk no-op leaves the
      // original (untrusted) sidecar shape.
      const remaining = Array.isArray(touched.blocks)
        ? (touched.blocks as Array<Record<string, unknown> | null>)
        : [];
      const hasAtomicAfter = remaining.some((b) => b?.kind === 'atomic');
      const hasMacroAfter = remaining.some((b) => b?.kind === 'macro');
      if (!hasAtomicAfter && hasMacroAfter) {
        // Roll back — return the ORIGINAL sidecar + template (no shift,
        // no pop). Op stages but applies to nothing.
        return { template, sidecar };
      }
    }
  }
  // Pop flat_slots[idx].
  const nextFlat = [...flat.slice(0, idx), ...flat.slice(idx + 1)];
  // Shift atomic refs at slot_index > idx by -1.
  nextSidecar = _shiftAtomicSlotIndices(nextSidecar, idx + 1, -1);
  return {
    template: { ...template, layout: { ...layout, flat_slots: nextFlat } },
    sidecar: nextSidecar,
  };
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


/**
 * §16.2.6 c3b review #1 batch C — Save replay as a pure function.
 *
 * Extracted from the visualise page's `handleStagedSave` so the
 * dispatch + error handling are TESTABLE without rendering the
 * page (Svelte 4's component-event-via-DOM limitation flagged in
 * c3 batch B blocks the equivalent page-level integration test).
 *
 * The caller injects the PATCH callables + the "refresh after
 * each successful PATCH" hook via `deps`. Returns a structured
 * result the caller maps to local UI state — never sets state
 * directly.
 *
 * **What's tested via this extraction:**
 * - Dispatch correctness: each Edit kind routes to the matching
 *   deps callable (a regression to the switch case order or
 *   signature is caught immediately).
 * - Order: ops execute in queue order; refresh happens between
 *   each successful PATCH.
 * - Failure stops the loop + reports the 1-indexed edit number +
 *   total + remaining queue (so the caller can show "edit K of N").
 * - 409-via-isConflict callback distinguishes conflict from
 *   generic error so the caller's banner branches correctly.
 *
 * **What stays in the page** (not in this function):
 * - The `saving` flag.
 * - The mapping from ReplayResult → saveError / conflict / etc.
 * - The _schedulePreview() trigger after success.
 */
export interface ReplayDeps {
  patchBlock: (
    address: BlockAddress,
    fields: Record<string, string | number | string[] | null>,
  ) => Promise<unknown>;
  patchMacro: (
    macroId: string,
    fields: Record<string, string | number | string[] | null>,
  ) => Promise<unknown>;
  renameSection: (sectionId: string, name: string) => Promise<unknown>;
  // §16.2.6 c3c — section-level structural kinds.
  createSection: (sectionId: string, name: string | null) => Promise<unknown>;
  deleteSection: (sectionId: string) => Promise<unknown>;
  reorderSections: (layoutOrder: string[]) => Promise<unknown>;
  // §16.2.6 c3d — block-level structural kinds (excl. structural
  // edit + index shifts; that's c3d-follow-up).
  moveBlock: (
    address: BlockAddress,
    targetSectionId: string,
    targetIndex: number | null,
  ) => Promise<unknown>;
  retypeBlock: (
    address: BlockAddress,
    newStrategy: string | null,
    newSource: string | null,
    newFields: Record<string, string | number | string[] | null>,
  ) => Promise<unknown>;
  // §16.2.6 c3d-follow-up — structural block edit (size / add /
  // remove with slot_index shifts). Closes the 9th + final staging
  // intercept kind.
  structuralBlockEdit: (
    edit: StructuralBlockEditOp['edit'],
  ) => Promise<unknown>;
  /** Called after each successful PATCH so the next op's
   *  optimistic-concurrency token is fresh. */
  refreshUpdatedAt: () => Promise<void>;
  /** Distinguishes 409 conflicts from generic errors. The caller
   *  imports BlockPatchConflict + passes the check; tests pass a
   *  simple predicate. */
  isConflict: (err: unknown) => boolean;
}

export type ReplayResult =
  | { success: true; totalEdits: number; remaining: Edit[] }
  | {
      success: false;
      totalEdits: number;
      /** 1-indexed position of the edit that failed. */
      failedAtIdx: number;
      remaining: Edit[];
    } & ({ conflict: true } | { conflict?: false; error: string });

export async function replayOpLog(
  ops: Edit[],
  deps: ReplayDeps,
): Promise<ReplayResult> {
  const totalEdits = ops.length;
  let remaining = ops.slice();
  let currentIdx = 0;
  while (remaining.length > 0) {
    const op = remaining[0];
    currentIdx += 1;
    try {
      switch (op.kind) {
        case 'patchBlock':
          await deps.patchBlock(op.address, op.fields);
          break;
        case 'patchMacro':
          await deps.patchMacro(op.macroId, op.fields);
          break;
        case 'renameSection':
          await deps.renameSection(op.sectionId, op.name);
          break;
        case 'sectionCreate':
          await deps.createSection(op.sectionId, op.name);
          break;
        case 'sectionDelete':
          await deps.deleteSection(op.sectionId);
          break;
        case 'reorderSections':
          await deps.reorderSections(op.layoutOrder);
          break;
        case 'moveBlock':
          await deps.moveBlock(op.address, op.targetSectionId, op.targetIndex);
          break;
        case 'retypeBlock':
          await deps.retypeBlock(
            op.address, op.newStrategy, op.newSource, op.newFields,
          );
          break;
        case 'structuralBlockEdit':
          await deps.structuralBlockEdit(op.edit);
          break;
        default: {
          // Same exhaustiveness pin as applyEdit's switch — a
          // future Edit kind without a dispatch case fails compile.
          const _exhaustive: never = op;
          void _exhaustive;
          throw new Error(
            `replayOpLog: unknown op kind '${(op as Edit).kind}'`,
          );
        }
      }
    } catch (err) {
      if (deps.isConflict(err)) {
        return {
          success: false,
          totalEdits,
          failedAtIdx: currentIdx,
          remaining,
          conflict: true,
        };
      }
      return {
        success: false,
        totalEdits,
        failedAtIdx: currentIdx,
        remaining,
        error: err instanceof Error ? err.message : String(err),
      };
    }
    await deps.refreshUpdatedAt();
    remaining = remaining.slice(1);
  }
  return { success: true, totalEdits, remaining };
}


function _isSidecarLike(value: unknown): boolean {
  // Review #1 fix #7: arrays are typeof === 'object' so the prior
  // check accepted them. Downstream `Array.isArray(.macros)` /
  // `Array.isArray(.sections)` reads catch the array case
  // defensively, but the helper's contract is "looks like a
  // sidecar dict" — tighten so the name matches the check.
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}


// ---- §16.2.7.t6 — direct-attribution helper for "revert this slot" ----


/**
 * §16.2.7.t6 — return the indices into `opLog` whose direct target
 * is `slotIndex`. DIRECT matches only:
 *
 *   - patchBlock: `op.address.slot_index === slotIndex`.
 *   - retypeBlock: `op.address.slot_index === slotIndex`.
 *   - moveBlock: `op.address.slot_index === slotIndex` (the SOURCE
 *     slot; target attribution is order-dependent and out of scope).
 *   - structuralBlockEdit: `op.edit.slot_index === slotIndex`.
 *   - other kinds (sectionCreate/Delete/Rename/Reorder, patchMacro):
 *     never match.
 *
 * CASCADE cases are EXPLICITLY out of scope (D5 of the design pass).
 * A structural-add at slot=0 shifts every later slot's content — the
 * actionable edit for "revert the change to slot 47" is the
 * structural-add, not slot 47 itself. The page handler shows a
 * disabled "Revert" button on cascade-CHANGED rows; this attribution
 * function returns an empty array for them.
 *
 * Pure function — no DOM, no state. Vitest-testable end to end.
 */
export function attributeOpsToSlot(
  opLog: Edit[], slotIndex: number,
): number[] {
  const out: number[] = [];
  for (let i = 0; i < opLog.length; i++) {
    const op = opLog[i];
    if (
      (op.kind === 'patchBlock' || op.kind === 'retypeBlock' ||
       op.kind === 'moveBlock') &&
      op.address.slot_index === slotIndex
    ) {
      out.push(i);
      continue;
    }
    if (op.kind === 'structuralBlockEdit' &&
        op.edit.slot_index === slotIndex) {
      out.push(i);
      continue;
    }
  }
  return out;
}
