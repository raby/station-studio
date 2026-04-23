<script>
  // Landing page stub. Fetches the station list from the FastAPI backend.
  import { onMount } from 'svelte';

  let stations = [];
  let error = null;

  onMount(async () => {
    try {
      const res = await fetch('/api/stations');
      stations = await res.json();
    } catch (e) {
      error = e.message;
    }
  });
</script>

<main>
  <h1>Station Studio</h1>
  <p>Playlist-generator authoring tool. See <code>DESIGN.md</code> for the full spec.</p>

  <h2>Stations</h2>
  {#if error}
    <p style="color: crimson">Could not reach backend: {error}</p>
    <p>Start it with <code>python run.py</code> from the repo root.</p>
  {:else if stations.length === 0}
    <p>No stations yet. Run the wizard at <a href="/stations/new">/stations/new</a> (TODO).</p>
  {:else}
    <ul>
      {#each stations as s}
        <li><a href="/stations/{s.slug}">{s.name}</a></li>
      {/each}
    </ul>
  {/if}
</main>

<style>
  main { font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }
  h1 { margin-bottom: 0.25rem; }
  code { background: #f4f4f4; padding: 0.1em 0.3em; border-radius: 3px; }
</style>
