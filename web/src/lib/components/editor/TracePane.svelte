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
  /** §16.2.6.t8 — currently selected preview week. Bound to the
   *  number input in the header. The PARENT owns the value + the
   *  persistence (D2 — localStorage per-station); this component
   *  is a controlled input. Changes dispatch `weekChange`. */
  export let weekValue: number = 1;

  const dispatch = createEventDispatcher<{
    select: { slotIndex: number };
    weekChange: { week: number };
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
</style>
