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

export type Edit =
  | PatchBlockOp
  | PatchMacroOp
  | RenameSectionOp
  | SectionCreateOp
  | SectionDeleteOp
  | ReorderSectionsOp;


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
      nextSections = [
        ...nextSections,
        { id: '_unsorted', blocks: targetBlocks },
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
  const oldSet = new Set(oldOrder);
  if (!layoutOrder.every((id) => oldSet.has(id))) return sidecar;
  return { ...dict, layout_order: [...layoutOrder] };
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
