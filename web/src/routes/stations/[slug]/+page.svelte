<script lang="ts">
  import { onMount } from 'svelte';
  import { getStation, getStationPlaylists, ApiError } from '$lib/api';
  import type { Station, PlaylistSummary } from '$lib/api';
  import { page } from '$app/stores';

  let station: Station | null = null;
  let playlists: PlaylistSummary[] = [];
  let error: string | null = null;
  let loading = true;

  // SvelteKit guarantees `slug` is present because this route only matches
  // when the param exists, but the param type is `string | undefined`.
  $: slug = $page.params.slug ?? '';

  onMount(async () => {
    if (!slug) {
      error = 'missing slug';
      loading = false;
      return;
    }
    try {
      [station, playlists] = await Promise.all([
        getStation(slug),
        getStationPlaylists(slug),
      ]);
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  function fmtDate(iso: string | null): string {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }
</script>

<svelte:head>
  <title>{station?.name ?? slug} — Station Studio</title>
</svelte:head>

<main>
  <p class="crumbs"><a href="/">← stations</a></p>

  {#if loading}
    <p>Loading…</p>
  {:else if error}
    <p class="error">{error}</p>
  {:else if station}
    <h1>{station.name}</h1>
    <p class="slug">{station.slug}</p>

    <h2>Playlists</h2>
    {#if playlists.length === 0}
      <p>
        None generated yet. From the repo root:
        <code>python -m scripts.bootstrap_classics</code>.
      </p>
    {:else}
      <table>
        <thead>
          <tr>
            <th>Week</th>
            <th>Slots</th>
            <th>Generated</th>
            <th>Trace</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each playlists as p}
            <tr>
              <td class="num">{String(p.week).padStart(3, '0')}</td>
              <td class="num">{p.entry_count}</td>
              <td>{fmtDate(p.generated_at)}</td>
              <td class="mono">{p.trace_id.slice(0, 8)}…</td>
              <td><a href="/playlists/{p.id}">view →</a></td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 900px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .crumbs {
    font-size: 0.85rem;
    margin-bottom: 1rem;
  }
  .crumbs a {
    color: #666;
    text-decoration: none;
  }
  .crumbs a:hover {
    text-decoration: underline;
  }
  h1 {
    margin: 0;
  }
  .slug {
    color: #888;
    font-family: ui-monospace, monospace;
    margin-top: 0.1rem;
  }
  h2 {
    margin-top: 2rem;
  }
  table {
    border-collapse: collapse;
    width: 100%;
  }
  th,
  td {
    padding: 0.4rem 0.75rem;
    text-align: left;
    border-bottom: 1px solid #eee;
  }
  th {
    font-weight: 600;
    font-size: 0.85rem;
    color: #555;
    border-bottom: 2px solid #ddd;
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .mono {
    font-family: ui-monospace, monospace;
    font-size: 0.85rem;
    color: #666;
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
