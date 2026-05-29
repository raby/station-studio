<script lang="ts">
  import {
    getPlaylist,
    getTrace,
    getStation,
    getStationPlaylists,
    exportPlaylistToDisk,
    ApiError,
  } from '$lib/api';
  import type { Playlist, PlaylistSummary, Trace, TraceSlot, Station } from '$lib/api';
  import { page } from '$app/stores';

  let trace: Trace | null = null;
  let playlist: Playlist | null = null;
  let station: Station | null = null;
  let stationPlaylists: PlaylistSummary[] = [];
  let error: string | null = null;
  let loading = true;

  // Disk-export state (DESIGN.md §16.6 D3). Two reactive vars rather than a
  // single string-with-flag so the template can apply distinct styles cleanly.
  let diskExporting = false;
  let diskExportStatus: string | null = null;
  let diskExportError: string | null = null;

  // Filter state (DESIGN.md §16.8). Three independent inputs that AND
  // together (D3); empty string = no constraint, not wildcard.
  let searchQuery = '';
  let sourceFilter = '';
  let strategyFilter = '';

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

  /**
   * Per-slot excluded-candidates disclosure state (§14.23). Keyed by slot
   * number (1-indexed). Default: collapsed — excluded lists can be long
   * (a weighted pick over 200 candidates) and most authoring questions are
   * answered by the picked slot's `reason`. Click "N rejected" to expand.
   */
  let excludedOpen: Record<number, boolean> = {};
  function toggleExcluded(slot: number) {
    excludedOpen[slot] = !excludedOpen[slot];
  }

  // §16.17 c3 — expand-all / collapse-all rejections. For a 2,196-slot
  // trace, flipping every slot's disclosure one-by-one is impractical;
  // this control opens (or closes) them all at once for a debugging
  // sweep. Only the slots that HAVE rejections are touched.
  // §16.17 c4 — per-trace summary: how many slots had contested picks.
  // Lets the operator spot at a glance whether (and roughly where) a
  // trace had rejections without scrolling a 2,196-slot table.
  $: excludedSlotCount = trace
    ? trace.slots.filter((s) => (s.excluded?.length ?? 0) > 0).length
    : 0;
  $: anyExcluded = excludedSlotCount > 0;
  // "All expanded" iff every rejection-bearing slot is open. None-open
  // or a mix → false → the button offers "Expand all".
  $: allExcludedExpanded = trace
    ? trace.slots
        .filter((s) => (s.excluded?.length ?? 0) > 0)
        .every((s) => !!excludedOpen[s.slot])
    : false;
  function toggleAllExcluded() {
    if (!trace) return;
    const target = !allExcludedExpanded;
    const next: Record<number, boolean> = { ...excludedOpen };
    for (const s of trace.slots) {
      if ((s.excluded?.length ?? 0) > 0) next[s.slot] = target;
    }
    excludedOpen = next;
  }

  /**
   * Generation counter for stale-write protection (review S1). Each
   * loadAll() invocation captures its own generation; if a later
   * invocation has bumped the counter by the time an await resolves,
   * the older invocation bails out before writing to component state.
   * Without this, rapid prev/next clicks could let a slow getTrace(3)
   * resolve AFTER getTrace(4) — leaving trace=trace3 even though the
   * URL is /playlists/4.
   */
  let loadGeneration = 0;

  /**
   * Fetch the trace + sibling data for a given playlist id. Extracted from
   * onMount so it can also be re-fired when the user navigates between
   * sibling weeks via §16.9's prev/next links — SvelteKit reuses the same
   * component for `/playlists/3` → `/playlists/4` (same route), so onMount
   * only fires once on the initial load. The reactive watcher below
   * re-invokes loadAll() whenever the id changes; without that, navigation
   * to a sibling week leaves the previous week's data on screen.
   *
   * Each await is followed by a generation check (review S1) so that a
   * stale invocation can't overwrite state that a newer invocation has
   * already updated.
   */
  async function loadAll(playlistId: number) {
    if (isNaN(playlistId)) return;
    const myGen = ++loadGeneration;

    // Initial load (no trace yet) shows the "Loading…" branch. Subsequent
    // navigations between sibling weeks keep the previous week's content
    // visible until the atomic swap below — no flicker, no blank-screen
    // flash on every prev/next click (review N3). Stale data with the
    // URL already updated reads as "transitioning"; the new data lands
    // atomically when all fetches resolve.
    const isInitialLoad = trace === null;
    if (isInitialLoad) loading = true;
    error = null;

    try {
      // getTrace must come first — its `station` field is the slug we
      // need for the next three fetches. Once we have it, the playlist /
      // station / playlist-list fetches are independent and parallelise
      // (review N2). Buffer all results in locals; swap to component
      // state at the end so reloads don't flash a half-state where the
      // header is week 4 but the rows are still week 3.
      const t = await getTrace(playlistId);
      if (myGen !== loadGeneration) return;

      // The playlist list is non-essential — if the endpoint flakes,
      // still render the trace; the nav strip just won't appear (S2).
      // .catch inside the Promise.all so a flaky list endpoint doesn't
      // poison the others.
      const [p, s, list] = await Promise.all([
        getPlaylist(playlistId),
        getStation(t.station),
        getStationPlaylists(t.station).catch(() => [] as PlaylistSummary[]),
      ]);
      if (myGen !== loadGeneration) return;

      // Atomic swap. All component state updates here, after every fetch
      // has resolved. Stale content stays visible during the transition;
      // new content snaps in as one. The {:else if trace && playlist}
      // gate flips the render path on the same tick.
      const sources = (s.template.sources as { name: string; colour: string }[]) ?? [];
      trace = t;
      playlist = p;
      station = s;
      stationPlaylists = list;
      sourceColours = Object.fromEntries(sources.map((src) => [src.name, src.colour]));
      rowGroups = groupByRow(t.slots);
    } catch (e) {
      if (myGen !== loadGeneration) return;
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      if (myGen === loadGeneration) loading = false;
    }
  }

  // Reactive: re-fetch whenever the route param changes. Fires once on
  // initial mount AND on every subsequent navigation between sibling
  // playlists. The `void` annotation is just to acknowledge the dropped
  // promise — errors land in `error` via loadAll's catch.
  $: if (!isNaN(id)) void loadAll(id);

  // ---- prev / next sibling derivation (DESIGN.md §16.9 D2) ---------------
  // Sort the station's playlists by week, find current's index, derive
  // neighbours. Returns nulls when no neighbour exists (boundaries) or
  // when the current playlist is somehow missing from the list (defensive
  // — shouldn't happen but a malformed list shouldn't crash the page).

  $: siblings = (() => {
    // Capture into a local so TS narrowing carries through the
    // findIndex closure (otherwise svelte-check sees `playlist`
    // as `Playlist | null` inside the arrow).
    const current = playlist;
    if (!current || stationPlaylists.length === 0) {
      return { prev: null as PlaylistSummary | null, next: null as PlaylistSummary | null };
    }
    const sorted = [...stationPlaylists].sort((a, b) => a.week - b.week);
    const idx = sorted.findIndex((p) => p.id === current.id);
    if (idx < 0) return { prev: null, next: null };
    return {
      prev: idx > 0 ? sorted[idx - 1] : null,
      next: idx < sorted.length - 1 ? sorted[idx + 1] : null,
    };
  })();

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

  // ---- filter logic (DESIGN.md §16.8) -----------------------------------
  // Pure derivations off `rowGroups`; `trace.slots` is never mutated.
  // Source / strategy options come from the trace's actual content rather
  // than the station template — handles renamed / removed pools gracefully.

  $: sourceOptions = trace
    ? [...new Set(trace.slots.map((s) => s.source))].sort()
    : [];
  $: strategyOptions = trace
    ? [...new Set(trace.slots.map((s) => s.strategy))].sort()
    : [];

  // Filtered groups: each group's slots narrowed by the predicate, then
  // groups with 0 surviving slots dropped (D8). The predicate is INLINED
  // (rather than a named function) because Svelte's `$:` dependency
  // tracking introspects the AST of the reactive block, not the bodies
  // of functions it calls. With a named `matchesFilters(slot)` reading
  // `searchQuery` etc. via closure, the compiler sees only the function
  // name and `rowGroups` — searchQuery changes wouldn't re-fire the
  // reactive. Inlining makes every variable read visible to the tracker.
  $: filteredRowGroups = (() => {
    const q = searchQuery.trim().toLowerCase();
    return rowGroups
      .map((g) => ({
        ...g,
        slots: g.slots.filter((s) => {
          if (sourceFilter && s.source !== sourceFilter) return false;
          if (strategyFilter && s.strategy !== strategyFilter) return false;
          if (q) {
            const trackHit = s.track_name.toLowerCase().includes(q);
            const artistHit = (s.track_artist ?? '').toLowerCase().includes(q);
            if (!trackHit && !artistHit) return false;
          }
          return true;
        }),
      }))
      .filter((g) => g.slots.length > 0);
  })();

  $: totalSlotCount = trace?.slots.length ?? 0;
  $: visibleSlotCount = filteredRowGroups.reduce((a, g) => a + g.slots.length, 0);
  $: filtersActive = !!(searchQuery.trim() || sourceFilter || strategyFilter);

  function clearFilters() {
    // Reset all three filter states atomically — the empty-state copy and
    // the dedicated "Clear" button share this handler so there's one
    // source of truth for "reset filter state".
    searchQuery = '';
    sourceFilter = '';
    strategyFilter = '';
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

    <!--
      Multi-week navigation (DESIGN.md §16.9). Per D5 the prev/next are
      <a href> not <button>s — they're navigations, not actions, so
      browser middle-click / open-in-new-tab work as expected. Per D3
      the disabled-edge state is a styled <span> rather than an
      anchor with no href (avoids a focusable "navigate to nowhere").
      Per D4 we show the week number, not just an arrow — context
      beats brevity at this scale.
    -->
    {#if siblings.prev || siblings.next}
      <nav class="week-nav" data-testid="week-nav" aria-label="adjacent weeks">
        {#if siblings.prev}
          <a
            href="/playlists/{siblings.prev.id}"
            class="week-nav-link"
            data-testid="week-nav-prev"
          >
            ← week {String(siblings.prev.week).padStart(3, '0')}
          </a>
        {:else}
          <span class="week-nav-link disabled" data-testid="week-nav-prev-disabled">
            ← (first week)
          </span>
        {/if}
        {#if siblings.next}
          <a
            href="/playlists/{siblings.next.id}"
            class="week-nav-link"
            data-testid="week-nav-next"
          >
            week {String(siblings.next.week).padStart(3, '0')} →
          </a>
        {:else}
          <span class="week-nav-link disabled" data-testid="week-nav-next-disabled">
            (last week) →
          </span>
        {/if}
      </nav>
    {/if}

    <dl class="meta">
      <dt>Slots</dt>
      <dd>{trace.slots.length}</dd>

      <dt>RNG</dt>
      <dd>{trace.rng}{trace.seed !== null ? ` · seed ${trace.seed}` : ''}</dd>

      <dt>Generated</dt>
      <dd>{fmtDate(trace.generated_at)}</dd>

      <dt>Trace</dt>
      <dd class="mono">{trace.trace_id}</dd>

      {#if anyExcluded}
        <!-- §16.17 c4: only shown when there ARE rejections — for
             window/pick-only traces (Classics) this stays absent
             rather than displaying a noisy "0". -->
        <dt>Rejections</dt>
        <dd data-testid="rejections-summary">
          {excludedSlotCount.toLocaleString()}
          {excludedSlotCount === 1 ? 'slot has' : 'slots have'} rejected candidates
        </dd>
      {/if}
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

    <!--
      Filter bar (DESIGN.md §16.8). Three independent inputs that AND
      together (D3); options for source / strategy come from the trace's
      actual content. Counter shows "Showing N of M" with locale-aware
      thousands separator (D10). Clear button only renders when at least
      one filter is active.
    -->
    <div class="filters" data-testid="filter-bar">
      <input
        type="text"
        placeholder="Search track or artist…"
        data-testid="filter-search"
        autocomplete="off"
        bind:value={searchQuery}
      />
      <select data-testid="filter-source" bind:value={sourceFilter}>
        <option value="">All sources</option>
        {#each sourceOptions as opt}
          <option value={opt}>{opt}</option>
        {/each}
      </select>
      <select data-testid="filter-strategy" bind:value={strategyFilter}>
        <option value="">All strategies</option>
        {#each strategyOptions as opt}
          <option value={opt}>{opt}</option>
        {/each}
      </select>
      <span class="counter" data-testid="filter-counter">
        Showing {visibleSlotCount.toLocaleString()} of {totalSlotCount.toLocaleString()}
      </span>
      {#if filtersActive}
        <button
          type="button"
          class="clear-btn"
          data-testid="filter-clear"
          on:click={clearFilters}
        >
          Clear
        </button>
      {/if}
      {#if anyExcluded}
        <!-- §16.17 c3: flip every slot's "Why not these?" at once. -->
        <button
          type="button"
          class="clear-btn"
          data-testid="toggle-all-excluded"
          on:click={toggleAllExcluded}
        >
          {allExcludedExpanded ? 'Collapse all rejections' : 'Expand all rejections'}
        </button>
      {/if}
    </div>

    {#if filtersActive && visibleSlotCount === 0}
      <!--
        <div> not <p>: a <button> inside <p> is "interactive in flow text"
        and reads weirdly to assistive tech. Same visual; cleaner semantics.
      -->
      <div class="empty-filter" data-testid="filter-empty" role="status">
        No slots match the current filters.
        <button type="button" class="clear-btn" on:click={clearFilters}>
          Clear filters
        </button>
      </div>
    {:else}
    <!--
      Keyed iteration — bind row identity to `g.row` (a stable integer)
      not to the array index, which shifts when filters hide rows. Future-
      proofs against §14.5's virtualisation punt where keyed `{#each}`
      becomes load-bearing.
    -->
    {#each filteredRowGroups as g (g.row)}
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
                  <td>
                    {s.track_name}
                    <!--
                      §14.23 excluded-candidates disclosure. Only renders when
                      a strategy populated the list (currently empty for
                      window/pick/sequential — lights up with `weighted` and
                      dedup-aware strategies). Inline button + sub-table so
                      the rejection rationale stays anchored to the picked
                      slot it explains.
                    -->
                    {#if s.excluded && s.excluded.length > 0}
                      <button
                        type="button"
                        class="excluded-toggle"
                        data-testid="excluded-toggle"
                        aria-expanded={!!excludedOpen[s.slot]}
                        on:click={() => toggleExcluded(s.slot)}
                      >
                        <!-- §16.17 c2: "Why not these? (N)" reads more
                             discoverably than the terse "N rejected" +
                             matches the design's wording. -->
                        {excludedOpen[s.slot] ? '▾' : '▸'} Why not these? ({s.excluded.length})
                      </button>
                    {/if}
                  </td>
                  <td class="muted">{s.track_artist ?? ''}</td>
                </tr>
                {#if s.excluded && s.excluded.length > 0 && excludedOpen[s.slot]}
                  <tr class="excluded-row" data-testid="excluded-row">
                    <td></td>
                    <td colspan="5">
                      <table class="excluded-table">
                        <thead>
                          <tr>
                            <th>Pool</th>
                            <th>Track</th>
                            <th>Artist</th>
                            <th>Why rejected</th>
                          </tr>
                        </thead>
                        <tbody>
                          {#each s.excluded as ex}
                            <tr>
                              <td class="num mono">{ex.pool_index}</td>
                              <td>{ex.track_name}</td>
                              <td class="muted">{ex.track_artist ?? ''}</td>
                              <td class="muted">{ex.reason}</td>
                            </tr>
                          {/each}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                {/if}
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/each}
    {/if}
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
  /* §16.9 multi-week prev/next nav. Sits between the h1 and the meta dl. */
  .week-nav {
    display: flex;
    gap: 0.75rem;
    margin: 0.4rem 0 1rem;
    font-size: 0.85rem;
  }
  .week-nav-link {
    display: inline-block;
    padding: 0.25rem 0.6rem;
    background: #f4f4f4;
    border: 1px solid #e0e0e0;
    border-radius: 3px;
    color: #2c3e50;
    text-decoration: none;
    font-variant-numeric: tabular-nums;
  }
  .week-nav-link:hover {
    background: #e8e8e8;
  }
  .week-nav-link.disabled {
    color: #999;
    background: #fafafa;
    cursor: not-allowed;
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

  /* ---- §16.8 filter bar ---------------------------------------------- */
  .filters {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
    margin: 0.5rem 0 1rem;
    padding: 0.5rem 0.75rem;
    background: #fafafa;
    border: 1px solid #e4e4e4;
    border-radius: 4px;
    font-size: 0.9rem;
  }
  .filters input[type='text'] {
    flex: 1 1 14rem;
    min-width: 12rem;
    padding: 0.4rem 0.6rem;
    border: 1px solid #ccc;
    border-radius: 3px;
    font: inherit;
  }
  .filters select {
    padding: 0.4rem 0.6rem;
    border: 1px solid #ccc;
    border-radius: 3px;
    font: inherit;
    background: white;
  }
  .filters .counter {
    margin-left: auto;
    color: #666;
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
  }
  .clear-btn {
    padding: 0.3rem 0.7rem;
    background: white;
    border: 1px solid #ccc;
    border-radius: 3px;
    cursor: pointer;
    font: inherit;
    font-size: 0.85rem;
  }
  .clear-btn:hover {
    background: #f4f4f4;
  }

  .empty-filter {
    margin: 1rem 0;
    padding: 1.25rem;
    background: #fafafa;
    border: 1px solid #e4e4e4;
    border-radius: 4px;
    text-align: center;
    color: #666;
  }
  .empty-filter .clear-btn {
    margin-left: 0.5rem;
  }

  /* ---- §14.23 excluded-candidates disclosure ------------------------- */
  .excluded-toggle {
    margin-left: 0.5rem;
    padding: 0 0.35rem;
    background: transparent;
    border: 1px solid #d8d8d8;
    border-radius: 3px;
    color: #888;
    font: inherit;
    font-size: 0.75rem;
    cursor: pointer;
    vertical-align: baseline;
  }
  .excluded-toggle:hover {
    background: #f4f4f4;
    color: #555;
  }
  .excluded-row > td {
    /* Strip the default row border so the sub-table reads as a
       continuation of the slot above, not a separate row. */
    border-bottom: none;
    background: #fafafa;
    padding: 0.4rem 0.5rem 0.6rem;
  }
  .excluded-table {
    font-size: 0.82rem;
    background: white;
    border: 1px solid #ececec;
    border-radius: 3px;
  }
  .excluded-table th {
    background: #f4f4f4;
    font-size: 0.72rem;
  }
  .excluded-table td,
  .excluded-table th {
    padding: 0.2rem 0.55rem;
  }
</style>
