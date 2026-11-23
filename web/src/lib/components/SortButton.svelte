<script lang="ts">
  /**
   * The button in a column header that sorts its table by that column.
   *
   * The table keeps the <th> (and its styles) and gives it
   * `aria-sort={ariaSort(sort, key)}`, so screen readers announce the
   * sorted column; this shows its arrow. The button's title says
   * what the next click does, in the column's own words ("newest
   * first", "A to Z"), since an arrow alone doesn't say which way
   * "up" is for a date.
   *
   * The table owns the state (see $lib/tableSort): this only shows it
   * and reports clicks with `on:sort`, passing the column's key.
   */
  import { createEventDispatcher } from 'svelte';
  import type { SortDir, SortState } from '$lib/tableSort';

  export let label: string;
  export let key: string;
  export let sort: SortState;
  /** The column's first-click direction, from its SortColumn. */
  export let firstDir: SortDir;
  /** How each direction reads for this column. */
  export let ascLabel = 'A to Z';
  export let descLabel = 'Z to A';
  /** A number column, right-aligned: the arrow goes before the label. */
  export let numeric = false;

  const dispatch = createEventDispatcher<{ sort: string }>();

  $: active = sort.key === key;
  // What a click does: flip the sorted column, or start this one its own way.
  $: nextDir = active ? (sort.dir === 'asc' ? 'desc' : 'asc') : firstDir;
  $: title = `Sort by ${label}, ${nextDir === 'asc' ? ascLabel : descLabel}`;
</script>

<button
  type="button"
  class="sort"
  class:active
  class:numeric
  {title}
  data-testid="sort-{key}"
  on:click={() => dispatch('sort', key)}
>
  {label}<span class="arrow" aria-hidden="true">{active ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
</button>

<style>
  .sort {
    all: unset;
    cursor: pointer;
    font: inherit;
    display: inline-flex;
    align-items: baseline;
    gap: 0.3rem;
    border-radius: 4px;
  }
  .sort:hover {
    color: var(--accent);
  }
  .sort:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .sort.numeric {
    flex-direction: row-reverse;
  }
  .arrow {
    font-size: 0.7em;
    min-width: 0.8em;
  }
  .active {
    color: var(--accent);
  }
</style>
