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
 * c3b extends the union with the other 8 PATCH-endpoint variants.
 */
export interface PatchBlockOp {
  kind: 'patchBlock';
  address: BlockAddress;
  fields: Record<string, string | number | string[] | null>;
}

export type Edit = PatchBlockOp;


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
  if (op.kind === 'patchBlock') {
    const nextTemplate = _patchBlock(template, op.address, op.fields);
    return { template: nextTemplate, sidecar };
  }
  // c3b will add the exhaustiveness pin (`const _: never = op`)
  // once the Edit union has 2+ kinds — TS doesn't narrow a single-
  // kind union to never after the matching branch returns, so the
  // assert would error today. Until c3b: this fall-through is
  // unreachable (Edit = PatchBlockOp).
  return { template, sidecar };
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

  // Build the new block dict by merging fields. null removes.
  const oldBlock = targetList[targetIdx];
  const newBlock: Record<string, unknown> = { ...oldBlock };
  for (const [k, v] of Object.entries(fields)) {
    if (v === null) delete newBlock[k];
    else newBlock[k] = v;
  }

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
