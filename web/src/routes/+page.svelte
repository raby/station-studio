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

<main id="main-content" tabindex="-1">
  <header class="view-head">
    <div>
      <div class="kicker">Station Studio</div>
      <h1>Stations</h1>
      <p class="sub">Each station builds its playlists from your source pools, by its own rules.</p>
    </div>
    <div class="header-actions">
      <a href="/sources" class="btn" data-testid="nav-sources">
        <svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v6.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z" /></svg>
        Source pools
      </a>
      <a href="/stations/new" class="btn primary" data-testid="new-station">
        <svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14" /><path d="M5 12h14" /></svg>
        New station
      </a>
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
      <a href="/stations/new">New station</a> above.
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
      <ul class="cards" data-testid="station-list">
        {#each visible as s (s.slug)}
          {@const lastGen = lastGeneratedLabel(s)}
          <li class="card" data-testid="station-row" data-slug={s.slug}>
            <div class="row-main">
              <span class="glyph" aria-hidden="true">
                <svg class="i" viewBox="0 0 24 24"><circle cx="12" cy="12" r="2" /><path d="M16.24 7.76a6 6 0 0 1 0 8.48" /><path d="M7.76 16.24a6 6 0 0 1 0-8.48" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14" /><path d="M4.93 19.07a10 10 0 0 1 0-14.14" /></svg>
              </span>
              <div class="names">
                <a class="name" href="/stations/{s.slug}">{s.name}</a>
                <span class="slug">{s.slug}</span>
              </div>
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
    max-width: 1100px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .view-head {
    align-items: flex-end;
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    justify-content: space-between;
    margin-bottom: 18px;
  }
  .kicker {
    color: var(--accent);
    font-size: 12px;
    font-weight: 650;
    letter-spacing: 0.08em;
    margin-bottom: 4px;
    text-transform: uppercase;
  }
  h1 {
    font-size: 28px;
    margin: 0 0 4px;
  }
  .sub {
    color: var(--muted);
    margin: 0;
  }
  .header-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .btn {
    align-items: center;
    background: var(--panel-2);
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    color: var(--text);
    display: inline-flex;
    font-weight: 550;
    gap: 7px;
    min-height: 36px;
    padding: 7px 14px;
    text-decoration: none;
    white-space: nowrap;
  }
  .btn:hover {
    background: var(--panel-3);
    border-color: var(--faint);
    color: var(--text);
  }
  .btn.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--accent-text);
    font-weight: 650;
  }
  .btn.primary:hover {
    background: var(--accent-strong);
    border-color: var(--accent-strong);
  }
  svg.i {
    fill: none;
    flex: none;
    height: 17px;
    stroke: currentColor;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-width: 2;
    width: 17px;
  }
  svg.i circle {
    fill: currentColor;
    stroke: none;
  }
  .controls {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 0 0 18px;
    padding: 14px 16px;
  }
  .search {
    max-width: 360px;
    width: 100%;
  }
  .control-row {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .control-label {
    color: var(--faint);
    flex-shrink: 0;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    width: 6rem;
  }
  .chip {
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--muted);
    font-size: 13px;
    font-weight: 500;
    padding: 3px 11px;
  }
  .chip:hover:not(:disabled) {
    background: var(--panel-3);
    color: var(--text);
  }
  .chip.active,
  .chip.active:hover:not(:disabled) {
    background: var(--accent-soft);
    border-color: var(--accent);
    color: var(--text);
    font-weight: 600;
  }
  .cards {
    display: grid;
    gap: 14px;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px;
    position: relative;
    transition:
      border-color 0.15s,
      box-shadow 0.15s;
  }
  .card:hover {
    border-color: var(--border-strong);
    box-shadow: var(--shadow-sm);
  }
  .row-main {
    align-items: flex-start;
    display: flex;
    gap: 12px;
  }
  .glyph {
    align-items: center;
    background: var(--accent-soft);
    border-radius: 10px;
    color: var(--accent);
    display: inline-flex;
    flex: none;
    height: 40px;
    justify-content: center;
    width: 40px;
  }
  .names {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-width: 0;
  }
  .name {
    color: var(--text);
    font-size: 15px;
    font-weight: 650;
    text-decoration: none;
  }
  /* The whole card opens the station. */
  .name::after {
    content: '';
    inset: 0;
    position: absolute;
  }
  .name:hover {
    color: var(--accent);
  }
  .slug {
    color: var(--muted);
    font-family: var(--font-mono);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .cadence-tag {
    border: 1px solid var(--border-strong);
    border-radius: 6px;
    color: var(--muted);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.05em;
    padding: 1px 6px;
    text-transform: uppercase;
  }
  .last-gen {
    color: var(--muted);
    font-size: 13px;
  }
  .last-gen.muted {
    color: var(--faint);
  }
  .empty-filtered {
    color: var(--muted);
  }
  .error {
    color: var(--danger);
  }
</style>
