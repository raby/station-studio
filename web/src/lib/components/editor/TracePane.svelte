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
   * renders the rows; the parent dispatches a `select` event on row
   * click so c3 / .t3 can wire it later without component changes.
   *
   * NO virtualisation at v1 — Rebirth Radio's 2,196 rows × ~30px row
   * height render fine in modern browsers; §16.18 (saved-trace
   * virtualisation) folds preview-pane in if scroll perf ever bites.
   */
  import { createEventDispatcher } from 'svelte';
  import type { Trace } from '$lib/api';

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

  const dispatch = createEventDispatcher<{
    select: { slotIndex: number };
  }>();

  function onRowClick(slotIndex: number) {
    dispatch('select', { slotIndex });
  }
</script>

<aside class="trace-pane" data-testid="trace-pane">
  <header class="trace-header">
    <span class="title">Trace</span>
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

  {#if trace && trace.slots.length > 0}
    <ol class="slots" data-testid="trace-slots">
      {#each trace.slots as slot, i (i)}
        <!-- svelte-ignore a11y-click-events-have-key-events -->
        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
        <li
          class="slot"
          data-testid="trace-row"
          data-slot-index={slot.slot}
          on:click={() => onRowClick(slot.slot)}
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
</style>
