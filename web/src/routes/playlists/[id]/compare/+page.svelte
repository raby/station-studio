<script lang="ts">
  /**
   * §16.4.t1 — side-by-side trace-version compare view.
   *
   * Lives at `/playlists/[id]/compare?from=N&to=M`. The URL is the
   * source of truth: switching versions updates the query params
   * via goto + the reactive watcher re-fires the fetch. Default
   * targets `from = current - 1, to = current` so the most-asked
   * question ("what changed in the latest regen?") needs zero
   * picker interaction.
   *
   * The wire payload is a `TraceDiffResponse` from
   * `GET /api/playlists/{id}/diff?from=N&to=M`: every slot with both
   * sides + a server-computed `changed` flag (predicate fires on
   * any difference in source / strategy / pool_index / track_id).
   * The "Show all" toggle is render-only — no second round-trip.
   *
   * Errors surface inline; the page never blanks (matches the
   * trace viewer's defensive render pattern).
   */
  import {
    getTraceDiff,
    getVersions,
    ApiError,
  } from '$lib/api';
  import type {
    TraceDiffResponse, TraceDiffRow, VersionsResponse,
  } from '$lib/api';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';

  let diff: TraceDiffResponse | null = null;
  let versions: VersionsResponse | null = null;
  let error: string | null = null;
  let loading = true;
  let showAll = false;

  $: id = Number($page.params.id);

  // Parse `?from` and `?to` defensively — null/NaN/non-positive
  // values fall back to `null` so the default-derivation below
  // can compute fresh from the versions list.
  function _parseV(raw: string | null): number | null {
    if (raw == null) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
  }
  $: requestedFrom = _parseV($page.url.searchParams.get('from'));
  $: requestedTo = _parseV($page.url.searchParams.get('to'));

  // Generation gate — same pattern as the trace viewer; rapid
  // navigations between version pairs can race the fetches.
  let loadGeneration = 0;

  async function loadAll(playlistId: number, from: number | null, to: number | null) {
    if (isNaN(playlistId)) return;
    const myGen = ++loadGeneration;
    loading = true;
    error = null;

    try {
      // Fetch versions first — needed to compute defaults if `from`
      // or `to` weren't supplied in the URL. Cheap (no slots JSON
      // on the wire).
      const vlist = await getVersions(playlistId);
      if (myGen !== loadGeneration) return;
      versions = vlist;

      if (vlist.versions.length < 2) {
        error = 'Compare requires at least two versions of this playlist.';
        loading = false;
        return;
      }

      // §16.4.t1 default-derivation: `from = current - 1, to = current`.
      // Honours explicit URL params when present; otherwise computes.
      const all = vlist.versions.map((v) => v.version_no);
      const max = vlist.current_v;
      const secondMax = all
        .filter((n) => n !== max)
        .reduce((acc, n) => (n > acc ? n : acc), 0);

      const effectiveFrom = from ?? secondMax;
      const effectiveTo = to ?? max;

      if (effectiveFrom === effectiveTo) {
        error = `from and to are the same version (${effectiveFrom}). ` +
          `Pick two different versions to compare.`;
        loading = false;
        return;
      }

      // If the URL was missing params, rewrite it so reloads /
      // permalinks carry the derived selection. replaceState=true
      // because we don't want the operator's "back" button to send
      // them to the param-less URL.
      if (from == null || to == null) {
        const url = new URL($page.url);
        url.searchParams.set('from', String(effectiveFrom));
        url.searchParams.set('to', String(effectiveTo));
        void goto(url.toString().replace(url.origin, ''), {
          keepFocus: true, noScroll: true, replaceState: true,
        });
        // Don't fetch the diff with potentially-stale state; the
        // goto fires another reactive cycle which arrives here with
        // requestedFrom/requestedTo populated.
        return;
      }

      const d = await getTraceDiff(playlistId, effectiveFrom, effectiveTo);
      if (myGen !== loadGeneration) return;
      diff = d;
    } catch (e) {
      if (myGen !== loadGeneration) return;
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      if (myGen === loadGeneration) loading = false;
    }
  }

  $: if (!isNaN(id)) void loadAll(id, requestedFrom, requestedTo);

  // Picker change handlers — update the URL; the reactive watcher
  // above re-fires the fetch. URL is the source of truth.
  function _onPickerChange(side: 'from' | 'to', e: Event) {
    const target = e.currentTarget as HTMLSelectElement;
    const next = Number(target.value);
    if (!Number.isInteger(next) || next < 1) return;
    const url = new URL($page.url);
    url.searchParams.set(side, String(next));
    void goto(url.toString().replace(url.origin, ''), {
      keepFocus: true, noScroll: true,
    });
  }

  $: visibleRows = (() => {
    if (!diff) return [] as TraceDiffRow[];
    return showAll ? diff.slots : diff.slots.filter((r) => r.changed);
  })();

  function fmtDate(iso: string | null | undefined): string {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString();
  }
</script>

<svelte:head>
  <title>
    Compare versions{diff ? ` v${diff.from.version_no} ↔ v${diff.to.version_no}` : ''}
    — Station Studio
  </title>
</svelte:head>

<main id="main-content" tabindex="-1">
  <p class="crumbs">
    <a href="/">stations</a> ›
    <a href="/playlists/{id}">playlist {id}</a> ›
    compare versions
  </p>

  {#if loading}
    <p>Loading…</p>
  {:else if error}
    <p class="error" data-testid="compare-error">{error}</p>
  {:else if diff && versions}
    <h1>
      Compare versions
      <span class="header-meta">
        v{diff.from.version_no} ↔ v{diff.to.version_no}
      </span>
    </h1>

    <!--
      Picker strip: two selects + the summary count. Each picker
      lets the operator pivot the comparison without leaving the
      page; URL stays in sync.
    -->
    <div class="picker-strip" data-testid="picker-strip">
      <label>
        From
        <select
          data-testid="picker-from"
          value={diff.from.version_no}
          on:change={(e) => _onPickerChange('from', e)}
        >
          {#each versions.versions as v (v.version_no)}
            <option value={v.version_no}>
              v{v.version_no}{v.version_no === versions.current_v ? ' (current)' : ''}
              · {fmtDate(v.generated_at)}
            </option>
          {/each}
        </select>
      </label>
      <label>
        To
        <select
          data-testid="picker-to"
          value={diff.to.version_no}
          on:change={(e) => _onPickerChange('to', e)}
        >
          {#each versions.versions as v (v.version_no)}
            <option value={v.version_no}>
              v{v.version_no}{v.version_no === versions.current_v ? ' (current)' : ''}
              · {fmtDate(v.generated_at)}
            </option>
          {/each}
        </select>
      </label>
      <span class="summary" data-testid="summary">
        <strong>{diff.summary.changed.toLocaleString()}</strong> of
        {diff.summary.total_slots.toLocaleString()} slots changed
      </span>
      <label class="show-all-toggle" data-testid="show-all-toggle">
        <input type="checkbox" bind:checked={showAll} />
        Show unchanged ({diff.summary.unchanged.toLocaleString()})
      </label>
    </div>

    {#if visibleRows.length === 0}
      <p class="empty" data-testid="empty-state">
        {diff.summary.changed === 0
          ? 'No changes between these two versions.'
          : 'No rows to show — toggle "Show unchanged" to see the full trace.'}
      </p>
    {:else}
      <!--
        Side-by-side table. Two stacked columns per slot ("from" /
        "to"); changed rows highlight the diffing fields. Unchanged
        rows render dimmed when the toggle is on.
      -->
      <table data-testid="diff-table">
        <thead>
          <tr>
            <th rowspan="2">#</th>
            <th rowspan="2">Unit · Row</th>
            <th colspan="4" class="side from">v{diff.from.version_no} (from)</th>
            <th colspan="4" class="side to">v{diff.to.version_no} (to)</th>
          </tr>
          <tr>
            <th class="side from">Pool</th>
            <th class="side from">Track</th>
            <th class="side from">Artist</th>
            <th class="side from">Source</th>
            <th class="side to">Pool</th>
            <th class="side to">Track</th>
            <th class="side to">Artist</th>
            <th class="side to">Source</th>
          </tr>
        </thead>
        <tbody>
          {#each visibleRows as row (row.slot)}
            <tr
              data-testid="diff-row"
              data-slot={row.slot}
              data-changed={row.changed}
              class:row-changed={row.changed}
              class:row-unchanged={!row.changed}
            >
              <td class="num">{row.slot}</td>
              <td class="mono">{row.unit} · {row.row}/{row.block}</td>
              {#if row.from}
                <td class="num mono">{row.from.pool_index ?? '—'}</td>
                <td>{row.from.track_name ?? '—'}</td>
                <td class="muted">{row.from.track_artist ?? ''}</td>
                <td class="mono">{row.from.source ?? '—'}</td>
              {:else}
                <td colspan="4" class="missing">— (no slot in v{diff.from.version_no})</td>
              {/if}
              {#if row.to}
                <td class="num mono">{row.to.pool_index ?? '—'}</td>
                <td>{row.to.track_name ?? '—'}</td>
                <td class="muted">{row.to.track_artist ?? ''}</td>
                <td class="mono">{row.to.source ?? '—'}</td>
              {:else}
                <td colspan="4" class="missing">— (no slot in v{diff.to.version_no})</td>
              {/if}
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
    max-width: 1400px;
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
    margin: 0 0 0.5rem;
  }
  .header-meta {
    color: #888;
    font-variant-numeric: tabular-nums;
    font-weight: 400;
    font-size: 0.85em;
  }
  .picker-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 1rem;
    margin: 0.75rem 0 1.5rem;
    padding: 0.5rem 0.75rem;
    background: #fafafa;
    border: 1px solid #e4e4e4;
    border-radius: 4px;
    font-size: 0.9rem;
  }
  .picker-strip label {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    color: #555;
  }
  .picker-strip select {
    padding: 0.25rem 0.5rem;
    border: 1px solid #ccc;
    border-radius: 3px;
    background: white;
    font: inherit;
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
  }
  .summary {
    color: #444;
    font-variant-numeric: tabular-nums;
  }
  .show-all-toggle {
    margin-left: auto;
    color: #555;
    cursor: pointer;
  }
  .show-all-toggle input {
    margin: 0 0.25rem 0 0;
  }
  .empty {
    color: #666;
    font-style: italic;
    padding: 2rem;
    text-align: center;
  }
  .error {
    color: crimson;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.85rem;
  }
  th,
  td {
    padding: 0.25rem 0.6rem;
    text-align: left;
    border-bottom: 1px solid #f1f1f1;
  }
  th {
    font-weight: 600;
    font-size: 0.75rem;
    color: #555;
    background: #fafafa;
    border-bottom: 2px solid #e4e4e4;
  }
  /* Subtle side-color wash so the from / to header groups separate
     at a glance. Body cells stay neutral — the row-changed
     highlight wins visually + a per-cell tint added unwanted
     contrast clutter. */
  th.side.from {
    background: rgba(255, 245, 230, 0.6);
  }
  th.side.to {
    background: rgba(230, 245, 255, 0.6);
  }
  /* Changed rows: yellow-ish wash so they pop against the
     side-tints. */
  tr.row-changed > td {
    background: rgba(255, 250, 200, 0.5);
  }
  /* Unchanged rows dim when "Show all" is on. */
  tr.row-unchanged {
    color: #888;
  }
  tr.row-unchanged > td {
    background: transparent;
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .mono {
    font-family: ui-monospace, monospace;
    font-size: 0.8rem;
  }
  .muted {
    color: #888;
  }
  .missing {
    color: #999;
    font-style: italic;
    text-align: center;
  }
</style>
