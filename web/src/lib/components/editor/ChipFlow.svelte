<script lang="ts">
  /**
   * <ChipFlow> — §16.2.2 c3.
   *
   * Wraps `expandSection`'s output into a flex-wrap row of `<Chip>`
   * elements. Two behaviours:
   *
   *   1. Macro placeholders: only the FIRST placeholder per macro
   *      repeat-span is rendered (its Chip shows the ×N count).
   *      Subsequent placeholders for the SAME contiguous macro are
   *      filtered out — they're redundant once the count badge is
   *      visible. Non-contiguous occurrences of the same macro
   *      (e.g. macro X at slot 5 AND slot 20) render as separate
   *      chips, each with its own ×N count.
   *
   *   2. Section gets one ChipFlow. The flex-wrap layout keeps
   *      chips on a single visual row when there's space, wrapping
   *      to multiple rows for wide sections (Decision B2: wrap-flow).
   */
  import { expandSection, type ExpandedRef, type Sidecar, type SidecarSection, type StationTemplate } from '$lib/editor';
  import Chip from './Chip.svelte';

  export let section: SidecarSection;
  export let template: StationTemplate;
  export let sidecar: Sidecar;

  // Compute the rendered list: expand, then collapse contiguous
  // macro placeholders to the first-of-each-span.
  $: rendered = (() => {
    const expanded = expandSection(section, sidecar);
    const out: ExpandedRef[] = [];
    let lastMacroId: string | null = null;
    for (const ref of expanded) {
      if (ref.kind === 'macro-placeholder') {
        if (ref.macro_id === lastMacroId) {
          // Same contiguous macro — fold into the previous chip's
          // ×N count via lookupMacro lookup in the Chip component.
          continue;
        }
        lastMacroId = ref.macro_id;
      } else {
        lastMacroId = null;
      }
      out.push(ref);
    }
    return out;
  })();

  // Stable key for {#each} — atomic refs use their address; macro
  // refs use macro_id + pick_in_macro (the first-of-span is always
  // pick_in_macro=1, so this is unique per macro instance).
  function refKey(ref: ExpandedRef, i: number): string {
    if (ref.kind === 'atomic-ref') {
      return ref.slot_index !== undefined
        ? `s${ref.slot_index}@${i}`
        : `r${ref.row_idx}-b${ref.block_idx}@${i}`;
    }
    return `m:${ref.macro_id}:${ref.pick_in_macro}@${i}`;
  }
</script>

<div class="chip-flow" data-testid="chip-flow">
  {#each rendered as ref, i (refKey(ref, i))}
    <Chip {ref} {template} {sidecar} />
  {/each}
</div>

<style>
  .chip-flow {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.05rem;
    padding: 0.25rem 0;
  }
</style>
