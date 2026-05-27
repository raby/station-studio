<script lang="ts">
  /**
   * <SectionHeader> — §16.2.2 c3.
   *
   * One header bar above a section's ChipFlow. Renders:
   *   - section name (with fallback to id when name is absent)
   *   - chip count
   *   - collapse / expand toggle button
   *
   * Read-only at v1: clicking the toggle flips a boolean the parent
   * Section component owns. c4 / §16.2.5 (rename + reorder) grow
   * this with edit affordances.
   */
  import { createEventDispatcher } from 'svelte';
  import type { SidecarSection } from '$lib/editor';

  export let section: SidecarSection;
  export let chipCount: number;
  export let expanded: boolean;

  const dispatch = createEventDispatcher<{ toggle: void }>();

  // Display name falls back to id when omitted (mid-slice review
  // 🟡-2 made section.name optional per the Python grammar).
  $: displayName = section.name ?? section.id;
</script>

<header
  class="section-header"
  data-testid="section-header"
  data-section-id={section.id}
  data-expanded={expanded}
>
  <button
    type="button"
    class="toggle"
    aria-expanded={expanded}
    aria-controls={`section-body-${section.id}`}
    data-testid="section-toggle"
    on:click={() => dispatch('toggle')}
  >
    <span class="caret" aria-hidden="true">{expanded ? '▼' : '▶'}</span>
    <span class="name" data-testid="section-name">{displayName}</span>
    <span class="count" data-testid="section-count">
      <!--
        "picks" reflects the engine-scale count — macros contribute
        `macro.repeat` to the total, matching the ×N badge on a
        single visible chip. Slice review 🟡-2 (`5 atomic + 1
        macro×10` reads "15 picks", with 6 visible chips). "picks"
        is the unit a hobbyist editing the template thinks in:
        "how many tracks does this section produce?"
      -->
      {chipCount} pick{chipCount === 1 ? '' : 's'}
    </span>
  </button>
</header>

<style>
  .section-header {
    margin: 0.4rem 0 0.2rem;
  }
  .toggle {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    width: 100%;
    padding: 0.35rem 0.5rem;
    background: var(--bg-subtle, #fafafa);
    border: 1px solid var(--border, #ddd);
    border-radius: 4px;
    cursor: pointer;
    font-family: inherit;
    font-size: 0.9rem;
    text-align: left;
  }
  .toggle:hover {
    background: var(--bg-hover, #f0f0f0);
  }
  .caret {
    font-size: 0.7rem;
    color: var(--text-muted, #666);
    width: 1ch;
  }
  .name {
    font-weight: 600;
    flex: 1;
  }
  .count {
    color: var(--text-muted, #666);
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
  }
</style>
