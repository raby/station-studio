/**
 * Undoing a queued change to a station's rules, for the week page and
 * the station editor alike.
 *
 * Each queued edit was made on top of the ones before it, so undoing one
 * can leave a later one changing a different rule, or failing to save
 * (`undoSet` says which). Those go too, but only once the operator has
 * seen them and said yes.
 */
import { confirmDialog } from '$lib/confirm';
import { undoSet, type Edit } from '$lib/edit-ops';
import type { Sidecar, StationTemplate } from '$lib/editor';
import { describeQueued, queuedChanges, type RuleDefaults } from '$lib/songList';

/**
 * The queued edits undoing `indices` takes out (ascending): those, and
 * any later ones built on them, once the operator says yes to those.
 * Null when they say no, or there's nothing to undo. `drop`: it's a
 * failed save's edit being dropped, so the question says so.
 */
export async function confirmUndo(
  opLog: Edit[],
  template: StationTemplate,
  sidecar: Sidecar,
  defaults: RuleDefaults,
  indices: number[],
  { drop = false }: { drop?: boolean } = {},
): Promise<number[] | null> {
  const all = undoSet(opLog, template, sidecar, indices);
  if (all.length === 0) return null;
  const asked = new Set(indices);
  const later = new Set(all.filter((i) => !asked.has(i)));
  if (later.size === 0) return all;

  // As the list says them: a whole change once, else each edit.
  const changes = queuedChanges(opLog, template, sidecar, defaults);
  const lines = describeQueued(opLog, template, sidecar, defaults);
  const say = (these: Set<number>): string[] =>
    changes.flatMap((change) => {
      const going = change.indices.filter((i) => these.has(i));
      if (going.length === 0) return [];
      return going.length === change.indices.length ? [change.text] : going.map((i) => lines[i]);
    });
  const undoing = say(asked);
  const built = say(later);
  const one = built.length === 1;
  const total = undoing.length + built.length;
  const [Verb, verbing] = drop ? ['Drop', 'Dropping'] : ['Undo', 'Undoing'];
  const ok = await confirmDialog({
    title: one ? `${Verb} a later change too?` : `${Verb} ${built.length} later changes too?`,
    body:
      `${verbing}: ${undoing.join('; ')}\n\n` +
      `${one ? 'This later change builds' : 'These later changes build'} on it, and without it ` +
      `would change a different rule or fail to save:\n\n` +
      built.map((line) => `• ${line}`).join('\n'),
    confirmLabel: total === 2 ? `${Verb} both` : `${Verb} all ${total}`,
  });
  return ok ? all : null;
}

/** Whether taking these edits out adds or removes a rule: the rules
 *  after it are then at other slots (so a panel open on a slot may be
 *  showing a different rule). */
export function movesRules(opLog: Edit[], indices: number[]): boolean {
  return indices.some((i) => {
    const op = opLog[i];
    return op?.kind === 'structuralBlockEdit' && op.edit.op !== 'size';
  });
}
