<script lang="ts">
  import { onMount } from 'svelte';
  import {
    getPlaylist,
    getTrace,
    getStation,
    exportPlaylistToDisk,
    ApiError,
  } from '$lib/api';
  import type { Playlist, Trace, TraceSlot, Station } from '$lib/api';
  import { page } from '$app/stores';

  let trace: Trace | null = null;
  let playlist: Playlist | null = null;
  let station: Station | null = null;
  let error: string | null = null;
  let loading = true;

  // Disk-export state (DESIGN.md §16.6 D3). Two reactive vars rather than a
  // single string-with-flag so the template can apply distinct styles cleanly.
  let diskExporting = false;
  let diskExportStatus: string | null = null;
  let diskExportError: string | null = null;

  $: id = Number($page.params.id);

  // Guard against non-numeric route params (e.g. /playlists/abc).
  // Number('abc') === NaN — catch it before the fetch so the error is
  // meaningful rather than a FastAPI 422 from an invalid path segment.
  $: if (isNaN(id)) {
    error = 'Invalid playlist ID';
    loading = false;
  }

  // Group slots by row. Each group carries its unit label (Mon/Tue/...).
  interface RowGroup {
    row: number;
    unit: string;
    slots: TraceSlot[];
  }
  let rowGroups: RowGroup[] = [];

  // Colour lookup for source chips — comes from the station template's
  // `sources` array. Fall back to a neutral grey if a source isn't listed
  // (e.g. reference-only pools that never appear in slots anyway).
  let sourceColours: Record<string, string> = {};

  // Per-row collapse state. Default: all rows expanded so the "your working"
  // is visible on page load. Click the header to collapse noisy rows.
  let collapsed: Record<number, boolean> = {};

  onMount(async () => {
    if (isNaN(id)) return; // reactive block above already set the error
    try {
      trace = await getTrace(id);
      // Playlist and station fetches aren't strictly needed for the trace
      // view, but they give us the station display name + source colours
      // without another lookup.
      playlist = await getPlaylist(id);
      station = await getStation(trace.station);

      // Build colour map from the template's `sources` list.
      const sources = (station.template.sources as { name: string; colour: string }[]) ?? [];
      sourceColours = Object.fromEntries(sources.map((s) => [s.name, s.colour]));

      rowGroups = groupByRow(trace.slots);
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  function groupByRow(slots: TraceSlot[]): RowGroup[] {
    const groups: Map<number, RowGroup> = new Map();
    for (const s of slots) {
      let g = groups.get(s.row);
      if (!g) {
        g = { row: s.row, unit: s.unit, slots: [] };
        groups.set(s.row, g);
      }
      g.slots.push(s);
    }
    return [...groups.values()].sort((a, b) => a.row - b.row);
  }

  function toggleRow(row: number) {
    collapsed[row] = !collapsed[row];
  }

  function fmtDate(iso: string | null | undefined): string {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString();
  }

  function chipStyle(source: string): string {
    const colour = sourceColours[source] ?? '#888';
    return `background: ${colour}; color: white;`;
  }

  /**
   * Disk-export handler (DESIGN.md §16.6). POST to the existing endpoint;
   * surface the returned `{path, bytes}` inline as a status line. The
   * backend translates "playlist not found" to 404; other server-side
   * IO problems (permission denied, directory missing, disk full) surface
   * as a generic 500 with FastAPI's default body. No confirmation —
   * disk-write is non-destructive in the §16.3 sense (overwrites only
   * its own file at a predictable path).
   */
  async function exportToDisk() {
    if (!playlist) return;
    diskExporting = true;
    diskExportStatus = null;
    diskExportError = null;
    try {
      const r = await exportPlaylistToDisk(playlist.id);
      diskExportStatus = `Wrote to ${r.path} (${formatBytes(r.bytes)})`;
    } catch (e) {
      diskExportError = e instanceof ApiError ? e.message : String(e);
    } finally {
      diskExporting = false;
    }
  }

  /**
   * Format a byte count for human display. Sub-KB values render as raw
   * bytes ("847 bytes") because rounding to KB at this scale yields "0 KB"
   * which reads like the write didn't happen. KB rendering kicks in at
   * ≥1024 bytes. Classics XMLs land around 850 KB so this branches once
   * for nearly every real export, but the small-file path matters for
   * future tiny stations / empty-week edge cases.
   */
  function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} bytes`;
    return `${Math.round(bytes / 1024).toLocaleString()} KB`;
  }
</script>

<svelte:head>
  <title>
    {station?.name ?? trace?.station ?? 'Playlist'}
    {trace ? String(trace.week).padStart(3, '0') : ''}
    — Station Studio
  </title>
</svelte:head>

<main>
  {#if trace}
    <p class="crumbs">
      <a href="/">stations</a> ›
      <a href="/stations/{trace.station}">{station?.name ?? trace.station}</a> ›
      week {String(trace.week).padStart(3, '0')}
    </p>
  {/if}

  {#if loading}
    <p>Loading…</p>
  {:else if error}
    <p class="error">{error}</p>
  {:else if trace && playlist}
    <h1>
      {station?.name ?? trace.station}
      <span class="week">{String(trace.week).padStart(3, '0')}</span>
    </h1>

    <dl class="meta">
      <dt>Slots</dt>
      <dd>{trace.slots.length}</dd>

      <dt>RNG</dt>
      <dd>{trace.rng}{trace.seed !== null ? ` · seed ${trace.seed}` : ''}</dd>

      <dt>Generated</dt>
      <dd>{fmtDate(trace.generated_at)}</dd>

      <dt>Trace</dt>
      <dd class="mono">{trace.trace_id}</dd>
    </dl>

    <div class="actions">
      <a href="/api/playlists/{playlist.id}/export" class="button">
        Download iTunes XML
      </a>
      <button
        type="button"
        class="button"
        data-testid="export-to-disk"
        disabled={diskExporting}
        on:click={exportToDisk}
      >
        {diskExporting ? 'Writing…' : 'Export to disk'}
      </button>
    </div>
    {#if diskExportStatus}
      <p class="disk-status success" data-testid="disk-export-status">
        {diskExportStatus}
      </p>
    {/if}
    {#if diskExportError}
      <p class="disk-status error" data-testid="disk-export-error">
        {diskExportError}
      </p>
    {/if}

    <h2>Your working</h2>
    <p class="hint">Each row below is one unit of the layout (a day for Classics). Click a row header to collapse it.</p>

    {#each rowGroups as g}
      <section class="row" class:collapsed={collapsed[g.row]}>
        <header
          on:click={() => toggleRow(g.row)}
          on:keydown={(e) => (e.key === 'Enter' || e.key === ' ') && toggleRow(g.row)}
          role="button"
          tabindex="0"
        >
          <span class="caret">{collapsed[g.row] ? '▸' : '▾'}</span>
          <span class="unit-label">{g.unit}</span>
          <span class="row-meta">Row {g.row} · {g.slots.length} slots</span>
        </header>

        {#if !collapsed[g.row]}
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Source</th>
                <th>Strategy</th>
                <th>Pool</th>
                <th>Track</th>
                <th>Artist</th>
              </tr>
            </thead>
            <tbody>
              {#each g.slots as s}
                <tr>
                  <td class="num">{s.slot}</td>
                  <td><span class="chip" style={chipStyle(s.source)}>{s.source}</span></td>
                  <td class="mono">{s.strategy}</td>
                  <td class="num mono" title={s.reason}>{s.pool_index}</td>
                  <td>{s.track_name}</td>
                  <td class="muted">{s.track_artist ?? ''}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/each}
  {/if}
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 1100px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .crumbs {
    font-size: 0.85rem;
    margin-bottom: 1rem;
    color: #666;
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
  .week {
    color: #888;
    font-variant-numeric: tabular-nums;
    font-weight: 400;
  }
  dl.meta {
    display: grid;
    grid-template-columns: auto 1fr;
    column-gap: 1rem;
    row-gap: 0.2rem;
    margin: 1rem 0 1.5rem;
    font-size: 0.9rem;
  }
  dl.meta dt {
    color: #666;
  }
  dl.meta dd {
    margin: 0;
  }
  .mono {
    font-family: ui-monospace, monospace;
    font-size: 0.85rem;
  }
  .actions {
    margin: 1rem 0 0.5rem;
    display: flex;
    gap: 0.5rem;
  }
  .button {
    display: inline-block;
    padding: 0.4rem 0.9rem;
    background: #2c3e50;
    color: white;
    text-decoration: none;
    border-radius: 4px;
    font-size: 0.9rem;
    border: none;
    cursor: pointer;
    font-family: inherit;
  }
  .button:hover:not(:disabled) {
    background: #1a252f;
  }
  .button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .disk-status {
    margin: 0 0 1.5rem;
    padding: 0.4rem 0.6rem;
    border-radius: 4px;
    font-size: 0.85rem;
    font-family: ui-monospace, monospace;
    word-break: break-all;
  }
  .disk-status.success {
    background: #e8f5e9;
    color: #1b5e20;
    border: 1px solid #c8e6c9;
  }
  .disk-status.error {
    background: #ffebee;
    color: #b71c1c;
    border: 1px solid #ffcdd2;
  }
  h2 {
    margin-top: 2rem;
    margin-bottom: 0.25rem;
  }
  .hint {
    color: #666;
    font-size: 0.85rem;
    margin-top: 0;
  }

  section.row {
    border: 1px solid #e4e4e4;
    border-radius: 4px;
    margin-bottom: 0.75rem;
    overflow: hidden;
  }
  section.row header {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem;
    background: #f7f7f7;
    cursor: pointer;
    user-select: none;
  }
  section.row header:hover {
    background: #efefef;
  }
  .caret {
    font-size: 0.8rem;
    color: #888;
    width: 1em;
  }
  .unit-label {
    font-weight: 600;
  }
  .row-meta {
    color: #888;
    font-size: 0.85rem;
  }

  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.9rem;
  }
  th,
  td {
    padding: 0.25rem 0.75rem;
    text-align: left;
    border-bottom: 1px solid #f1f1f1;
  }
  th {
    font-weight: 600;
    font-size: 0.8rem;
    color: #555;
    background: #fafafa;
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .muted {
    color: #888;
  }

  .chip {
    display: inline-block;
    padding: 0.05em 0.5em;
    border-radius: 3px;
    font-size: 0.75rem;
    font-family: ui-monospace, monospace;
    letter-spacing: 0.02em;
  }

  .error {
    color: crimson;
  }
</style>
