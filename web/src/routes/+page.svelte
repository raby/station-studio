<script lang="ts">
  import { onMount } from 'svelte';
  import { getStations, ApiError } from '$lib/api';
  import type { StationSummary } from '$lib/api';
  import {
    DEFAULT_CONTROLS,
    availableCadences,
    filterSortStations,
    lastGeneratedLabel,
    type GenerationFilter,
    type SortBy,
    type StationListControls,
  } from '$lib/stations';

  let stations: StationSummary[] = [];
  let error: string | null = null;
  let loading = true;

  // §16.12 — list controls. Not persisted (session-scoped); the
  // list is short enough that a fresh default each visit is fine.
  let controls: StationListControls = { ...DEFAULT_CONTROLS };

  onMount(async () => {
    try {
      stations = await getStations();
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  $: cadences = availableCadences(stations);
  $: visible = filterSortStations(stations, controls);

  function setCadence(c: string) {
    controls = { ...controls, cadence: c };
  }
  function setGeneration(g: GenerationFilter) {
    controls = { ...controls, generation: g };
  }
  function setSort(s: SortBy) {
    controls = { ...controls, sortBy: s };
  }
</script>

<svelte:head>
  <title>Station Studio</title>
</svelte:head>

<main id="main-content">
  <h1>Station Studio</h1>
  <p class="sub">
    Playlist-generator authoring tool. See <code>DESIGN.md</code> for the full spec.
  </p>

  <header class="section-head">
    <h2>Stations</h2>
    <div class="header-actions">
      <a href="/sources" class="link-secondary" data-testid="nav-sources">
        Source pools →
      </a>
      <a href="/stations/new" class="button">+ new station</a>
    </div>
  </header>

  {#if loading}
    <p>Loading…</p>
  {:else if error}
    <p class="error">Could not reach backend: {error}</p>
    <p>Start it with <code>python run.py</code> from the repo root.</p>
  {:else if stations.length === 0}
    <p>
      No stations yet. Bootstrap Rebirth Classics with
      <code>python -m scripts.bootstrap_classics</code>, or use
      <a href="/stations/new">+ new station</a> above.
    </p>
  {:else}
    <!-- §16.12 controls: search + filter chips + sort. -->
    <div class="controls" data-testid="list-controls">
      <input
        type="search"
        class="search"
        placeholder="Search name or slug…"
        aria-label="Search stations by name or slug"
        data-testid="station-search"
        bind:value={controls.search}
      />

      <div class="control-row" role="group" aria-label="Filter by cadence">
        <span class="control-label">Cadence</span>
        <button
          type="button" class="chip" class:active={controls.cadence === 'all'}
          aria-pressed={controls.cadence === 'all'}
          data-testid="cadence-all" on:click={() => setCadence('all')}
        >all</button>
        {#each cadences as c}
          <button
            type="button" class="chip" class:active={controls.cadence === c}
            aria-pressed={controls.cadence === c}
            data-testid="cadence-{c}" on:click={() => setCadence(c)}
          >{c}</button>
        {/each}
      </div>

      <div class="control-row" role="group" aria-label="Filter by generation status">
        <span class="control-label">Generated</span>
        <button
          type="button" class="chip" class:active={controls.generation === 'all'}
          aria-pressed={controls.generation === 'all'}
          data-testid="gen-all" on:click={() => setGeneration('all')}
        >all</button>
        <button
          type="button" class="chip" class:active={controls.generation === 'has'}
          aria-pressed={controls.generation === 'has'}
          data-testid="gen-has" on:click={() => setGeneration('has')}
        >has generations</button>
        <button
          type="button" class="chip" class:active={controls.generation === 'empty'}
          aria-pressed={controls.generation === 'empty'}
          data-testid="gen-empty" on:click={() => setGeneration('empty')}
        >empty</button>
      </div>

      <div class="control-row" role="group" aria-label="Sort order">
        <span class="control-label">Sort</span>
        <button
          type="button" class="chip" class:active={controls.sortBy === 'name'}
          aria-pressed={controls.sortBy === 'name'}
          data-testid="sort-name" on:click={() => setSort('name')}
        >name</button>
        <button
          type="button" class="chip" class:active={controls.sortBy === 'last-generated'}
          aria-pressed={controls.sortBy === 'last-generated'}
          data-testid="sort-last-generated" on:click={() => setSort('last-generated')}
        >last generated</button>
        <button
          type="button" class="chip" class:active={controls.sortBy === 'updated'}
          aria-pressed={controls.sortBy === 'updated'}
          data-testid="sort-updated" on:click={() => setSort('updated')}
        >recently edited</button>
      </div>
    </div>

    {#if visible.length === 0}
      <p class="empty-filtered" data-testid="no-matches">
        No stations match the current search / filters.
      </p>
    {:else}
      <ul data-testid="station-list">
        {#each visible as s (s.slug)}
          {@const lastGen = lastGeneratedLabel(s)}
          <li data-testid="station-row" data-slug={s.slug}>
            <div class="row-main">
              <a href="/stations/{s.slug}">{s.name}</a>
              <span class="slug">{s.slug}</span>
              <span class="cadence-tag">{s.cadence}</span>
            </div>
            <div class="row-meta">
              {#if lastGen}
                <span class="last-gen" data-testid="last-generated">
                  Last generated: {lastGen}
                </span>
              {:else}
                <span class="last-gen muted" data-testid="never-generated">
                  Never generated
                </span>
              {/if}
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 720px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  h1 {
    margin-bottom: 0.25rem;
  }
  .sub {
    color: #666;
    margin-top: 0;
  }
  .section-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-top: 2rem;
  }
  .section-head h2 {
    margin: 0;
  }
  .header-actions {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  .link-secondary {
    color: #2563eb;
    text-decoration: none;
    font-size: 0.9rem;
  }
  .link-secondary:hover {
    text-decoration: underline;
  }
  .button {
    display: inline-block;
    padding: 0.35rem 0.8rem;
    background: #2c3e50;
    color: white;
    text-decoration: none;
    border-radius: 4px;
    font-size: 0.85rem;
  }
  .button:hover {
    background: #1a252f;
  }
  .controls {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin: 1rem 0;
    padding: 0.75rem;
    background: #fafafa;
    border: 1px solid #eee;
    border-radius: 6px;
  }
  .search {
    padding: 0.4rem 0.6rem;
    border: 1px solid #ccc;
    border-radius: 4px;
    font: inherit;
    font-size: 0.9rem;
  }
  .control-row {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    flex-wrap: wrap;
  }
  .control-label {
    color: #666;
    font-size: 0.8rem;
    width: 4.5rem;
    flex-shrink: 0;
  }
  .chip {
    padding: 0.15rem 0.55rem;
    background: white;
    border: 1px solid #ccc;
    border-radius: 999px;
    cursor: pointer;
    font: inherit;
    font-size: 0.8rem;
    color: #555;
  }
  .chip:hover {
    background: #f0f0f0;
  }
  .chip.active {
    background: #2c7a7b;
    color: white;
    border-color: #2c7a7b;
  }
  ul {
    list-style: none;
    padding: 0;
  }
  li {
    padding: 0.6rem 0;
    border-bottom: 1px solid #eee;
  }
  .row-main {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }
  .row-main a {
    text-decoration: none;
    font-weight: 500;
  }
  .slug {
    color: #888;
    font-family: ui-monospace, monospace;
    font-size: 0.85rem;
  }
  .cadence-tag {
    margin-left: auto;
    font-size: 0.72rem;
    color: #555;
    background: #eee;
    padding: 0.05rem 0.4rem;
    border-radius: 3px;
  }
  .row-meta {
    margin-top: 0.2rem;
  }
  .last-gen {
    font-size: 0.8rem;
    color: #666;
  }
  .last-gen.muted {
    color: #aaa;
    font-style: italic;
  }
  .empty-filtered {
    color: #888;
    font-style: italic;
  }
  .error {
    color: crimson;
  }
  code {
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
  }
</style>
