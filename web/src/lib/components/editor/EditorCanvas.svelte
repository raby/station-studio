<script lang="ts">
  /**
   * <EditorCanvas> — §16.2.2 c3 root component for the read-only
   * visual block editor.
   *
   * Iterates `sidecar.layout_order` and renders one `<Section>` per
   * section id. The order is significant: the canvas's vertical
   * stack matches `layout_order`, which is what the sidecar author
   * decided is the intuitive top-to-bottom reading order. Sections
   * referenced by `layout_order` but absent from `sidecar.sections`
   * (drift caught at the engine's `validate_sidecar` time) are
   * skipped silently here — the warning banner in the route
   * already surfaced the error.
   *
   * Per-section initial expanded state:
   *
   *   * Total chips across the canvas ≤ 500 → every section starts
   *     EXPANDED. Small stations (Classics, Rebirth Radio's
   *     authored sidecar with 5 sections of various sizes) render
   *     the full canvas immediately.
   *   * Total chips > 500 → every section starts COLLAPSED. The
   *     user opts in by clicking section headers. Prevents a
   *     2,196-chip wall-of-flow on first paint (Rebirth Radio's
   *     synthesized-trivial fallback).
   *
   * The threshold is a per-canvas decision (not per-section) — even
   * a small section in a large station starts collapsed, by design:
   * uniformity helps the user discover that "open everything" is
   * one click each.
   */
  import {
    expandSection,
    readViewMode,
    writeViewMode,
    type ExpandedRef,
    type Sidecar,
    type SidecarSection,
    type StationTemplate,
    type ViewModeState,
  } from '$lib/editor';
  import type { StructuralBlockEdit } from '$lib/api';
  import { commitOnEnterEscapeBlur, focusOnMount } from '$lib/actions';
  import { isReservedIdPrefix } from '$lib/drag-drop';
  import { createEventDispatcher } from 'svelte';
  import DetailPanel from './DetailPanel.svelte';
  import Section from './Section.svelte';
  import ViewModeToggle from './ViewModeToggle.svelte';

  export let template: StationTemplate;
  export let sidecar: Sidecar;

  // §16.2.3 c2 + §16.2.4 c2 — forward the DetailPanel's write-path
  // events (atomic save, macro save, retype) up to the route, which
  // owns the slug + optimistic token + the PATCH calls. The canvas
  // stays persistence-agnostic.
  //
  // c2 review #2: field-value union widened to include `string[]`
  // (for allowlist_sources). DetailPanel + api.ts use the same union.
  type FieldValue = string | number | string[] | null;
  // c2 review #6: StructuralBlockEdit is the single source of truth
  // in $lib/api; EditorCanvas + DetailPanel + the route handler all
  // import it. No inline duplicates so a schema change in api.ts
  // can't silently desync the dispatch payload + the api call.
  const dispatch = createEventDispatcher<{
    save: { ref: ExpandedRef; fields: Record<string, FieldValue> };
    macroSave: { macroId: string; fields: Record<string, FieldValue> };
    retype: {
      ref: ExpandedRef;
      newStrategy: string | null;
      newSource: string | null;
      newFields: Record<string, FieldValue>;
    };
    // §16.2.5 c2 — section composition + structural edits.
    sectionCreate: { id: string; name: string | null };
    sectionRename: { sectionId: string; name: string };
    sectionDelete: { sectionId: string };
    sectionsReorder: { layoutOrder: string[] };
    blockMove: {
      ref: ExpandedRef;
      targetSectionId: string;
      targetIndex: number | null;
    };
    structuralEdit: { edit: StructuralBlockEdit };
    // §16.2.6 c3e c2 — D2(b) DetailPanel ↔ TracePane mutual-
    // exclusivity. EditorCanvas owns the `selected` state (and
    // therefore knows when DetailPanel is open); the route listens
    // for these events to hide its TracePane while DetailPanel
    // claims the side-panel real estate.
    detailOpen: { open: boolean };
  }>();

  // §16.2.2 c4 — view-mode prefs, hydrated from localStorage +
  // persisted on every change. SSR-safe: readViewMode() returns
  // defaults when window is undefined.
  let viewMode: ViewModeState = readViewMode();
  $: writeViewMode(viewMode);

  // Currently-selected chip for the DetailPanel. null = panel
  // closed. Section component bubbles `select` events up via the
  // forward; we annotate the ref with its owning section here
  // (DetailPanel renders the section name).
  let selected: { ref: ExpandedRef; section: SidecarSection } | null = null;

  // §16.2.6 c3e c2 — emit `detailOpen` whenever the selection
  // state flips. Reactive `$:` so any future code path that sets
  // `selected` (programmatic open from a query param, etc.) also
  // emits; keeping the dispatch out of the handlers avoids drift.
  $: dispatch('detailOpen', { open: selected !== null });

  function onSelect(e: CustomEvent<{ ref: ExpandedRef }>, section: SidecarSection) {
    selected = { ref: e.detail.ref, section };
  }
  function closeDetail() {
    selected = null;
  }

  /**
   * Total chips threshold. ≤ threshold → all sections start
   * expanded; > threshold → all start collapsed + show the
   * "Large station" hint. Default 500 per DESIGN.md §16.2.2
   * first-impression contract: Classics (~14 atomic via
   * synthesized trivial) sits well below; Rebirth Radio (2,196)
   * sits well above. Overridable from the route (or a test) for
   * stations that land in the awkward middle.
   */
  export let collapseThreshold: number = 500;

  // §16.2.2.t5 — compute per-section chip counts ONCE here and pass
  // them down. Previous design had EditorCanvas reduce + each Section
  // independently call expandSection again for its header — meaning
  // Rebirth Radio's synthesized-trivial (2,196 atomic blocks in one
  // section) ran the 2,196-element array build twice per render
  // cycle. Now once.
  $: sectionChipCounts = new Map(
    sidecar.sections.map((s) => [s.id, expandSection(s, sidecar).length]),
  );
  $: totalChips = [...sectionChipCounts.values()].reduce((a, b) => a + b, 0);
  $: initialExpanded = totalChips <= collapseThreshold;

  // Resolve sections in layout_order; skip ids absent from
  // sidecar.sections (validate_sidecar would have rejected at API
  // load, but defend defensively here too — the route's warning
  // banner already pointed the operator at the broken file).
  $: sectionsById = new Map(sidecar.sections.map((s) => [s.id, s]));
  $: orderedSections = sidecar.layout_order
    .map((id) => sectionsById.get(id))
    .filter((s): s is NonNullable<typeof s> => s !== undefined);

  // c2 review #9: events that just forward from DetailPanel up to
  // the route use Svelte's `on:eventname` shorthand (no handler) in
  // the template — no TS-annotated arrow-function wrappers needed.
  // The dispatcher above still declares each event's payload type
  // so consumers stay type-safe at the route handler.

  // §16.2.5 c2 — Move up / Move down → reorder layout_order by
  // swapping the section's slot with its neighbor.
  function _swap(order: string[], a: number, b: number): string[] {
    const next = order.slice();
    const tmp = next[a];
    next[a] = next[b];
    next[b] = tmp;
    return next;
  }
  function handleMoveUp(e: CustomEvent<{ sectionId: string }>) {
    const i = sidecar.layout_order.indexOf(e.detail.sectionId);
    if (i <= 0) return;
    dispatch('sectionsReorder', {
      layoutOrder: _swap(sidecar.layout_order, i - 1, i),
    });
  }
  function handleMoveDown(e: CustomEvent<{ sectionId: string }>) {
    const i = sidecar.layout_order.indexOf(e.detail.sectionId);
    if (i < 0 || i >= sidecar.layout_order.length - 1) return;
    dispatch('sectionsReorder', {
      layoutOrder: _swap(sidecar.layout_order, i, i + 1),
    });
  }

  // §16.2.5 c2 D6 — drop event from SectionHeader → blockMove.
  // The dropped payload is `unknown` because it came through JSON
  // round-trip + DataTransfer; type-narrow to an atomic ExpandedRef
  // before forwarding. Macro chips can't be dropped (Chip's
  // dragstart blocks the drag), so we expect only atomic-ref here;
  // a defensive check guards against future divergence.
  //
  // c2 review #3 fix #4: tighten the guard to require slot_index OR
  // block_idx — a payload like `{kind: 'atomic-ref'}` (no address)
  // previously passed the kind check and dispatched a malformed
  // blockMove. Today the route handler surfaces it as
  // 'cannot move this block (no storage address)' but the narrowing
  // is cleaner at the dispatch boundary.
  //
  // c2 review #3 fix #2: skip same-section drops. A drop on the
  // chip's OWN section is a no-op (server would remove then re-
  // append to the same blocks list, silently re-ordering). Find
  // the source section by walking sidecar.sections; skip the
  // dispatch if it matches the drop target.
  // §16.2.5.t1 — section-header drag-reorder. The dropped section
  // moves to BEFORE the dropped-on section in layout_order. Self-drop
  // is filtered at the SectionHeader level; we also filter
  // `_unsorted` (system bin can't move via reorder — pinned at end).
  function handleSectionDrop(
    e: CustomEvent<{ sourceSectionId: string; beforeSectionId: string }>,
  ) {
    const { sourceSectionId, beforeSectionId } = e.detail;
    if (sourceSectionId === beforeSectionId) return;
    // c3 review #1 finding #7: fall back to sidecar.sections.map
    // when layout_order is missing or partial (sections not in
    // layout_order render at the tail per the grammar). Without
    // this, indexOf returns -1 → silent early return → looks like
    // drag-reorder is broken even though ▲/▼ still works.
    const rawOrder = sidecar.layout_order ?? [];
    const allIds = sidecar.sections.map((s) => s.id);
    const missingFromOrder = allIds.filter((id) => !rawOrder.includes(id));
    const order = [...rawOrder, ...missingFromOrder];
    const srcIdx = order.indexOf(sourceSectionId);
    const dstIdx = order.indexOf(beforeSectionId);
    if (srcIdx < 0 || dstIdx < 0) return;
    // Build new order: remove source, insert at destination's slot.
    const next = order.slice();
    next.splice(srcIdx, 1);
    // After removal, recompute the dst index — the source's old
    // position may have shifted dst by 1 if source was before dst.
    const adjustedDst = srcIdx < dstIdx ? dstIdx - 1 : dstIdx;
    next.splice(adjustedDst, 0, sourceSectionId);
    if (next.join() === order.join()) return;  // no-op (same order)

    // c3 review #1 finding #8: re-pin `_unsorted` at the end. If
    // the bin was last in the original order (the documented
    // invariant), an arbitrary section drop could leave it
    // mid-list — server PUT /sections/order then 400s with
    // 'must stay at end'. Pop + push so the bin stays anchored.
    const unsortedIdx = next.indexOf('_unsorted');
    if (unsortedIdx >= 0 && unsortedIdx !== next.length - 1) {
      const wasLast = order[order.length - 1] === '_unsorted';
      if (wasLast) {
        next.splice(unsortedIdx, 1);
        next.push('_unsorted');
      }
    }
    dispatch('sectionsReorder', { layoutOrder: next });
  }

  function handleChipDrop(
    e: CustomEvent<{
      ref: unknown;
      targetSectionId: string;
      // §16.2.5.t1: optional. ChipFlow drops (within-section
      // granular) supply a computed index; SectionHeader drops
      // (drop on header bar = append) omit it.
      targetIndex?: number;
    }>,
  ) {
    const r = e.detail.ref;
    if (
      !r || typeof r !== 'object' ||
      (r as { kind?: unknown }).kind !== 'atomic-ref'
    ) {
      return;
    }
    const slotIndex = (r as { slot_index?: unknown }).slot_index;
    const blockIdx = (r as { block_idx?: unknown }).block_idx;
    if (typeof slotIndex !== 'number' && typeof blockIdx !== 'number') {
      return;
    }
    // Skip same-section drops UNLESS targetIndex is supplied (which
    // means reorder within the section — that's a legitimate move).
    // §16.2.5.t1: enables the within-section drag-reorder affordance.
    const sourceSection = sidecar.sections.find((s) =>
      (s.blocks ?? []).some((b) => {
        if (b.kind !== 'atomic') return false;
        if (typeof slotIndex === 'number') return b.slot_index === slotIndex;
        return b.block_idx === blockIdx;
      })
    );
    if (
      sourceSection &&
      sourceSection.id === e.detail.targetSectionId &&
      e.detail.targetIndex === undefined
    ) {
      return;  // same-section drop on the section header is a no-op
    }

    // c3 review #1 finding #1: same-section reorder off-by-one
    // adjustment. The server (api/routers/editor.py move_block)
    // removes the source from blocks FIRST then inserts at
    // target_index. So when source.idx < target.idx, the post-
    // removal blocks list is shifted by one and the chip lands one
    // slot LATER than the operator targeted. Mirror the section-
    // reorder compensation that handleSectionDrop applies via
    // `adjustedDst = srcIdx < dstIdx ? dstIdx - 1 : dstIdx`.
    let adjustedTargetIndex = e.detail.targetIndex;
    if (
      sourceSection &&
      sourceSection.id === e.detail.targetSectionId &&
      adjustedTargetIndex !== undefined
    ) {
      const blocks = sourceSection.blocks ?? [];
      const sourceBlockIdx = blocks.findIndex((b) => {
        if (b.kind !== 'atomic') return false;
        if (typeof slotIndex === 'number') return b.slot_index === slotIndex;
        return b.block_idx === blockIdx;
      });
      if (sourceBlockIdx >= 0 && sourceBlockIdx < adjustedTargetIndex) {
        adjustedTargetIndex = adjustedTargetIndex - 1;
      }
      // Skip dispatch if the adjusted target equals the source's
      // current index — that's a no-op same-position drop.
      if (adjustedTargetIndex === sourceBlockIdx) return;
    }

    dispatch('blockMove', {
      ref: r as ExpandedRef,
      targetSectionId: e.detail.targetSectionId,
      targetIndex: adjustedTargetIndex ?? null,
    });
  }

  // §16.2.5 c2 — Add Section: minimal modeless flow. A small inline
  // input lets the operator type an id; Enter / Add commits via
  // sectionCreate. The id becomes both the wire id AND the default
  // name (rename via the SectionHeader pencil after).
  let addingSection = false;
  let newSectionId = '';
  let addError: string | null = null;
  // c2 review #8: clear `addError` the moment the operator types
  // past the input that triggered the prior validation failure.
  // `errorForInput` snapshots which newSectionId value the error
  // belongs to; the reactive guard clears addError IFF the input
  // has diverged from that snapshot. Without this, a stale message
  // (e.g. "id can't start with `_`") stays visible until the next
  // click — operator can't tell whether their fix took.
  let errorForInput = '';
  $: if (addError !== null && newSectionId !== errorForInput) {
    addError = null;
  }

  function _setAddError(msg: string) {
    addError = msg;
    errorForInput = newSectionId;
  }

  function startAddSection() {
    addingSection = true;
    newSectionId = '';
    addError = null;
  }
  function cancelAddSection() {
    addingSection = false;
    addError = null;
  }
  function commitAddSection() {
    const trimmed = newSectionId.trim();
    if (trimmed === '') {
      _setAddError('id must not be empty');
      return;
    }
    // c1 review #3: forward-compat guard at CREATE — use the
    // prefix-only check so we block ANY underscore-prefixed id, not
    // just current system bins. The server's `is_reserved_id_prefix`
    // mirror enforces the same.
    if (isReservedIdPrefix(trimmed)) {
      _setAddError('id can\'t start with `_` (reserved for current + future system bins)');
      return;
    }
    if (sidecar.sections.some((s) => s.id === trimmed)) {
      _setAddError(`id "${trimmed}" already exists`);
      return;
    }
    dispatch('sectionCreate', { id: trimmed, name: null });
    addingSection = false;
  }
  // c4 review #1: migrated to the shared `commitOnEnterEscapeBlur`
  // action. Closes the silent-half-state bug the action's docstring
  // explicitly names — pre-fix, clicking-away mid-typing left
  // `addingSection = true` + the input visible but with no commit
  // path other than re-clicking Add/Cancel. Now blur commits via
  // the action.
</script>

<div
  class="editor-canvas"
  data-testid="editor-canvas"
  data-total-chips={totalChips}
  data-initially-collapsed={!initialExpanded}
>
  <ViewModeToggle bind:value={viewMode} />

  {#if totalChips > collapseThreshold}
    <p class="large-station-hint" data-testid="large-station-hint">
      Large station ({totalChips.toLocaleString()} picks total).
      Sections start collapsed; click a section header to expand.
    </p>
  {/if}

  {#each orderedSections as section, i (section.id)}
    <Section
      {section}
      {template}
      {sidecar}
      {viewMode}
      chipCount={sectionChipCounts.get(section.id) ?? 0}
      initialExpanded={initialExpanded}
      canMoveUp={i > 0}
      canMoveDown={i < orderedSections.length - 1}
      on:select={(e) => onSelect(e, section)}
      on:rename={(e) => dispatch('sectionRename', e.detail)}
      on:delete={(e) => dispatch('sectionDelete', e.detail)}
      on:chipDrop={handleChipDrop}
      on:sectionDrop={handleSectionDrop}
      on:moveUp={handleMoveUp}
      on:moveDown={handleMoveDown}
    />
  {/each}

  {#if orderedSections.length === 0}
    <p class="empty" data-testid="empty-canvas">
      No sections to render. (Sidecar's <code>layout_order</code> is
      empty — author entries in <code>stations/{template.station}.editor.json</code>
      or regenerate with <code>make derive-sidecar STATION={template.station}</code>.)
    </p>
  {/if}

  <!-- §16.2.5 c2 — Add Section affordance at the bottom of the canvas. -->
  <div class="add-section" data-testid="add-section-row">
    {#if addingSection}
      <input
        type="text"
        data-testid="add-section-input"
        placeholder="Section id (e.g. mornings)"
        bind:value={newSectionId}
        use:focusOnMount
        use:commitOnEnterEscapeBlur={{
          onCommit: () => commitAddSection(),
          onCancel: () => cancelAddSection(),
        }}
      />
      <button
        type="button"
        class="primary"
        data-testid="add-section-commit"
        on:click={commitAddSection}
      >Add</button>
      <button
        type="button"
        data-testid="add-section-cancel"
        on:click={cancelAddSection}
      >Cancel</button>
      {#if addError}
        <span class="err" data-testid="add-section-error">{addError}</span>
      {/if}
    {:else}
      <button
        type="button"
        class="primary"
        data-testid="add-section"
        on:click={startAddSection}
      >+ Add section</button>
    {/if}
  </div>
</div>

<DetailPanel
  {selected}
  {template}
  {sidecar}
  on:close={closeDetail}
  on:save
  on:macroSave
  on:retype
  on:blockMove
  on:structuralEdit
/>

<style>
  .editor-canvas {
    display: flex;
    flex-direction: column;
  }
  .large-station-hint {
    background: var(--bg-info, #eef6fb);
    border: 1px solid var(--border-info, #b8d8e8);
    border-radius: 4px;
    padding: 0.5rem 0.75rem;
    margin: 0 0 0.75rem;
    font-size: 0.85rem;
    color: var(--text-muted, #444);
  }
  .empty {
    color: var(--text-muted, #666);
    font-size: 0.9rem;
    font-style: italic;
  }
  code {
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
  }
  /* §16.2.5 c2 — Add Section affordance. */
  .add-section {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin: 0.6rem 0 0.3rem;
  }
  .add-section input {
    flex: 0 1 220px;
    padding: 0.3rem 0.5rem;
    border: 1px solid var(--border, #ccc);
    border-radius: 4px;
    font: inherit;
    font-size: 0.85rem;
  }
  .add-section button {
    padding: 0.3rem 0.7rem;
    border: 1px solid var(--border, #ccc);
    background: white;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .add-section button.primary {
    background: var(--accent, #2c7a7b);
    color: white;
    border-color: var(--accent, #2c7a7b);
  }
  .add-section .err {
    color: var(--error, #c0392b);
    font-size: 0.8rem;
  }
</style>
