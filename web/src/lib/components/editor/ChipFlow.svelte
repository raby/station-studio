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
  import { createEventDispatcher, onDestroy } from 'svelte';
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
  //       container-leave, not a child transition), dragend, drop,
  //       and destroy. The drop-indicator is layout-neutral by CSS
  //       (`width: 2px; margin: 0 -1px` → effective inline size 0px;
  //       a single flex `gap: 0.05rem` ≈ 0.8px is the only residual
  //       shift), so indicator-change does NOT invalidate the cache
  //       — sub-pixel midpoint drift is below cursor resolution.
  //
  //   (b) rAF-coalesce the `dropIndicatorAt` write. The visual
  //       indicator only needs frame-rate updates; dragover firing
  //       at sub-frame granularity is the browser oversampling. One
  //       rAF in flight at a time; subsequent dragovers just update
  //       the pending payload.
  //
  // §16.2.6 live-preview adds debounced re-renders on edits; without
  // this throttle they'd stack on top of the drag's per-frame layout
  // work and double-spend the budget on idle drag motion.
  //
  // `pending` collapses (clientX, container) into one record — they
  // always move together (written by onDragOver, consumed + cleared
  // by the rAF + onDrop, nulled by _resetDragState).
  let cachedChipRects: DOMRect[] | null = null;
  let pending: { clientX: number; container: HTMLElement } | null = null;
  let rafId: number | null = null;

  function _invalidateRectCache() {
    cachedChipRects = null;
  }

  // §16.2.5.t22 (a) — viewport-coord invalidation. cachedChipRects
  // holds `getBoundingClientRect()` results, which are viewport-
  // relative. HTML5 drag near a viewport edge triggers native auto-
  // scroll: chips translate in viewport space while `clientX` stays
  // fresh-viewport → cached midpoints address the wrong chip. Same
  // for any concurrent layout shift outside the drag interaction
  // (sibling section expand/collapse, inspector panel open). Window
  // scroll + resize while the cache is live → invalidate; the next
  // dragover refetches automatically via `_ensureRects`.
  //
  // Listeners are scoped to the drag-cycle lifetime: registered when
  // the cache first populates, removed by `_resetDragState`. Passive
  // because they only invalidate state, never mutate DOM or call
  // preventDefault. `capture: true` so the handlers see scrolls in
  // any ancestor scroll container — `scroll` doesn't bubble.
  let listenersAttached = false;
  function _attachScrollListeners() {
    if (listenersAttached) return;
    listenersAttached = true;
    window.addEventListener('scroll', _invalidateRectCache, {
      passive: true, capture: true,
    });
    window.addEventListener('resize', _invalidateRectCache, { passive: true });
  }
  function _detachScrollListeners() {
    if (!listenersAttached) return;
    listenersAttached = false;
    window.removeEventListener('scroll', _invalidateRectCache, {
      capture: true,
    });
    window.removeEventListener('resize', _invalidateRectCache);
  }

  // §16.2.5.t22 (c) — window-level dragend listener catches the
  // ESC-cancel-of-foreign-drag case. ChipFlow's `on:dragend` only
  // fires when the drag SOURCE is a descendant; if the source lives
  // in another section but the cursor was hovering this ChipFlow at
  // ESC-time, this listener tears down the indicator + rect cache.
  // Idempotent — fires for every window dragend (including unrelated
  // drags) but `_resetDragState` is a sequence of nullish assignments
  // + listener detaches that are safe to run on already-clean state.
  // Lifetime scoped to drag-cycle via the same attach/detach pattern
  // as scroll/resize.
  let dragEndListenerAttached = false;
  function _attachDragEndListener() {
    if (dragEndListenerAttached) return;
    dragEndListenerAttached = true;
    window.addEventListener('dragend', _resetDragState);
  }
  function _detachDragEndListener() {
    if (!dragEndListenerAttached) return;
    dragEndListenerAttached = false;
    window.removeEventListener('dragend', _resetDragState);
  }

  function _ensureRects(container: HTMLElement): DOMRect[] {
    if (cachedChipRects !== null) return cachedChipRects;
    const chips = container.querySelectorAll('[data-testid="chip"]');
    const rects: DOMRect[] = new Array(chips.length);
    for (let i = 0; i < chips.length; i++) {
      rects[i] = (chips[i] as HTMLElement).getBoundingClientRect();
    }
    cachedChipRects = rects;
    _attachScrollListeners();
    // §16.2.5.t22 (c) — register the cross-section dragend safety net
    // at the same lifecycle point as scroll/resize. Detached by
    // `_resetDragState` (dragleave / dragend / drop / destroy).
    _attachDragEndListener();
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
    pending = null;
  }

  // Common cleanup invoked by dragleave (real container-leave),
  // dragend, drop, and destroy. Combines pending-rAF cancel + rect-
  // cache invalidation + scroll-listener teardown + window-dragend
  // teardown + indicator clear into one site so the lifecycle
  // invariants stay together.
  function _resetDragState() {
    _cancelPendingFrame();
    _invalidateRectCache();
    _detachScrollListeners();
    _detachDragEndListener();
    dropIndicatorAt = null;
  }

  // §16.2.5.t18 review #1 fix #1: cancel any pending rAF + clear the
  // rect cache on unmount. Without this, an in-flight frame fires
  // after destroy (parent re-renders sections + the now-empty source
  // section unmounts mid-drag — see EditorCanvas's reactive
  // sidecar-driven re-render after blockMove). The callback writes
  // `dropIndicatorAt` on a torn-down component (Svelte 4 silently
  // swallows but logs in dev) and the detached container subtree's
  // GC is delayed one frame.
  onDestroy(_resetDragState);

  function onDragOver(e: DragEvent) {
    if (!_hasChipPayload(e) || !acceptsDrops) return;
    e.preventDefault();  // enables drop
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    // §16.2.5.t18(b) — coalesce indicator-position writes to frame
    // rate. Capture the latest clientX + container; one scheduled
    // rAF reads them when the frame fires.
    pending = {
      clientX: e.clientX,
      container: e.currentTarget as HTMLElement,
    };
    if (rafId !== null) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      if (pending === null) return;
      // §16.2.5.t18 review #1 fix #3: re-check `acceptsDrops` at fire
      // time, not at schedule time. §16.2.6 live-preview can mutate
      // section.blocks mid-drag (add a macro → hasFineGrainedSupport
      // flips false → acceptsDrops false). Without this guard, the
      // previously-scheduled rAF would write an indicator for a
      // section that no longer accepts drops.
      if (!acceptsDrops) {
        pending = null;
        return;
      }
      const next = _computeTargetIndex(pending.clientX, pending.container);
      pending = null;
      if (next !== dropIndicatorAt) dropIndicatorAt = next;
    });
  }
  function onDragLeave(e: DragEvent) {
    // §16.2.5.t22 (b) — DO NOT gate cleanup on `_hasChipPayload`.
    // Firefox / Safari blank `dataTransfer.types` during dragleave
    // across some boundaries (cross-origin iframes, plugin overlays).
    // With the gate, our cleanup never runs on those events — the
    // 2px indicator pins, in-flight rAF fires post-leave, the rect
    // cache leaks. `_resetDragState` is idempotent (sequence of
    // nullish assignments + listener detaches that are safe on
    // already-clean state), so calling it for non-chip drags is
    // a no-op rather than a correctness risk.
    //
    // Only clear when leaving the container, not when transitioning
    // between child chips. The relatedTarget check distinguishes.
    const container = e.currentTarget as HTMLElement;
    const next = e.relatedTarget as Node | null;
    if (next && container.contains(next)) {
      // §16.2.5.t22 (a) — child-transition path. The early return
      // skips `_resetDragState` (the drag is still inside us), but
      // the in-flight rAF still holds the PRE-transition clientX.
      // Refreshing `pending.clientX` here lets the next rAF tick
      // read the current cursor — eliminates the one-frame indicator
      // jitter when the cursor crosses chip boundaries quickly.
      if (pending !== null) pending.clientX = e.clientX;
      return;
    }
    _resetDragState();
  }

  // c3 review #1 finding #5: dragend cleanup. ESC-cancelled drags
  // (or any drag-source-removed-mid-drag path) fire `dragend` on the
  // source but NOT `dragleave` on the target — without this listener
  // the 2px indicator bar would remain visible until the next drag
  // cycle moves the cursor across the container boundary.
  function onDragEnd() {
    _resetDragState();
  }
  function onDrop(e: DragEvent) {
    // §16.2.5.t18 review #1 fix #5: split the gate so preventDefault
    // is unconditional on chip-payload presence. Pre-fix, the last
    // dragover's preventDefault enabled the drop; if `acceptsDrops`
    // flipped false between then + drop fire (mid-drag template
    // mutation per fix #3), the asymmetric guard returned without
    // preventDefault → browser default for any auxiliary MIME the
    // source set (text/plain, text/uri-list) could trigger.
    if (!_hasChipPayload(e)) return;
    e.preventDefault();
    if (!acceptsDrops) return;
    // c3 review #1 finding #10: removed the stopPropagation +
    // misleading comment. SectionHeader is a SIBLING of ChipFlow
    // inside <section>, not an ancestor — drops on ChipFlow's div
    // never bubble to the header.
    //
    // §16.2.5.t18 review #1 fix #2: drop position is the LAST VISIBLE
    // indicator, not a fresh recompute from e.clientX. Under rapid
    // scrubbing the rAF coalesce means dropIndicatorAt can lag the
    // cursor by up to one frame; if the operator releases during the
    // lag the cursor's clientX maps to a different gap than the
    // visible bar. Operator mental model: "drop where the bar is".
    //
    // Synchronously flush any pending rAF first so dropIndicatorAt
    // reflects the latest pending clientX (not the prior frame's).
    // Fallback to clientX-recompute only when there's never been a
    // dragover (no pending + dropIndicatorAt still null — drop on
    // first frame).
    if (rafId !== null && pending !== null) {
      const next = _computeTargetIndex(pending.clientX, pending.container);
      if (next !== dropIndicatorAt) dropIndicatorAt = next;
    }
    const targetIndex = dropIndicatorAt ?? _computeTargetIndex(
      e.clientX, e.currentTarget as HTMLElement,
    );
    _resetDragState();
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
