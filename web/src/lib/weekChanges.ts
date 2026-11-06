/**
 * Just-this-week changes on the week page: one song of the week changed
 * for that week only (engine/week_changes.py, api/routers/week_changes.py).
 *
 *  - `itemFor`: the change's address for a song of the generated week.
 *  - `changedRule`: the rule as a panel change (patchBlock / retypeBlock)
 *    makes it, for a just-this-song rule change, worked out as the
 *    station's own save would (applyEdits).
 *  - `changesAfter`: a week's changes moved through queued edits, as the
 *    server moves them on save (engine/week_change_store.py
 *    `follow_rule_ops`), for a preview of the queue.
 *  - `canChangeJustThisWeek`: whether, and if not why not, a song can be
 *    changed just this week now.
 */
import type { Trace, TraceSlot, WeekChange, WeekChangeItem } from '$lib/api';
import { applyEdits, slotAfter, type Edit, type PatchBlockOp, type RetypeBlockOp } from '$lib/edit-ops';
import type { Sidecar, StationTemplate } from '$lib/editor';
import { weekMatchesRules } from '$lib/songList';

/** The change address of the song at `position` (from 1) in `trace`:
 *  its row, its rule's place, and which of that rule's songs it is. */
export function itemFor(
  trace: Trace,
  position: number,
): Pick<WeekChangeItem, 'row' | 'block' | 'k' | 'rule_strategy' | 'rule_source'> | null {
  const slot = trace.slots[position - 1];
  if (!slot) return null;
  let k = 0;
  for (let i = 0; i < position - 1; i++) {
    const s = trace.slots[i];
    if (s.row === slot.row && s.block === slot.block) k++;
  }
  return { row: slot.row, block: slot.block, k, rule_strategy: slot.strategy, rule_source: slot.source };
}

function rules(template: StationTemplate): Record<string, unknown>[] {
  const layout = template.layout as { flat_slots?: unknown; row_template?: unknown };
  const rows = Array.isArray(layout.row_template) && layout.row_template.length > 0;
  const list = rows ? layout.row_template : layout.flat_slots;
  return Array.isArray(list) ? (list as Record<string, unknown>[]) : [];
}

/** The rule the edit makes of the station's rule at the edit's address,
 *  as a whole block, or null when the edit isn't of one rule. Its size is
 *  the rule's own: a just-this-week change keeps the week's shape. */
export function changedRule(
  template: StationTemplate,
  sidecar: Sidecar,
  edit: PatchBlockOp | RetypeBlockOp,
): Record<string, unknown> | null {
  const p = edit.address.slot_index ?? edit.address.block_idx;
  if (p === undefined || p === null) return null;
  const before = rules(template)[p];
  if (!before) return null;
  let after: Record<string, unknown> | undefined;
  try {
    after = rules(applyEdits(template, sidecar, [edit]).template)[p];
  } catch {
    return null;
  }
  if (!after) return null;
  return { ...after, size: before.size };
}

/** What `rule` changes of the station's rule number `block` (from 1):
 *  each field set differently, null for one it leaves out. The twin of
 *  the server's `changed_fields` (size never counts). */
export function changedFields(
  template: StationTemplate,
  block: number,
  rule: Record<string, unknown>,
): Record<string, unknown> {
  const orig = rules(template)[block - 1] ?? {};
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(rule)) {
    if (k !== 'size' && JSON.stringify(orig[k]) !== JSON.stringify(v)) out[k] = v;
  }
  for (const k of Object.keys(orig)) if (k !== 'size' && !(k in rule)) out[k] = null;
  return out;
}

/**
 * The week's changes after the queued edits: each follows its rule to
 * where the edits put it, as the server moves them when the edits are
 * saved. One whose rule an edit removes, or whose song a smaller rule
 * no longer has, is left out (the server deletes it). Its rule changing
 * kind or source isn't followed here: the preview drops it then, and
 * says why, as generating the week would.
 */
export function changesAfter<T extends Pick<WeekChangeItem, 'block' | 'k'>>(items: T[], opLog: Edit[]): T[] {
  if (opLog.length === 0) return items;
  const out: T[] = [];
  for (const item of items) {
    let p: number | undefined = item.block - 1;
    for (const op of opLog) {
      if (p === undefined) break;
      if (op.kind === 'structuralBlockEdit' && op.edit.op === 'size' && op.edit.slot_index === p
        && item.k >= (op.edit.new_size ?? 0)) {
        p = undefined;
      } else {
        p = slotAfter(op, p);
      }
    }
    if (p !== undefined) out.push(p === item.block - 1 ? item : { ...item, block: p + 1 });
  }
  return out;
}

/** The items to send a preview, from the stored changes. */
export function itemsOf(changes: WeekChange[]): WeekChangeItem[] {
  return changes.map((c) => ({
    row: c.row,
    block: c.block,
    k: c.k,
    kind: c.kind,
    track_id: c.kind === 'swap' ? c.track_id : null,
    rule: c.kind === 'rule' ? c.rule : null,
    rule_strategy: c.rule_strategy,
    rule_source: c.rule_source,
  }));
}

/** Why the song can't be changed just this week now, or null when it
 *  can. The server checks the same and says the same. */
export function whyNotJustThisWeek(opts: {
  trace: Trace;
  template: StationTemplate;
  slot: TraceSlot | undefined;
  viewingCurrent: boolean;
  queued: number;
}): string | null {
  const { trace, template, slot, viewingCurrent, queued } = opts;
  if (!viewingCurrent) return 'Only the current version of the week can be changed.';
  if (!slot) return 'That song is no longer in the week.';
  if (queued > 0) {
    return 'Save or discard the rule changes queued for every week first: a song is changed just this week on the week as saved.';
  }
  if (!weekMatchesRules(trace, template)) {
    return "This week was made before the station's rules last changed: regenerate it first.";
  }
  return null;
}

/** A just-this-week change in words, for lists and notes. */
export function changeWords(change: { kind: string; track?: { name: string; artist: string | null } | null;
  fields?: Record<string, unknown> | null }): string {
  if (change.kind === 'swap') {
    const t = change.track;
    if (!t) return 'Swapped for a song no longer in the library';
    return `Swapped for ${t.name}${t.artist ? ` by ${t.artist}` : ''}`;
  }
  const f = change.fields ?? {};
  const order = Object.keys(f).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  if (order.length === 0) return 'Picked by its rule changed';
  return `Picked by its rule changed: ${order.map((k) => (f[k] === null ? `no ${k}` : `${k} ${fmt(f[k])}`)).join(', ')}`;
}

function rank(k: string): number {
  return k === 'source' ? 0 : k === 'strategy' ? 1 : 2;
}
function fmt(v: unknown): string {
  return Array.isArray(v) ? v.join(', ') : String(v);
}

/** Where the `k`-th song of the rule at (`row`, `block`) plays in
 *  `trace` (from 1), or null when it has no such song. */
export function positionOf(trace: Trace, row: number, block: number, k: number): number | null {
  let seen = 0;
  for (let i = 0; i < trace.slots.length; i++) {
    const s = trace.slots[i];
    if (s.row === row && s.block === block) {
      if (seen === k) return i + 1;
      seen++;
    }
  }
  return null;
}

/** "song 7 (Tue)": a song of the week, by its place and day. */
export function songLabel(trace: Trace, position: number | null): string {
  if (position === null) return 'a song no longer in the week';
  const unit = trace.slots[position - 1]?.unit;
  return `song ${position}${unit ? ` (${unit})` : ''}`;
}
