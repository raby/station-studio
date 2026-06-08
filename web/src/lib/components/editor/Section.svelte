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
  import { expandSection, type Sidecar, type SidecarSection, type StationTemplate, type ViewModeState } from '$lib/editor';
  import ChipFlow from './ChipFlow.svelte';
  import SectionHeader from './SectionHeader.svelte';

  export let section: SidecarSection;
  export let template: StationTemplate;
  export let sidecar: Sidecar;
  export let viewMode: ViewModeState;
  export let initialExpanded: boolean = true;
  /**
   * Pick count for the header (§16.2.2.t5 — passed down from
   * EditorCanvas which computes all section counts in a single
   * reduce; previous design had each Section recompute via its
   * own `expandSection` call, doubling the work on large
   * stations). When called directly (testing in isolation),
   * fall back to a fresh computation.
   */
  export let chipCount: number | undefined = undefined;
  /** §16.2.5 c2 — Move boundaries; EditorCanvas computes from
   *  layout_order index. */
  export let canMoveUp: boolean = false;
  export let canMoveDown: boolean = false;

  let expanded = initialExpanded;

  // Use the passed-down count when available; otherwise recompute
  // (test-friendly fallback so callers can omit the prop).
  $: effectiveCount =
    chipCount !== undefined ? chipCount : expandSection(section, sidecar).length;

  function toggle() {
    expanded = !expanded;
  }
</script>

<section
  class="section"
  data-testid="section"
  data-section-id={section.id}
>
  <SectionHeader
    {section}
    chipCount={effectiveCount}
    {expanded}
    {canMoveUp}
    {canMoveDown}
    on:toggle={toggle}
    on:rename
    on:delete
    on:moveUp
    on:moveDown
    on:chipDrop
    on:sectionDrop
  />

  {#if expanded}
    <div
      class="section-body"
      id={`section-body-${section.id}`}
      data-testid="section-body"
    >
      <ChipFlow {section} {template} {sidecar} {viewMode} on:select on:chipDrop />
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
