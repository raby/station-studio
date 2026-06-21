<script lang="ts">
  /**
   * <MacroPalette> — §16.2.4.t1 browseable macro list.
   *
   * Renders every macro declared in the current sidecar as a
   * clickable row. Click → dispatches `select { macroId }` so the
   * parent can scroll-into-view + flash the first occurrence of
   * that macro on the canvas.
   *
   * UX decisions (locked at design pass):
   *   * Placement: toggle drawer above TracePane (parent owns the
   *     open/closed transition).
   *   * Click action: jump-to-canvas + flash. NO edit-from-sidebar
   *     in v1 — edit lands via clicking the macro chip on the
   *     canvas (existing flow). A future slice can add an "edit"
   *     affordance here when authors complain about the extra hop.
   *   * Empty state: friendly message + toggle stays visible
   *     (deliberate — discoverability matters more than chrome
   *     reduction at this scale).
   */
  import { createEventDispatcher } from 'svelte';
  import type { Sidecar } from '$lib/editor';
  import { compactStrategyLabel } from '$lib/editor';

  export let sidecar: Sidecar | null = null;

  const dispatch = createEventDispatcher<{
    select: { macroId: string };
  }>();

  $: macros = sidecar?.macros ?? [];

  function onSelect(macroId: string) {
    dispatch('select', { macroId });
  }
</script>

<div class="macro-palette" data-testid="macro-palette">
  <div class="header">
    <span class="title">Macros</span>
    <span class="count" data-testid="macro-palette-count">{macros.length}</span>
  </div>
  {#if macros.length === 0}
    <p class="empty" data-testid="macro-palette-empty">
      No macros yet. Group repeated blocks into a macro from the
      canvas to see them here.
    </p>
  {:else}
    <ul class="list">
      {#each macros as macro (macro.id)}
        <li>
          <button
            type="button"
            class="row"
            data-testid="macro-palette-row"
            data-macro-id={macro.id}
            on:click={() => onSelect(macro.id)}
          >
            <span class="name">{macro.name ?? macro.id}</span>
            <span class="meta">
              <span class="repeat">× {macro.repeat}</span>
              <span class="spec">{compactStrategyLabel(macro.spec)}</span>
            </span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .macro-palette {
    background: var(--color-panel-bg, #f8f8f8);
    border: 1px solid var(--color-panel-border, #ddd);
    border-radius: 6px;
    padding: 0.5rem;
    font-size: 0.85rem;
    max-height: 16rem;
    overflow-y: auto;
  }
  .header {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
    padding-bottom: 0.25rem;
    border-bottom: 1px solid var(--color-panel-border, #ddd);
  }
  .title {
    font-weight: 600;
  }
  .count {
    color: var(--color-text-muted, #666);
    font-size: 0.75rem;
  }
  .empty {
    color: var(--color-text-muted, #666);
    font-style: italic;
    margin: 0;
  }
  .list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .row {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.5rem;
    padding: 0.35rem 0.5rem;
    background: var(--color-row-bg, white);
    border: 1px solid var(--color-panel-border, #ddd);
    border-radius: 4px;
    cursor: pointer;
    font: inherit;
    text-align: left;
  }
  .row:hover {
    background: var(--color-row-hover, #eef);
  }
  .row:focus-visible {
    outline: 2px solid var(--color-focus, #3b82f6);
    outline-offset: 1px;
  }
  .name {
    font-weight: 500;
  }
  .meta {
    display: flex;
    gap: 0.5rem;
    color: var(--color-text-muted, #666);
    font-size: 0.75rem;
  }
  .repeat {
    font-variant-numeric: tabular-nums;
  }
  .spec {
    font-family: var(--font-mono, monospace);
  }
</style>
