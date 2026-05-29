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
  import { createEventDispatcher } from 'svelte';
  import DetailPanel from './DetailPanel.svelte';
  import Section from './Section.svelte';
  import ViewModeToggle from './ViewModeToggle.svelte';

  export let template: StationTemplate;
  export let sidecar: Sidecar;

  // §16.2.3 c2 — forward the DetailPanel's `save` event up to the
  // route, which owns the slug + optimistic token + the PATCH call.
  // The canvas stays persistence-agnostic.
  const dispatch = createEventDispatcher<{
    save: { ref: ExpandedRef; fields: Record<string, string | number | null> };
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

  {#each orderedSections as section (section.id)}
    <Section
      {section}
      {template}
      {sidecar}
      {viewMode}
      chipCount={sectionChipCounts.get(section.id) ?? 0}
      initialExpanded={initialExpanded}
      on:select={(e) => onSelect(e, section)}
    />
  {/each}

  {#if orderedSections.length === 0}
    <p class="empty" data-testid="empty-canvas">
      No sections to render. (Sidecar's <code>layout_order</code> is
      empty — author entries in <code>stations/{template.station}.editor.json</code>
      or regenerate with <code>make derive-sidecar STATION={template.station}</code>.)
    </p>
  {/if}
</div>

<DetailPanel
  {selected}
  {template}
  {sidecar}
  on:close={closeDetail}
  on:save={(e) => dispatch('save', e.detail)}
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
</style>
