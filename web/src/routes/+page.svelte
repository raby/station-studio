<script lang="ts">
  import { onMount } from 'svelte';
  import { getStations, ApiError } from '$lib/api';
  import type { Station } from '$lib/api';

  let stations: Station[] = [];
  let error: string | null = null;
  let loading = true;

  onMount(async () => {
    try {
      stations = await getStations();
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      loading = false;
    }
  });
</script>

<svelte:head>
  <title>Station Studio</title>
</svelte:head>

<main>
  <h1>Station Studio</h1>
  <p class="sub">
    Playlist-generator authoring tool. See <code>DESIGN.md</code> for the full spec.
  </p>

  <header class="section-head">
    <h2>Stations</h2>
    <a href="/stations/new" class="button">+ new station</a>
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
    <ul>
      {#each stations as s}
        <li>
          <a href="/stations/{s.slug}">{s.name}</a>
          <span class="slug">{s.slug}</span>
        </li>
      {/each}
    </ul>
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
  ul {
    list-style: none;
    padding: 0;
  }
  li {
    padding: 0.5rem 0;
    border-bottom: 1px solid #eee;
  }
  li a {
    text-decoration: none;
    font-weight: 500;
  }
  .slug {
    color: #888;
    font-family: ui-monospace, monospace;
    font-size: 0.85rem;
    margin-left: 0.5rem;
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
