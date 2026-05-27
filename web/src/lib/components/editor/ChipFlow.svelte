<script lang="ts">
  /**
   * <ChipFlow> — §16.2.2 c3.
   *
   * Wraps `expandSection`'s output into a flex-wrap row of `<Chip>`
   * elements. Two behaviours:
   *
   *   1. Macro placeholders: only the FIRST placeholder per macro
   *      repeat-span is rendered (its Chip shows the ×N count from
   *      `macro.repeat`). Subsequent placeholders within the same
   *      span are filtered out — they're redundant once the count
   *      badge is visible.
   *
   *      Detection uses `pick_in_macro === 1` rather than tracking
   *      the last-seen macro_id (c3 review 🔴-1). A macro_id-based
   *      tracker incorrectly collapsed ALL placeholders across a
   *      `repeat_with_offsets` section into one chip — eliding the
   *      offset-instance distinctions. pick_in_macro resets to 1 at
   *      the start of each span (the engine emits it 1..N per
   *      offset iteration), so "first-of-span" is unambiguous.
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

  // Compute the rendered list: expand, then collapse each macro
  // span to its first placeholder (pick_in_macro === 1).
  $: rendered = expandSection(section, sidecar).filter(
    (ref): ref is ExpandedRef =>
      ref.kind === 'atomic-ref' || ref.pick_in_macro === 1,
  );

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
