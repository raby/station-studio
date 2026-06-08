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
  import { createEventDispatcher } from 'svelte';
  import { expandSection, type ExpandedRef, type Sidecar, type SidecarSection, type StationTemplate, type ViewModeState } from '$lib/editor';
  import { CHIP_DRAG_MIME, canDropOn } from '$lib/drag-drop';
  import Chip from './Chip.svelte';

  export let section: SidecarSection;
  export let template: StationTemplate;
  export let sidecar: Sidecar;
  export let viewMode: ViewModeState;

  // §16.2.5.t1 — within-section drag-drop: ChipFlow becomes a fine-
  // grained drop target so the operator can drop a chip between any
  // two chips (or before the first / after the last). The targetIndex
  // is computed from the cursor position relative to each chip's
  // bounding box: cursor left-of-midpoint → drop BEFORE that chip;
  // right-of-midpoint → drop AFTER. The visual indicator is a
  // 2px-wide vertical bar inserted at the drop position via the
  // `dropIndicatorAt` reactive.
  //
  // The SectionHeader drop target stays — drops on the header are
  // "append to end" semantics (targetIndex=null). ChipFlow drops
  // carry the computed targetIndex.
  const dispatch = createEventDispatcher<{
    chipDrop: { ref: unknown; targetSectionId: string; targetIndex: number };
  }>();
  let dropIndicatorAt: number | null = null;
  $: acceptsDrops = canDropOn(section);

  function _hasChipPayload(e: DragEvent): boolean {
    if (!e.dataTransfer) return false;
    return Array.from(e.dataTransfer.types).includes(CHIP_DRAG_MIME);
  }

  function _computeTargetIndex(e: DragEvent, container: HTMLElement): number {
    // Walk the container's chip children and find the cursor's
    // position. Left-of-midpoint of chip[i] → insert at index i.
    // Right-of-everything → append (index === chip count).
    const chips = Array.from(
      container.querySelectorAll('[data-testid="chip"]'),
    ) as HTMLElement[];
    for (let i = 0; i < chips.length; i++) {
      const r = chips[i].getBoundingClientRect();
      const midX = r.left + r.width / 2;
      if (e.clientX < midX) return i;
    }
    return chips.length;
  }

  function onDragOver(e: DragEvent) {
    if (!_hasChipPayload(e) || !acceptsDrops) return;
    e.preventDefault();  // enables drop
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    dropIndicatorAt = _computeTargetIndex(
      e, e.currentTarget as HTMLElement,
    );
  }
  function onDragLeave(e: DragEvent) {
    if (!_hasChipPayload(e)) return;
    // Only clear when leaving the container, not when transitioning
    // between child chips. The relatedTarget check distinguishes.
    const container = e.currentTarget as HTMLElement;
    const next = e.relatedTarget as Node | null;
    if (next && container.contains(next)) return;
    dropIndicatorAt = null;
  }
  function onDrop(e: DragEvent) {
    if (!_hasChipPayload(e) || !acceptsDrops) return;
    e.preventDefault();
    e.stopPropagation();  // prevent the SectionHeader's bubble-up drop
    const targetIndex = _computeTargetIndex(
      e, e.currentTarget as HTMLElement,
    );
    dropIndicatorAt = null;
    const raw = e.dataTransfer?.getData(CHIP_DRAG_MIME);
    if (!raw) return;
    let ref: unknown;
    try { ref = JSON.parse(raw); }
    catch (err) {
      console.warn('ChipFlow: invalid chip-drop JSON', err, raw);
      return;
    }
    dispatch('chipDrop', {
      ref, targetSectionId: section.id, targetIndex,
    });
  }

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

<!-- svelte-ignore a11y-no-static-element-interactions -->
<div
  class="chip-flow"
  data-testid="chip-flow"
  data-drop-at={dropIndicatorAt}
  on:dragover={onDragOver}
  on:dragleave={onDragLeave}
  on:drop={onDrop}
>
  {#each rendered as ref, i (refKey(ref, i))}
    {#if dropIndicatorAt === i}
      <span class="drop-indicator" data-testid="drop-indicator" aria-hidden="true"></span>
    {/if}
    <Chip {ref} {template} {sidecar} {viewMode} on:select />
  {/each}
  {#if dropIndicatorAt === rendered.length && rendered.length > 0}
    <span class="drop-indicator" data-testid="drop-indicator" aria-hidden="true"></span>
  {/if}
</div>

<style>
  .chip-flow {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.05rem;
    padding: 0.25rem 0;
  }
  /* §16.2.5.t1 — within-section drop-position indicator. 2px tall
     vertical bar inserted between chips at the targeted insert
     position. Width-zero so chip layout doesn't shift on hover. */
  .drop-indicator {
    display: inline-block;
    width: 2px;
    height: 1.4em;
    background: var(--accent, #2c7a7b);
    margin: 0 -1px;
    border-radius: 1px;
    pointer-events: none;
  }
</style>
