<script lang="ts">
  /**
   * <Section> — §16.2.2 c3.
   *
   * One semantic section in the visual canvas. Composes the header
   * (name + count + toggle) with the chip flow (the expanded
   * rendering). Owns local collapse state.
   *
   * Initial expanded state is controlled by the parent EditorCanvas
   * via the `initialExpanded` prop. Decision: per the design's
   * "collapsed by default if total chips > 500" rule, the canvas
   * decides whether each section starts open; this component just
   * tracks user-toggled state from there.
   */
  import { expandSection, type Sidecar, type SidecarSection, type StationTemplate } from '$lib/editor';
  import ChipFlow from './ChipFlow.svelte';
  import SectionHeader from './SectionHeader.svelte';

  export let section: SidecarSection;
  export let template: StationTemplate;
  export let sidecar: Sidecar;
  export let initialExpanded: boolean = true;

  let expanded = initialExpanded;

  // Chip count = total expanded refs in this section. Computed once
  // per (section, sidecar) — Svelte reactivity caches via the `$:`.
  // Includes EVERY placeholder a macro span emits (not the collapsed
  // first-only count) so the header reflects engine-level scale,
  // not visual chip-count.
  $: chipCount = expandSection(section, sidecar).length;

  function toggle() {
    expanded = !expanded;
  }
</script>

<section
  class="section"
  data-testid="section"
  data-section-id={section.id}
>
  <SectionHeader {section} {chipCount} {expanded} on:toggle={toggle} />

  {#if expanded}
    <div
      class="section-body"
      id={`section-body-${section.id}`}
      data-testid="section-body"
    >
      <ChipFlow {section} {template} {sidecar} />
    </div>
  {/if}
</section>

<style>
  .section {
    margin-bottom: 0.6rem;
  }
  .section-body {
    padding: 0.25rem 0.5rem;
  }
</style>
