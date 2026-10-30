/**
 * The rule panel (DetailPanel) reports what the operator did as events;
 * these turn them into queued edits (`Edit`s) for a staged-edit session.
 *
 * Used by the week page's song list. The station editor page builds the
 * same edits inline (it also has a save-immediately mode).
 */
import type { BlockAddress, StructuralBlockEdit } from '$lib/api';
import type { ExpandedRef } from '$lib/editor';
import type { Edit } from '$lib/edit-ops';

type FieldValue = string | number | string[] | null;

/** The storage address of a single block, or null for a macro (macros are
 *  edited through their own id). In a repeating row the address is the
 *  block's place in the row: editing it edits every row. */
export function refToAddress(ref: ExpandedRef): BlockAddress | null {
  if (ref.kind !== 'atomic-ref') return null;
  if (ref.slot_index !== undefined) return { slot_index: ref.slot_index };
  if (ref.block_idx !== undefined) return { block_idx: ref.block_idx };
  return null;
}

/** The panel's events, by name, with what each carries. `also` /
 *  `alsoMacroIds`: the identical rules the operator asked to change
 *  too (identicalRules). */
export type PanelEvent =
  | { type: 'save'; detail: { ref: ExpandedRef; fields: Record<string, FieldValue>; also?: ExpandedRef[] } }
  | {
      type: 'retype';
      detail: {
        ref: ExpandedRef;
        newStrategy: string | null;
        newSource: string | null;
        newFields: Record<string, FieldValue>;
        also?: ExpandedRef[];
      };
    }
  | { type: 'macroSave'; detail: { macroId: string; fields: Record<string, FieldValue>; alsoMacroIds?: string[] } }
  | { type: 'blockMove'; detail: { ref: ExpandedRef; targetSectionId: string; targetIndex: number | null } }
  | { type: 'structuralEdit'; detail: { edit: StructuralBlockEdit } };

/**
 * The edit a panel event asks for, or null when there's nothing to queue:
 * a save with no fields changed, or a block with no address. Fields and
 * addresses are copied so later changes to the event can't alter the
 * queued edit.
 */
export function editFromPanel(event: PanelEvent): Edit | null {
  switch (event.type) {
    case 'save': {
      const address = refToAddress(event.detail.ref);
      if (address === null || Object.keys(event.detail.fields).length === 0) return null;
      return { kind: 'patchBlock', address: { ...address }, fields: { ...event.detail.fields } };
    }
    case 'retype': {
      const address = refToAddress(event.detail.ref);
      if (address === null) return null;
      return {
        kind: 'retypeBlock',
        address: { ...address },
        newStrategy: event.detail.newStrategy,
        newSource: event.detail.newSource,
        newFields: { ...event.detail.newFields },
      };
    }
    case 'macroSave':
      if (Object.keys(event.detail.fields).length === 0) return null;
      return { kind: 'patchMacro', macroId: event.detail.macroId, fields: { ...event.detail.fields } };
    case 'blockMove': {
      const address = refToAddress(event.detail.ref);
      if (address === null) return null;
      return {
        kind: 'moveBlock',
        address: { ...address },
        targetSectionId: event.detail.targetSectionId,
        targetIndex: event.detail.targetIndex,
      };
    }
    case 'structuralEdit':
      return { kind: 'structuralBlockEdit', edit: { ...event.detail.edit } };
  }
}

/**
 * Every edit a panel event asks for: the rule's own (editFromPanel),
 * then the same change to each identical rule the operator ticked, in
 * place order. Empty when there's nothing to queue.
 */
export function editsFromPanel(event: PanelEvent): Edit[] {
  const first = editFromPanel(event);
  if (first === null) return [];
  const rest: (Edit | null)[] = [];
  switch (event.type) {
    case 'save':
      for (const ref of event.detail.also ?? []) {
        rest.push(editFromPanel({ type: 'save', detail: { ref, fields: event.detail.fields } }));
      }
      break;
    case 'retype':
      for (const ref of event.detail.also ?? []) {
        rest.push(editFromPanel({ type: 'retype', detail: { ...event.detail, ref, also: [] } }));
      }
      break;
    case 'macroSave':
      for (const macroId of event.detail.alsoMacroIds ?? []) {
        rest.push(editFromPanel({ type: 'macroSave', detail: { macroId, fields: event.detail.fields } }));
      }
      break;
  }
  return [first, ...rest.filter((e): e is Edit => e !== null)];
}
