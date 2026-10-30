/**
 * Rules exactly like another one, so a change can go to all of them at
 * once. A station often repeats one rule many times (the starter has 21
 * identical "7 × weighted" macros, one per segment); changing just one
 * moves a seventh of what the operator usually means.
 *
 * "Exactly like" is same kind and same settings: a macro matches other
 * macros with the same spec (any repeat), a single block matches other
 * single blocks with the same spec. A slot inside a macro is never a
 * match: patching it alone would make it drift from its macro.
 */
import {
  lookupEngineBlock,
  lookupMacro,
  type EngineBlock,
  type ExpandedRef,
  type Sidecar,
  type StationTemplate,
} from '$lib/editor';

export interface IdenticalRules {
  /** The other single blocks (flat slots, or blocks of the repeating row). */
  refs: ExpandedRef[];
  /** The other macros. */
  macroIds: string[];
  /** How many songs a week those rules make between them. */
  songs: number;
}

const NONE: IdenticalRules = { refs: [], macroIds: [], songs: 0 };

/** The block as text with its keys in order, so equal settings compare
 *  equal however the template's JSON was written. */
function canonical(block: EngineBlock): string {
  const sort = (v: unknown): unknown =>
    Array.isArray(v)
      ? v.map(sort)
      : v !== null && typeof v === 'object'
        ? Object.fromEntries(
            Object.keys(v as object).sort().map((k) => [k, sort((v as Record<string, unknown>)[k])]),
          )
        : v;
  return JSON.stringify(sort(block));
}

/** Rows a week has in a repeating-row station (one per day, say). */
function rowsPerWeek(template: StationTemplate): number {
  const layout = template.layout as { row_start_offsets?: unknown[]; unit_labels?: unknown[] };
  return Math.max(1, layout.row_start_offsets?.length ?? layout.unit_labels?.length ?? 1);
}

export function identicalRules(
  template: StationTemplate,
  sidecar: Sidecar,
  ref: ExpandedRef,
): IdenticalRules {
  if (ref.kind === 'macro-placeholder') {
    const macro = lookupMacro(sidecar, ref.macro_id);
    if (!macro) return NONE;
    const key = canonical(macro.spec);
    const twins = (sidecar.macros ?? []).filter((m) => m.id !== macro.id && canonical(m.spec) === key);
    return {
      refs: [],
      macroIds: twins.map((m) => m.id),
      songs: twins.reduce((n, m) => n + m.repeat * m.spec.size, 0),
    };
  }

  const block = lookupEngineBlock(ref, template.layout);
  if (!block) return NONE;
  const key = canonical(block);
  const flat = ref.slot_index !== undefined;
  const perWeek = flat ? 1 : rowsPerWeek(template);
  const refs: ExpandedRef[] = [];
  const seen = new Set<number>();
  let songs = 0;
  for (const section of sidecar.sections) {
    for (const b of section.blocks) {
      if (b.kind !== 'atomic') continue;
      const at = flat ? b.slot_index : b.block_idx;
      if (at === undefined || seen.has(at)) continue;
      if (at === (flat ? ref.slot_index : ref.block_idx)) continue;
      seen.add(at);
      const other: ExpandedRef = flat
        ? { kind: 'atomic-ref', slot_index: at }
        : { kind: 'atomic-ref', row_idx: 0, block_idx: at };
      const spec = lookupEngineBlock(other, template.layout);
      if (spec && canonical(spec) === key) {
        refs.push(other);
        songs += spec.size * perWeek;
      }
    }
  }
  return { refs, macroIds: [], songs };
}
