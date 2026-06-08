/**
 * Shared drag-drop primitives for the visual block editor.
 *
 * Centralises the wire shape that travels through DataTransfer +
 * the policy that decides which sections are valid drop targets.
 * Single source-of-truth so the Chip source, SectionHeader target,
 * and the DetailPanel's dropdown all agree.
 */
import type { Sidecar, SidecarSection } from './editor';


/**
 * Custom MIME for chip-ref drag payloads. Browsers normalise MIMEs
 * to lowercase in the DataTransfer.types list; matching is
 * case-insensitive but we use lowercase to match what the browser
 * returns.
 *
 * Was duplicated in Chip.svelte + SectionHeader.svelte; c2 review
 * #5 hoisted to a single constant so the source/target pair can't
 * drift apart (a typo on one side would silently break drops
 * everywhere with no compile error).
 */
export const CHIP_DRAG_MIME = 'application/x-station-chip-ref';

/**
 * Custom MIME for whole-section reorder payloads. SectionHeader is
 * both a drag SOURCE (drag the header bar to reorder) and a drop
 * TARGET (a chip OR another section dropped on it). The MIME
 * discriminates so the drop handler routes to the right dispatch
 * (chipDrop vs sectionDrop).
 *
 * §16.2.5.t1 — section-header drag-reorder. Drop semantics: dropped-
 * section moves to BEFORE the dropped-on section in layout_order.
 * The system bin (`_unsorted`) is neither a valid drag source NOR a
 * valid drop target — `canDropOn` filters target; the source is
 * gated by SectionHeader's `isSystemBin` check (no drag handle).
 */
export const SECTION_DRAG_MIME = 'application/x-station-section-ref';


/**
 * Decide whether a chip can be dropped into / moved into `target`.
 *
 * The system bin (`_unsorted`) is reserved for AUTO-moves on
 * section delete (D2). Manual moves — whether via drag-drop or the
 * DetailPanel's Move dropdown — must NOT route there; otherwise
 * the bin becomes a generic catch-all and the visual hint of
 * "these are orphans that lost their section" is lost.
 *
 * c2 review #3 fix #1: the prior implementation enforced this
 * filter on the dropdown only — drag-drop bypassed it. This helper
 * is now the single policy gate; both surfaces call it.
 */
export function canDropOn(target: SidecarSection): boolean {
  return !target.id.startsWith('_');
}


/**
 * `canDropOn` adapted for the move-dropdown's filter list. Reuses
 * the same predicate + adds a source-section exclusion (operator
 * can't pick their CURRENT section as a target — that would be a
 * no-op move).
 */
export function moveTargets(
  sidecar: Sidecar,
  sourceSectionId: string | null,
): SidecarSection[] {
  return sidecar.sections.filter(
    (s) => s.id !== sourceSectionId && canDropOn(s),
  );
}
