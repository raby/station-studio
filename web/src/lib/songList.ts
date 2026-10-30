/**
 * The song list: a week's songs, each with the rule that put it there
 * and why, in the order they play, under the station's section names.
 *
 * `ruleText` says what a block (rule) does in plain words. `songGroups`
 * turns a trace into runs of songs that share a section. Runs, not one
 * group per section: in a station built from a repeating row the same
 * section comes round every row, and the list keeps playing order.
 */
import type { Trace, TraceSlot } from '$lib/api';
import {
  classifySlots,
  findChipRef,
  findSectionIdForRef,
  lookupEngineBlock,
  lookupMacro,
  type EngineBlock,
  type ExpandedRef,
  type Sidecar,
  type StationTemplate,
} from '$lib/editor';

/** What a rule does, in plain words: "Next in order from NIGHT". */
export function ruleText(block: EngineBlock): string {
  const from = block.source;
  let text: string;
  switch (block.strategy) {
    case 'pick':
      text = `Picked from ${from}`;
      break;
    case 'window':
      text = block.size > 1 ? `${block.size} in a row from ${from}` : `Next from ${from}`;
      break;
    case 'sequential':
      text = block.size > 1 ? `Next ${block.size} in order from ${from}` : `Next in order from ${from}`;
      break;
    case 'weighted':
      // Every song once before any repeats; no artist twice within the
      // last `window` picks (10 unless the block says).
      text = `Shuffled from ${from}, no artist twice in ${block.window ?? 10}`;
      break;
    case 'jingle':
      text = block.genre ? `Jingle from ${from}: ${block.genre}` : `Jingle from ${from}`;
      break;
    case 'mix':
      text = block.genre ? `Mix from ${from}: ${block.genre}` : `Mix from ${from}`;
      if (block.exclusion_window) {
        const w = block.exclusion_window;
        text += `, not used in the last ${w} ${w === 1 ? 'week' : 'weeks'}`;
      }
      break;
    case 'prev_ref':
      text =
        block.position !== undefined
          ? `Same as last week's song ${block.position}`
          : `Same as last week`;
      // A miss falls back to a mix of fallback_genre, or else a weighted
      // pick from this block's source (engine/generator.py).
      text += block.fallback_genre
        ? ` (else a ${block.fallback_genre} mix)`
        : ` (else shuffled from ${from})`;
      break;
    case 'archive_ref':
      text =
        block.position !== undefined
          ? `Song ${block.position} from the ${from} archive`
          : `From the ${from} archive`;
      break;
    default: {
      // A new strategy without words yet: say what the engine calls it.
      const _new: never = block.strategy;
      text = `${String(_new)} from ${from}`;
    }
  }
  if (block.allowlist_sources && block.allowlist_sources.length > 0) {
    text += `, only songs also in ${block.allowlist_sources.join(' or ')}`;
  }
  return text;
}

/**
 * Which rule made a song, as a key: songs with the same key change
 * together when that rule is edited. A repeating row's block is one rule
 * for every row (editing it edits each row), so the row isn't part of the
 * key; a macro is one rule for all its songs.
 */
export function ruleKey(ref: ExpandedRef): string {
  if (ref.kind === 'macro-placeholder') return `macro:${ref.macro_id}`;
  if (ref.slot_index !== undefined) return `slot:${ref.slot_index}`;
  return `block:${ref.block_idx}`;
}

export interface SongRow {
  slot: TraceSlot;
  /** 1-based place in the week. */
  position: number;
  /** The rule's block in the editor, or null when it can't be found. */
  ref: ExpandedRef | null;
  /** The rule itself (a macro's spec for a macro's songs). */
  block: EngineBlock | null;
  /** The macro's name when the song comes from one ("Hour jingles"). */
  macroName: string | null;
  /** Compared with the saved week: a different song, a new one, or null. */
  change: 'changed' | 'added' | null;
  /** The block's rule now differs from the one this song was picked with
   *  (an older version of the week, or the template changed since). The
   *  trace's own strategy and source are then the accurate account. */
  ruleChanged: boolean;
}

export interface SongGroup {
  /** The sidecar section's id, or null for songs outside any section. */
  sectionId: string | null;
  sectionName: string;
  rows: SongRow[];
}

function blockFor(ref: ExpandedRef | null, template: StationTemplate, sidecar: Sidecar): {
  block: EngineBlock | null;
  macroName: string | null;
} {
  if (ref === null) return { block: null, macroName: null };
  if (ref.kind === 'macro-placeholder') {
    const macro = lookupMacro(sidecar, ref.macro_id);
    return { block: macro?.spec ?? null, macroName: macro ? (macro.name ?? macro.id) : null };
  }
  return { block: lookupEngineBlock(ref, template.layout), macroName: null };
}

/**
 * A week's songs in playing order, in runs that share a section.
 * With `saved` (the same week as saved), each song is marked when it
 * differs from the saved week's song at that place.
 */
export function songGroups(
  trace: Trace,
  template: StationTemplate,
  sidecar: Sidecar,
  saved: Trace | null = null,
): SongGroup[] {
  const sectionNames = new Map(sidecar.sections.map((s) => [s.id, s.name ?? s.id] as const));
  const changes = saved ? classifySlots(saved, trace) : [];
  const groups: SongGroup[] = [];
  trace.slots.forEach((slot, i) => {
    const ref = findChipRef(template, sidecar, slot);
    const sectionId = ref ? findSectionIdForRef(sidecar, ref) : null;
    const { block, macroName } = blockFor(ref, template, sidecar);
    const kind = changes[i]?.kind;
    const row: SongRow = {
      slot,
      position: i + 1,
      ref,
      block,
      macroName,
      change: kind === 'changed' || kind === 'added' ? kind : null,
      ruleChanged: block !== null && (block.strategy !== slot.strategy || block.source !== slot.source),
    };
    const last = groups[groups.length - 1];
    if (last && last.sectionId === sectionId) {
      last.rows.push(row);
    } else {
      groups.push({
        sectionId,
        sectionName: sectionId === null ? 'Not in a section' : (sectionNames.get(sectionId) ?? sectionId),
        rows: [row],
      });
    }
  });
  return groups;
}

/** How many songs the saved week had that the edited week doesn't
 *  (shown as a note: removed songs have no row of their own). */
export function removedCount(trace: Trace, saved: Trace | null): number {
  if (!saved) return 0;
  return classifySlots(saved, trace).filter((c) => c.kind === 'removed').length;
}
