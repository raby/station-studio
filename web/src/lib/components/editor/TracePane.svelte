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
   * §16.2.6.t3 SHIPPED the click-trace-row → jump-to-chip interaction.
   * Row click (or Enter/Space keyboard activation) dispatches a
   * `select` event that the parent's `handleTraceSelect` consumes —
   * the handler maps the trace slot to a chip ref via `findChipRef`,
   * scrolls the chip into view, and flashes its outline. Row markup
   * stays inline (no `<TraceRow>` sub-component — the row is short
   * enough that the extra wiring wouldn't pay off).
   *
   * NO virtualisation at v1 — Rebirth Radio's 2,196 rows × ~30px row
   * height render fine in modern browsers; §16.18 (saved-trace
   * virtualisation) folds preview-pane in if scroll perf ever bites.
   */
  import { createEventDispatcher } from 'svelte';
  import type { Trace, TraceSlot } from '$lib/api';
  import {
    aggregateBySection,
    classifySlots,
    summarizeClassifications,
    UNMAPPED_BUCKET_KEY,
    type ClassificationCounts,
    type Sidecar,
    type SlotClassification,
    type StationTemplate,
  } from '$lib/editor';
  import { describeOp, type Edit } from '$lib/edit-ops';

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
  /** §16.2.7.t4 — multi-week diff aggregator props. PARENT owns the
   *  range value + the per-week cache + the fetch loop; TracePane
   *  renders. `compareNextN` is the number of ADDITIONAL weeks
   *  beyond the focused `weekValue` to aggregate (0 = single-week
   *  mode = no multi-week UI rendered). `weekDiffSummary` is the
   *  per-week aggregate counts Map keyed by absolute week number;
   *  entries may be `{ changed, added, removed }` for successful
   *  fetches or `{ error: string }` for failed ones (D5).
   *  `multiWeekFetching` drives the "Computing…" placeholder. */
  export let compareNextN: number = 0;
  export let weekDiffSummary: Map<number, {
    changed: number; added: number; removed: number;
  } | { error: string }> = new Map();
  export let multiWeekFetching: boolean = false;
  /** Max value the parent will accept (D1: defensive cap, default 5).
   *  TracePane just renders the input's `max` attribute against this. */
  export let compareNextNMax: number = 5;
  /** §16.2.7.t5 — per-section diff aggregates inputs. PARENT owns
   *  the saved/staged template + sidecar; TracePane computes the
   *  per-section bucket map via aggregateBySection + renders a
   *  summary row. Null when not available (cache pending /
   *  un-authored station) — the row simply doesn't render. */
  export let template: StationTemplate | null = null;
  export let savedSidecar: Sidecar | null = null;
  export let stagedSidecar: Sidecar | null = null;
  /** §16.2.7.t6 — set of slot indices that have a DIRECT op
   *  attributable in the opLog. The PARENT owns the opLog +
   *  computes this via `attributeOpsToSlot`. TracePane uses it to
   *  enable/disable the per-row Revert button. Slots NOT in this
   *  set (cascade-CHANGED, e.g. shifted by an earlier structural
   *  edit) show a disabled button with a tooltip explaining the
   *  cause. */
  export let revertableSlots: Set<number> = new Set();
  /** §16.2.7.t7 — the operator's staged opLog. PARENT passes a
   *  read-only reference; TracePane just renders each entry via
   *  `describeOp` as a numbered line in the "Edits" panel. Panel
   *  shows only when showDiff is on AND opLog non-empty. */
  export let opLog: Edit[] = [];

  const dispatch = createEventDispatcher<{
    select: { slotIndex: number };
    weekChange: { week: number };
    showDiffChange: { on: boolean };
    compareNextNChange: { n: number };
    revertSlot: { slotIndex: number };
  }>();

  function onRevertClick(e: Event, slotIndex: number): void {
    // D1 — stop propagation so the parent <li>'s click-to-jump
    // doesn't also fire. The button is inside the row's grid;
    // without stopPropagation a click would dispatch BOTH
    // revertSlot AND select.
    e.stopPropagation();
    dispatch('revertSlot', { slotIndex });
  }

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

  function onCompareNextNChange(e: Event) {
    const target = e.currentTarget as HTMLInputElement;
    // Defensive parse + clamp mirrors the page's
    // _applyCompareNextNChange. The page is the authoritative
    // clamper; this one keeps the input's displayed value sane on
    // garbage input.
    const raw = Number(target.value);
    const next = Number.isFinite(raw)
      ? Math.max(0, Math.min(compareNextNMax, Math.floor(raw)))
      : 0;
    if (next !== compareNextN) {
      dispatch('compareNextNChange', { n: next });
    }
    target.value = String(next);
  }

  // §16.2.7.t5 — per-section aggregate, derived reactively from the
  // existing diff inputs. Only computes when diff is ready (otherwise
  // returns empty Map). The renderer below iterates the staged
  // sidecar's layout_order for stable ordering + appends UNMAPPED at
  // the tail (if non-zero) so unmapped slots are visible without
  // disrupting the operator's mental ordering of sections.
  $: sectionAggregate = diffReady
    ? aggregateBySection(
        classifications,
        savedTrace,
        trace,
        template,
        savedSidecar,
        stagedSidecar,
      )
    : new Map<string, ClassificationCounts>();

  // Render data: per-section cells ordered by stagedSidecar's
  // layout_order, then UNMAPPED if non-zero. Sections with all-zero
  // counts are hidden — operator only sees the rippled sections.
  function _sectionLabel(sectionId: string, sidecar: Sidecar | null): string {
    if (sectionId === UNMAPPED_BUCKET_KEY) return '(unmapped)';
    const section = sidecar?.sections.find((s) => s.id === sectionId);
    return section?.name ?? sectionId;
  }
  function _sectionCellLabel(counts: ClassificationCounts): string {
    const parts: string[] = [];
    if (counts.changed) parts.push(`${counts.changed}c`);
    if (counts.added) parts.push(`${counts.added}a`);
    if (counts.removed) parts.push(`${counts.removed}r`);
    return parts.join(' ');
  }
  $: sectionCells = (() => {
    if (sectionAggregate.size === 0) return [] as Array<{
      sectionId: string; name: string; label: string;
    }>;
    const out: Array<{ sectionId: string; name: string; label: string }> = [];
    const seenIds = new Set<string>();
    const order = stagedSidecar?.layout_order ?? [];
    for (const sectionId of order) {
      seenIds.add(sectionId);
      const counts = sectionAggregate.get(sectionId);
      if (counts === undefined) continue;
      const label = _sectionCellLabel(counts);
      if (label === '') continue;  // all-zero → skip
      out.push({
        sectionId, name: _sectionLabel(sectionId, stagedSidecar), label,
      });
    }
    // §16.2.7.t5 review R1 — render aggregate entries for sections
    // NOT in stagedSidecar.layout_order. The deleted-in-staged
    // scenario: saved had 'morning', staged removed it; the
    // aggregator buckets REMOVED rows under saved-side section id;
    // iterating only staged.layout_order would silently drop those
    // counts. Render them at the tail (before UNMAPPED) using the
    // SAVED sidecar's section name (since staged no longer has the
    // section at all). UNMAPPED is excluded here — it's rendered
    // below as the final tail cell.
    for (const [sectionId, counts] of sectionAggregate.entries()) {
      if (seenIds.has(sectionId)) continue;
      if (sectionId === UNMAPPED_BUCKET_KEY) continue;
      const label = _sectionCellLabel(counts);
      if (label === '') continue;
      out.push({
        sectionId, name: _sectionLabel(sectionId, savedSidecar), label,
      });
    }
    // Unmapped bucket at the tail.
    const unmapped = sectionAggregate.get(UNMAPPED_BUCKET_KEY);
    if (unmapped !== undefined) {
      const label = _sectionCellLabel(unmapped);
      if (label !== '') {
        out.push({
          sectionId: UNMAPPED_BUCKET_KEY,
          name: '(unmapped)',
          label,
        });
      }
    }
    return out;
  })();

  // §16.2.7.t4 — render-side helper for the per-week summary cells.
  // Returns an array of `{week, label, kind}` items so the template
  // renders the cells consistently. `label` is the rendered text;
  // `kind` ('ok' | 'fetching' | 'error') drives the per-cell class.
  $: multiWeekCells = (() => {
    if (!showDiff || compareNextN <= 0) return [];
    const cells: Array<{
      week: number; label: string;
      kind: 'ok' | 'fetching' | 'error';
      title?: string;
    }> = [];
    for (let i = 0; i <= compareNextN; i++) {
      const wk = weekValue + i;
      const entry = weekDiffSummary.get(wk);
      if (entry === undefined) {
        cells.push({
          week: wk,
          label: multiWeekFetching ? '…' : '–',
          kind: 'fetching',
        });
        continue;
      }
      if ('error' in entry) {
        cells.push({
          week: wk, label: '⚠', kind: 'error',
          title: `Week ${wk} preview failed: ${entry.error}`,
        });
        continue;
      }
      const parts: string[] = [];
      if (entry.changed) parts.push(`${entry.changed}c`);
      if (entry.added) parts.push(`${entry.added}a`);
      if (entry.removed) parts.push(`${entry.removed}r`);
      const label = parts.length === 0 ? '·' : parts.join(' ');
      cells.push({ week: wk, label, kind: 'ok' });
    }
    return cells;
  })();

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
  // every render in non-diff mode. Uses `summarizeClassifications`
  // (extracted for §16.2.7.t4 so the multi-week aggregator shares
  // the same kind-counting semantics).
  $: diffCounts = diffReady
    ? summarizeClassifications(classifications)
    : { changed: 0, added: 0, removed: 0 };

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
    <!-- §16.2.7.t4 (D1) — multi-week aggregate input. Only renders
         when diff is on (no point asking for "+N more weeks" of a
         feature that's off). Capped at compareNextNMax client-side
         + server-side. -->
    {#if showDiff}
      <label class="compare-next-label">
        +
        <input
          type="number"
          min="0"
          max={compareNextNMax}
          step="1"
          class="compare-next-input"
          data-testid="trace-compare-next-n"
          value={compareNextN}
          on:change={onCompareNextNChange}
        />
        wks
      </label>
    {/if}
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

  {#if showDiff && compareNextN > 0}
    <!-- §16.2.7.t4 (D6) — multi-week summary row. Renders below the
         single-week summary (which lives inline in the header). Cells
         are read-only; clicking would be a natural quality-of-life
         (jump focused week to that cell) — queued as a future
         §16.2.7.t4-followup if operators ask. -->
    <div class="multi-week-summary" data-testid="trace-multi-week">
      <span class="multi-week-label">
        Weeks {weekValue}-{weekValue + compareNextN}:
      </span>
      {#each multiWeekCells as cell (cell.week)}
        <span
          class="multi-week-cell multi-week-cell--{cell.kind}"
          data-testid="trace-multi-week-cell"
          data-week={cell.week}
          data-kind={cell.kind}
          title={cell.title ?? ''}
        >
          W{cell.week} {cell.label}
        </span>
      {/each}
    </div>
  {/if}

  {#if diffReady && sectionCells.length > 0}
    <!-- §16.2.7.t5 — per-section diff aggregate row. Renders only
         when diff is on AND at least one section has a non-zero
         count (sectionCells filters all-zero). Mirrors the multi-
         week summary shape — cell-per-bucket inline list. -->
    <div class="section-summary" data-testid="trace-section-summary">
      <span class="section-summary-label">Sect:</span>
      {#each sectionCells as cell (cell.sectionId)}
        <span
          class="section-summary-cell"
          data-testid="trace-section-summary-cell"
          data-section-id={cell.sectionId}
        >
          {cell.name} {cell.label}
        </span>
      {/each}
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

  {#if showDiff && opLog.length > 0}
    <!-- §16.2.7.t7 (D1) — Edits panel. Surfaces structural intent
         (renames, reorders, create/delete) that doesn't show up
         in track_id-based slot diff. Numbered list, scrollable so
         50-op opLogs don't take over. Renders ONLY when showDiff is
         on AND opLog non-empty per D1; same gating as the per-week
         + per-section summary rows. -->
    <div class="edits-panel" data-testid="trace-edits-panel">
      <div class="edits-panel-label">Edits ({opLog.length}):</div>
      <ol class="edits-panel-list" data-testid="trace-edits-list">
        {#each opLog as op, i (i)}
          <li class="edits-panel-row" data-testid="trace-edits-row">
            {describeOp(op)}
          </li>
        {/each}
      </ol>
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
            <!-- §16.2.7.t6 (D1) — Revert button. Hidden on
                 UNCHANGED rows (nothing to revert). Disabled when
                 no opLog op directly attributable to this slot
                 (cascade case — operator must revert the cause
                 instead). Click stops propagation so the row's
                 select-to-jump doesn't also fire. -->
            {#if cls.kind !== 'unchanged'}
              {@const revertable = revertableSlots.has(slot.slot)}
              <button
                type="button"
                class="diff-revert"
                class:diff-revert--disabled={!revertable}
                data-testid="trace-row-revert"
                data-revert-enabled={revertable ? 'true' : 'false'}
                disabled={!revertable}
                title={revertable
                  ? 'Revert the staged edit affecting this slot'
                  : 'No direct edit to revert (this row changed due to a structural edit elsewhere — revert that instead).'}
                on:click={(e) => onRevertClick(e, slot.slot)}
              >
                Revert
              </button>
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
  /* §16.2.7.t6 — diff rows get an extra trailing column for the
     Revert button. Width sized to fit the button + a small gap;
     UNCHANGED rows in diff mode keep the trailing column reserved
     (placeholder) so the grid alignment matches across kinds. */
  .slots--diff .slot {
    grid-template-columns: 2.5rem 4rem 4rem 1fr 1fr auto;
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
  /* §16.2.7.t2 — per-kind diff classes use COMPOUND selectors to
     match the parent's specificity (0,2,0). Pre-fix the per-kind
     rules were single-class (0,1,0) which lost to the parent rule's
     `border-left: transparent` shorthand → fixed via !important.
     With matching specificity the cascade order alone determines
     the winner; !important removed. Cleaner cascade. */
  .slots--diff .slot--changed {
    border-left-color: var(--diff-warn, #b08c00);
    background: color-mix(in srgb, var(--diff-warn, #b08c00) 6%, transparent);
  }
  .slots--diff .slot--added {
    border-left-color: var(--diff-add, #2c7a2c);
    background: color-mix(in srgb, var(--diff-add, #2c7a2c) 6%, transparent);
  }
  .slots--diff .slot--removed {
    border-left-color: var(--diff-rem, #c0392b);
    background: color-mix(in srgb, var(--diff-rem, #c0392b) 6%, transparent);
  }
  .slots--diff .slot--removed .name,
  .slots--diff .slot--removed .artist {
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

  /* §16.2.7.t4 — multi-week aggregator UI. */
  .compare-next-label {
    display: inline-flex;
    align-items: center;
    gap: 0.2rem;
    font-size: 0.8rem;
    color: var(--text-muted, #666);
  }
  .compare-next-input {
    width: 2.5rem;
    padding: 0.1rem 0.3rem;
    font-size: 0.85rem;
    border: 1px solid var(--border, #ccc);
    border-radius: 3px;
    font-variant-numeric: tabular-nums;
  }
  .multi-week-summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.4rem;
    padding: 0.4rem 0.75rem;
    border-bottom: 1px solid var(--border, #ddd);
    background: color-mix(in srgb, var(--bg-subtle, #fafafa) 50%, transparent);
    font-size: 0.78rem;
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
  }
  .multi-week-label {
    color: var(--text-muted, #666);
    font-weight: 500;
  }
  .multi-week-cell {
    padding: 0.1rem 0.35rem;
    border-radius: 3px;
    border: 1px solid transparent;
  }
  .multi-week-cell--ok {
    color: var(--text-muted, #555);
    background: color-mix(in srgb, var(--border, #ddd) 25%, transparent);
  }
  .multi-week-cell--fetching {
    color: var(--text-muted, #888);
    font-style: italic;
  }
  .multi-week-cell--error {
    color: var(--diff-rem, #c0392b);
    border-color: var(--diff-rem, #c0392b);
  }

  /* §16.2.7.t5 — per-section summary row. Same shape as multi-week
     but per-cell color stays neutral (mixing kind accents within a
     section cell would over-color). */
  .section-summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.4rem;
    padding: 0.4rem 0.75rem;
    border-bottom: 1px solid var(--border, #ddd);
    background: color-mix(in srgb, var(--bg-subtle, #fafafa) 50%, transparent);
    font-size: 0.78rem;
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
  }
  .section-summary-label {
    color: var(--text-muted, #666);
    font-weight: 500;
  }
  .section-summary-cell {
    padding: 0.1rem 0.35rem;
    border-radius: 3px;
    background: color-mix(in srgb, var(--border, #ddd) 25%, transparent);
    color: var(--text-muted, #555);
  }

  /* §16.2.7.t6 — per-row Revert button. Lives inside the .slot
     grid as a 6th-column overflow (grid-column: -1 / span 1 puts
     it at the end). Color via the row's parent class (.slot--*)
     so it picks up the kind accent. Disabled state grays out +
     not-allowed cursor. */
  .diff-revert {
    grid-column: -2 / -1;
    align-self: center;
    padding: 0.1rem 0.4rem;
    border: 1px solid currentColor;
    border-radius: 3px;
    background: transparent;
    font-size: 0.7rem;
    font-family: inherit;
    cursor: pointer;
    line-height: 1.2;
  }
  .slot--changed .diff-revert {
    color: var(--diff-warn, #b08c00);
  }
  .slot--added .diff-revert {
    color: var(--diff-add, #2c7a2c);
  }
  .slot--removed .diff-revert {
    color: var(--diff-rem, #c0392b);
  }
  .diff-revert:hover:not(:disabled) {
    background: color-mix(in srgb, currentColor 10%, transparent);
  }
  .diff-revert:focus-visible {
    outline: 2px solid currentColor;
    outline-offset: 1px;
  }
  .diff-revert--disabled,
  .diff-revert:disabled {
    color: var(--text-muted, #888) !important;
    border-color: var(--text-muted, #888);
    cursor: not-allowed;
    opacity: 0.6;
  }

  /* §16.2.7.t7 — Edits panel. Scrollable list capped at 25vh so
     50-op opLogs don't take over. Renders below the per-section
     summary. */
  .edits-panel {
    display: flex;
    flex-direction: column;
    padding: 0.4rem 0.75rem;
    border-bottom: 1px solid var(--border, #ddd);
    background: color-mix(in srgb, var(--bg-subtle, #fafafa) 60%, transparent);
    font-size: 0.78rem;
    flex-shrink: 0;
    max-height: 25vh;
    overflow-y: auto;
  }
  .edits-panel-label {
    color: var(--text-muted, #666);
    font-weight: 500;
    margin-bottom: 0.2rem;
  }
  .edits-panel-list {
    list-style: decimal inside;
    padding: 0;
    margin: 0;
  }
  .edits-panel-row {
    padding: 0.1rem 0;
    color: var(--text-muted, #444);
    line-height: 1.4;
  }
</style>
