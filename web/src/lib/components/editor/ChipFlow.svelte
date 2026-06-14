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
  import { CHIP_DRAG_MIME, canDropOn, hasDragType } from '$lib/drag-drop';
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
  // c3 review #1 finding #2: fine-grained drop requires
  // `rendered.length === section.blocks.length` (visual ↔ blocks
  // index parity). Two cases violate this:
  //
  //   (a) Sections containing macros — `rendered` collapses macro
  //       spans to a single chip (pick_in_macro===1), so a section
  //       with [A, macro×3, B] renders 3 chips for 3 block entries
  //       at indices 0/1/2 — OK there. BUT: drop landing AROUND the
  //       macro must address the macro's index, not an atomic
  //       within the span. Operators dragging a chip onto the macro
  //       chip's region would silently mis-address. Punt.
  //   (b) row_template stations — `expandSection` emits one ref
  //       per (row × block), so rendered.length is rows×blocks.
  //       targetIndex from the rendered walk can't map back to
  //       section.blocks index without a parallel table.
  //
  // For both cases, disable the fine-grained drop indicator. The
  // SectionHeader bar drop (targetIndex=null → server appends)
  // still works for cross-section moves; within-section reorder
  // falls through to the ▲/▼ buttons (when present) or stays a
  // §16.2.5.t16 punt.
  $: hasFineGrainedSupport =
    template.layout.flat_slots !== undefined &&
    !(section.blocks ?? []).some((b) => b.kind === 'macro');
  $: acceptsDrops = canDropOn(section) && hasFineGrainedSupport;

  function _hasChipPayload(e: DragEvent): boolean {
    return hasDragType(e, CHIP_DRAG_MIME);
  }

  // §16.2.5.t18 — perf throttling for the dragover hot path.
  //
  // Pre-fix: `_computeTargetIndex` walked the container's chip
  // children + called `getBoundingClientRect()` on each one — on
  // every dragover (~60Hz × N chips ≈ 3000 forced layouts/sec for a
  // 50-chip section). The rects don't change mid-drag (chips don't
  // move while the operator drags one above them), so the per-frame
  // recompute is pure waste.
  //
  // Two cheap fixes:
  //
  //   (a) Cache chip rects on first dragover, reuse for subsequent
  //       dragovers + the final drop. Invalidate on dragleave (real
  //       container-leave, not a child transition) / dragend / drop.
  //       Indicator-position writes also invalidate the cache lazily
  //       since `dropIndicatorAt` change → DOM reflow next tick →
  //       chips at index ≥ indicator shift right by 2px. So we
  //       invalidate after each rAF write.
  //
  //   (b) rAF-coalesce the `dropIndicatorAt` write. The visual
  //       indicator only needs frame-rate updates; dragover firing
  //       at sub-frame granularity is the browser oversampling. One
  //       rAF in flight at a time; subsequent dragovers just update
  //       the pending clientX.
  //
  // §16.2.6 live-preview adds debounced re-renders on edits; without
  // this throttle they'd stack on top of the drag's per-frame layout
  // work and double-spend the budget on idle drag motion.
  let cachedChipRects: DOMRect[] | null = null;
  let pendingClientX: number | null = null;
  let pendingContainer: HTMLElement | null = null;
  let rafId: number | null = null;

  function _invalidateRectCache() {
    cachedChipRects = null;
  }

  function _ensureRects(container: HTMLElement): DOMRect[] {
    if (cachedChipRects !== null) return cachedChipRects;
    const chips = container.querySelectorAll('[data-testid="chip"]');
    const rects: DOMRect[] = new Array(chips.length);
    for (let i = 0; i < chips.length; i++) {
      rects[i] = (chips[i] as HTMLElement).getBoundingClientRect();
    }
    cachedChipRects = rects;
    return rects;
  }

  function _computeTargetIndex(clientX: number, container: HTMLElement): number {
    // Left-of-midpoint of chip[i] → insert at index i. Right-of-
    // everything → append (index === chip count).
    const rects = _ensureRects(container);
    for (let i = 0; i < rects.length; i++) {
      const midX = rects[i].left + rects[i].width / 2;
      if (clientX < midX) return i;
    }
    return rects.length;
  }

  function _cancelPendingFrame() {
    if (rafId !== null) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    pendingClientX = null;
    pendingContainer = null;
  }

  function onDragOver(e: DragEvent) {
    if (!_hasChipPayload(e) || !acceptsDrops) return;
    e.preventDefault();  // enables drop
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    // §16.2.5.t18(b) — coalesce indicator-position writes to frame
    // rate. Capture the latest clientX + container; one scheduled
    // rAF reads them when the frame fires.
    pendingClientX = e.clientX;
    pendingContainer = e.currentTarget as HTMLElement;
    if (rafId !== null) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      if (pendingClientX === null || pendingContainer === null) return;
      const next = _computeTargetIndex(pendingClientX, pendingContainer);
      pendingClientX = null;
      pendingContainer = null;
      if (next !== dropIndicatorAt) {
        dropIndicatorAt = next;
        // Indicator inserts a 2px span; chips at index ≥ next shift
        // right. Force a rect refetch on the next dragover so we
        // don't read stale cached midpoints.
        _invalidateRectCache();
      }
    });
  }
  function onDragLeave(e: DragEvent) {
    if (!_hasChipPayload(e)) return;
    // Only clear when leaving the container, not when transitioning
    // between child chips. The relatedTarget check distinguishes.
    const container = e.currentTarget as HTMLElement;
    const next = e.relatedTarget as Node | null;
    if (next && container.contains(next)) return;
    _cancelPendingFrame();
    _invalidateRectCache();
    dropIndicatorAt = null;
  }

  // c3 review #1 finding #5: dragend cleanup. ESC-cancelled drags
  // (or any drag-source-removed-mid-drag path) fire `dragend` on the
  // source but NOT `dragleave` on the target — without this listener
  // the 2px indicator bar would remain visible until the next drag
  // cycle moves the cursor across the container boundary.
  function onDragEnd() {
    _cancelPendingFrame();
    _invalidateRectCache();
    dropIndicatorAt = null;
  }
  function onDrop(e: DragEvent) {
    if (!_hasChipPayload(e) || !acceptsDrops) return;
    e.preventDefault();
    // c3 review #1 finding #10: removed the stopPropagation +
    // misleading comment. SectionHeader is a SIBLING of ChipFlow
    // inside <section>, not an ancestor — drops on ChipFlow's div
    // never bubble to the header.
    //
    // Drop must compute the index synchronously — rAF won't fire
    // before the event completes. Use the cached rects if available
    // (set by the last dragover frame); otherwise compute fresh.
    const targetIndex = _computeTargetIndex(
      e.clientX, e.currentTarget as HTMLElement,
    );
    _cancelPendingFrame();
    _invalidateRectCache();
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
  on:dragend={onDragEnd}
  on:drop={onDrop}
>
  {#each rendered as ref, i (refKey(ref, i))}
    {#if dropIndicatorAt === i}
      <span class="drop-indicator" data-testid="drop-indicator" aria-hidden="true"></span>
    {/if}
    <Chip {ref} {template} {sidecar} {viewMode} on:select />
  {/each}
  <!-- c3 review #1 finding #11: trailing indicator covers both the
       'drop at end of populated section' AND 'drop into empty
       section' cases. Pre-fix the `rendered.length > 0` guard
       suppressed visual feedback in empty sections. -->
  {#if dropIndicatorAt === rendered.length}
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
