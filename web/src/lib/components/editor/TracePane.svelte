<script lang="ts">
  /**
   * <TracePane> — §16.2.6 c2 live-preview trace display.
   *
   * Renders the trace JSON returned by `POST /api/stations/{slug}/jobs/preview`
   * as a flat list of slot rows. Side panel on the right of the canvas
   * (D2) — 30% viewport default; the parent page owns width + the
   * open/closed transition.
   *
   * Three render states:
   *   - PREVIEWING — "Previewing…" spinner (no trace yet, job in flight)
   *   - STAGED + ok — flat trace.slots list
   *   - empty (initial / Discard) — empty-state nudge
   *
   * Per the design's §16.2.6.t3 forward-pointer the click-trace-row →
   * jump-to-chip interaction lands in a future sub-slice. c2 just
   * renders the rows inline (no `<TraceRow>` sub-component — the row
   * markup is short enough that an extra component would add wiring
   * without payoff). The parent dispatches a `select` event on row
   * click (or Enter/Space keyboard activation) so c3 / .t3 can wire
   * the jump-to-chip handler later without component changes.
   *
   * NO virtualisation at v1 — Rebirth Radio's 2,196 rows × ~30px row
   * height render fine in modern browsers; §16.18 (saved-trace
   * virtualisation) folds preview-pane in if scroll perf ever bites.
   */
  import { createEventDispatcher } from 'svelte';
  import type { Trace, TraceSlot } from '$lib/api';
  import {
    classifySlots,
    type SlotClassification,
  } from '$lib/editor';

  /** Latest successful preview trace. null when no preview has run yet
   *  or when Discard cleared it. */
  export let trace: Trace | null = null;
  /** True while a preview job is in flight. Shown as a "Previewing…"
   *  state in the header. */
  export let previewing: boolean = false;
  /** Last preview-job error message, if any. Renders as a banner ABOVE
   *  the (possibly stale) trace so the operator's frame of reference
   *  doesn't disappear when mid-typing an invalid edit. */
  export let errorMessage: string | null = null;
  /** §16.2.6.t8 — currently selected preview week. Bound to the
   *  number input in the header. The PARENT owns the value + the
   *  persistence (D2 — localStorage per-station); this component
   *  is a controlled input. Changes dispatch `weekChange`. */
  export let weekValue: number = 1;
  /** §16.2.7 c2 — compare-against-saved props. PARENT owns the
   *  toggle state + the savedTrace cache (D2 — fire once per Save
   *  cycle); this component renders against both. `showDiff` gates
   *  the diff UI (checkbox + row classes + summary); `savedTrace`
   *  is the cached SAVED-state preview to compare against the
   *  live STAGED `trace` prop above; `savedTraceError` surfaces a
   *  "Diff unavailable: ..." banner when the saved-state preview
   *  failed (D6 — never block the staged-trace flow). */
  export let showDiff: boolean = false;
  export let savedTrace: Trace | null = null;
  export let savedTraceError: string | null = null;

  const dispatch = createEventDispatcher<{
    select: { slotIndex: number };
    weekChange: { week: number };
    showDiffChange: { on: boolean };
  }>();

  function onRowClick(slotIndex: number) {
    dispatch('select', { slotIndex });
  }

  function onWeekChange(e: Event) {
    // D4 — `change` event (NOT `input`) so the dispatch fires once
    // per blur/Enter/arrow-release, not per keystroke. The parent's
    // 500ms preview debounce absorbs any trickle.
    const target = e.currentTarget as HTMLInputElement;
    const next = Math.max(1, Math.floor(Number(target.value) || 1));
    if (next !== weekValue) {
      dispatch('weekChange', { week: next });
    }
    // Reflect the clamped value back into the input so the operator
    // sees the corrected number when they type e.g. 0 or empty.
    target.value = String(next);
  }

  function onShowDiffChange(e: Event) {
    const target = e.currentTarget as HTMLInputElement;
    dispatch('showDiffChange', { on: target.checked });
  }

  // §16.2.7 c2 — derived diff state. Only meaningful when BOTH
  // traces are present (savedTrace cache populated AND staged trace
  // landed). Falls back gracefully when either is missing — the
  // diff classes don't apply; the existing single-trace render path
  // takes over.
  $: diffReady = showDiff && trace !== null && savedTrace !== null;
  $: classifications = diffReady
    ? classifySlots(savedTrace, trace)
    : ([] as SlotClassification[]);

  // Per-kind counts for the header summary (D4). Computed only when
  // diffReady so the iteration cost (O(slots)) doesn't fire on
  // every render in non-diff mode.
  $: diffCounts = (() => {
    if (!diffReady) return { changed: 0, added: 0, removed: 0 };
    let changed = 0, added = 0, removed = 0;
    for (const c of classifications) {
      if (c.kind === 'changed') changed++;
      else if (c.kind === 'added') added++;
      else if (c.kind === 'removed') removed++;
    }
    return { changed, added, removed };
  })();

  // Summary text: "N changed · M added · K removed" with zero-count
  // terms omitted (D4 — "zero-count terms omitted"). When all-zero
  // we show "No changes since save" so the operator sees explicit
  // confirmation rather than a blank summary slot.
  $: diffSummaryText = (() => {
    if (!diffReady) return '';
    const parts: string[] = [];
    if (diffCounts.changed) parts.push(`${diffCounts.changed} changed`);
    if (diffCounts.added) parts.push(`${diffCounts.added} added`);
    if (diffCounts.removed) parts.push(`${diffCounts.removed} removed`);
    return parts.length === 0 ? 'No changes since save' : parts.join(' · ');
  })();

  // For each classification, pick the TraceSlot to render. UNCHANGED /
  // CHANGED / ADDED render the STAGED slot (operator sees the future
  // state); REMOVED renders the SAVED slot (operator sees what's
  // about to disappear). The render function reads track_name etc.
  // off whichever side has the data; the row class drives the visual
  // delta.
  function pickDisplaySlot(
    cls: SlotClassification,
    saved: Trace | null,
    staged: Trace | null,
  ): TraceSlot | null {
    if (cls.kind === 'removed') {
      return saved?.slots[cls.index] ?? null;
    }
    return staged?.slots[cls.index] ?? null;
  }

  // For CHANGED rows we ALSO surface the saved-side track_name as
  // inline "was: <name>" subtext. UNCHANGED / ADDED / REMOVED don't
  // use this (UNCHANGED has no diff to show; ADDED has no saved
  // side; REMOVED's content IS the saved side already).
  function pickWasName(
    cls: SlotClassification,
    saved: Trace | null,
  ): string | null {
    if (cls.kind !== 'changed') return null;
    return saved?.slots[cls.index]?.track_name ?? null;
  }
</script>

<aside class="trace-pane" data-testid="trace-pane">
  <header class="trace-header">
    <span class="title">Trace</span>
    <!-- §16.2.6.t8 week selector. D1: lives inline with the status
         text so the operator's eye stays in one place. Controlled
         input: value comes from the `weekValue` prop; change events
         dispatch up to the parent which owns persistence + reschedule. -->
    <label class="week-label">
      Week
      <input
        type="number"
        min="1"
        step="1"
        class="week-input"
        data-testid="trace-week-input"
        value={weekValue}
        on:change={onWeekChange}
      />
    </label>
    <!-- §16.2.7 c2 (D1) — compare-against-saved toggle. Controlled
         checkbox: value from `showDiff` prop; change dispatches
         showDiffChange. The parent owns persistence + cache
         lifecycle. -->
    <label class="diff-label">
      <input
        type="checkbox"
        class="diff-checkbox"
        data-testid="trace-show-diff"
        checked={showDiff}
        on:change={onShowDiffChange}
      />
      Diff
    </label>
    {#if previewing}
      <span class="status previewing" data-testid="trace-status-previewing">
        Previewing…
      </span>
    {:else if errorMessage}
      <span class="status error" data-testid="trace-status-error">
        Preview failed
      </span>
    {:else if trace}
      <span class="status ok" data-testid="trace-status-ok">
        Week {trace.week} · {trace.slots.length} slots
      </span>
    {/if}
    {#if diffReady}
      <!-- §16.2.7 c2 (D4) — diff summary alongside the status. -->
      <span class="status diff-summary" data-testid="trace-diff-summary">
        {diffSummaryText}
      </span>
    {/if}
  </header>

  {#if errorMessage}
    <!-- The error banner sits ABOVE any stale-but-last-good trace.
         §16.2.6 design: "The trace pane keeps showing the last-good
         trace so the operator's frame of reference doesn't disappear
         when they're mid-typing." Banner names the inline validation
         error; the next valid preview clears it. -->
    <div class="error-banner" data-testid="trace-error" role="status">
      {errorMessage}
    </div>
  {/if}

  {#if showDiff && savedTraceError}
    <!-- §16.2.7 c2 (D6) — saved-trace fetch failure surfaces
         "Diff unavailable: ..." without blocking the staged-trace
         flow above. Operator can clear by toggling diff off then
         back on (re-fires the saved-trace fetch) or by saving (the
         post-Save invalidation refires too). -->
    <div class="diff-error-banner" data-testid="trace-diff-error" role="status">
      Diff unavailable: {savedTraceError}
    </div>
  {/if}

  {#if diffReady}
    <ol class="slots slots--diff" data-testid="trace-slots">
      {#each classifications as cls (cls.index)}
        {@const slot = pickDisplaySlot(cls, savedTrace, trace)}
        {@const wasName = pickWasName(cls, savedTrace)}
        {#if slot}
          <!-- svelte-ignore a11y-no-noninteractive-element-to-interactive-role -->
          <li
            class="slot slot--{cls.kind}"
            data-testid="trace-row"
            data-slot-index={slot.slot}
            data-diff-kind={cls.kind}
            role="button"
            tabindex="0"
            on:click={() => onRowClick(slot.slot)}
            on:keydown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onRowClick(slot.slot);
              }
            }}
          >
            <span class="idx">{slot.slot}</span>
            <span class="source" title={slot.source}>{slot.source}</span>
            <span class="strategy">{slot.strategy}</span>
            <span class="name" title={slot.track_name}>
              {slot.track_name}
              {#if cls.kind === 'added'}
                <span class="diff-marker" data-testid="trace-row-marker">+ added</span>
              {/if}
            </span>
            {#if slot.track_artist}
              <span class="artist" title={slot.track_artist}>
                {slot.track_artist}
              </span>
            {/if}
            {#if wasName}
              <!-- §16.2.7 c2 (D4) — "was: <name>" subtext on CHANGED
                   rows so the operator sees the prior content
                   alongside the new one. -->
              <span class="diff-was" data-testid="trace-row-was">
                was: {wasName}
              </span>
            {/if}
          </li>
        {/if}
      {/each}
    </ol>
  {:else if trace && trace.slots.length > 0}
    <ol class="slots" data-testid="trace-slots">
      {#each trace.slots as slot, i (i)}
        <!-- §16.2.6 c2 review #1 fix #7: keyboard a11y on trace
             rows. `role="button"` + `tabindex="0"` make rows
             keyboard-focusable; Enter and Space both dispatch the
             same select event as the click handler. Wraps the
             §16.2.6.t3 jump-to-chip interaction in keyboard
             support from day one. The
             a11y-no-noninteractive-element-to-interactive-role
             warning is intentional: an inner <button> would
             complicate the grid layout + duplicate the click
             surface; the role+tabindex+keydown trio gives the
             same accessibility outcome with simpler markup. -->
        <!-- svelte-ignore a11y-no-noninteractive-element-to-interactive-role -->
        <li
          class="slot"
          data-testid="trace-row"
          data-slot-index={slot.slot}
          role="button"
          tabindex="0"
          on:click={() => onRowClick(slot.slot)}
          on:keydown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              onRowClick(slot.slot);
            }
          }}
        >
          <span class="idx">{slot.slot}</span>
          <span class="source" title={slot.source}>{slot.source}</span>
          <span class="strategy">{slot.strategy}</span>
          <span class="name" title={slot.track_name}>{slot.track_name}</span>
          {#if slot.track_artist}
            <span class="artist" title={slot.track_artist}>
              {slot.track_artist}
            </span>
          {/if}
        </li>
      {/each}
    </ol>
  {:else if !previewing && !errorMessage}
    <div class="empty" data-testid="trace-empty">
      Edit a chip to see a preview here.
    </div>
  {/if}
</aside>

<style>
  .trace-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-subtle, #fafafa);
    border-left: 1px solid var(--border, #ddd);
    overflow: hidden;
  }
  .trace-header {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid var(--border, #ddd);
    flex-shrink: 0;
  }
  .title {
    font-weight: 600;
    font-size: 0.95rem;
  }
  .week-label {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.8rem;
    color: var(--text-muted, #666);
  }
  .week-input {
    width: 3.5rem;
    padding: 0.1rem 0.3rem;
    font-size: 0.85rem;
    border: 1px solid var(--border, #ccc);
    border-radius: 3px;
    font-variant-numeric: tabular-nums;
  }
  .status {
    font-size: 0.8rem;
    color: var(--text-muted, #666);
    font-variant-numeric: tabular-nums;
  }
  .status.previewing {
    color: var(--accent, #2c7a7b);
  }
  .status.error {
    color: var(--error, #c0392b);
  }
  .error-banner {
    padding: 0.5rem 0.75rem;
    background: color-mix(in srgb, var(--error, #c0392b) 10%, transparent);
    border-bottom: 1px solid var(--error, #c0392b);
    font-size: 0.85rem;
    color: var(--error, #c0392b);
    flex-shrink: 0;
  }
  .empty {
    padding: 1.5rem 0.75rem;
    font-size: 0.85rem;
    color: var(--text-muted, #666);
    text-align: center;
    font-style: italic;
  }
  .slots {
    list-style: none;
    padding: 0;
    margin: 0;
    overflow-y: auto;
    flex: 1;
  }
  .slot {
    display: grid;
    grid-template-columns: 2.5rem 4rem 4rem 1fr 1fr;
    gap: 0.4rem;
    padding: 0.2rem 0.75rem;
    font-size: 0.8rem;
    cursor: pointer;
    border-bottom: 1px solid color-mix(in srgb, var(--border, #ddd) 50%, transparent);
  }
  .slot:hover {
    background: var(--bg-hover, #f0f0f0);
  }
  .idx {
    color: var(--text-muted, #666);
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .source {
    color: var(--text-muted, #555);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .strategy {
    color: var(--text-muted, #555);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .artist {
    color: var(--text-muted, #666);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* §16.2.7 c2 (D1, D4) — diff toggle + summary + per-row classes.
     Accent colors via CSS variables so the §16.5 a11y pass can tune
     contrast without re-touching this component. Each accent gets a
     left-border on the row + a tinted background; the row's grid
     layout stays unchanged so toggling diff doesn't reflow content. */
  .diff-label {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.8rem;
    color: var(--text-muted, #666);
    cursor: pointer;
  }
  .diff-checkbox {
    margin: 0;
  }
  .diff-summary {
    color: var(--text-muted, #666);
    font-variant-numeric: tabular-nums;
  }
  .diff-error-banner {
    padding: 0.4rem 0.75rem;
    background: color-mix(in srgb, var(--diff-rem, #c0392b) 8%, transparent);
    border-bottom: 1px solid var(--diff-rem, #c0392b);
    font-size: 0.8rem;
    color: var(--diff-rem, #c0392b);
    flex-shrink: 0;
  }
  .slots--diff .slot {
    /* .slot--unchanged has no rule by design — UNCHANGED rows look
       identical to the non-diff view; only the diff-flagged rows
       below decorate. The transparent border + transition let
       changed/added/removed slide in without reflowing the grid. */
    border-left: 3px solid transparent;
    transition: background 80ms ease-out, border-color 80ms ease-out;
  }
  .slot--changed {
    border-left-color: var(--diff-warn, #b08c00) !important;
    background: color-mix(in srgb, var(--diff-warn, #b08c00) 6%, transparent);
  }
  .slot--added {
    border-left-color: var(--diff-add, #2c7a2c) !important;
    background: color-mix(in srgb, var(--diff-add, #2c7a2c) 6%, transparent);
  }
  .slot--removed {
    border-left-color: var(--diff-rem, #c0392b) !important;
    background: color-mix(in srgb, var(--diff-rem, #c0392b) 6%, transparent);
  }
  .slot--removed .name,
  .slot--removed .artist {
    text-decoration: line-through;
    color: var(--text-muted, #888);
  }
  .diff-marker {
    margin-left: 0.4rem;
    font-size: 0.7rem;
    color: var(--diff-add, #2c7a2c);
    font-weight: 600;
    vertical-align: middle;
  }
  .diff-was {
    grid-column: 4 / 6;
    font-size: 0.7rem;
    color: var(--text-muted, #888);
    font-style: italic;
    margin-top: 0.1rem;
  }
  /* D4 — prefers-reduced-motion swap: skip the transition; the
     class change is instant. The visual delta still lands; the
     animation just doesn't. */
  @media (prefers-reduced-motion: reduce) {
    .slots--diff .slot {
      transition: none;
    }
  }
</style>
