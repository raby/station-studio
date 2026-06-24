<script lang="ts">
  /**
   * §16.23 — week-vs-week trace diff page.
   *
   * Lives at `/stations/[slug]/diff?from=<week>&to=<week>`. URL is
   * the source of truth: switching weeks updates the query params
   * via goto + the reactive watcher re-fires the fetch. Default
   * targets `from = N-1, to = N` where N = the station's latest
   * generated week, so the most-asked question ("what changed in
   * the latest week vs the prior?") needs zero picker interaction.
   *
   * Reads the CURRENT version of each week's playlist (design-pass
   * decision; opt-in per-side version picking is a future extension).
   *
   * Sibling to `/playlists/[id]/compare` (§16.4.t1's
   * version-vs-version diff) — same per-slot wire shape, same diff-
   * table render, different labels + permalink targets. The trio
   * is now: version-vs-version (§16.4.t1) + week-vs-week (this) +
   * staged-vs-saved (§16.2.7).
   */
  import {
    getStation,
    getStationPlaylists,
    getStationWeekDiff,
    ApiError,
  } from '$lib/api';
  import type {
    Station, PlaylistSummary, WeekDiffResponse, TraceDiffRow,
  } from '$lib/api';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';

  let diff: WeekDiffResponse | null = null;
  let station: Station | null = null;
  let playlists: PlaylistSummary[] = [];
  let error: string | null = null;
  let loading = true;
  let showAll = false;

  $: slug = $page.params.slug ?? '';

  function _parseW(raw: string | null): number | null {
    if (raw == null) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
  }
  $: requestedFrom = _parseW($page.url.searchParams.get('from'));
  $: requestedTo = _parseW($page.url.searchParams.get('to'));

  // Generation gate — mirrors the trace viewer pattern; rapid
  // navigations between week pairs can race the fetches.
  let loadGeneration = 0;

  async function loadAll(stationSlug: string, from: number | null, to: number | null) {
    if (!stationSlug) return;
    const myGen = ++loadGeneration;
    loading = true;
    error = null;

    try {
      // Station + playlists list resolve in parallel — both are
      // needed to compute defaults + populate the week pickers.
      const [s, list] = await Promise.all([
        getStation(stationSlug),
        getStationPlaylists(stationSlug).catch(
          () => [] as PlaylistSummary[],
        ),
      ]);
      if (myGen !== loadGeneration) return;
      station = s;
      playlists = list;

      if (list.length < 2) {
        error =
          'Compare requires at least two generated weeks for this station.';
        loading = false;
        return;
      }

      // §16.23 default-derivation: from = N-1, to = N where N is
      // the latest generated week. Honours explicit URL params when
      // present; otherwise computes.
      const weeks = list.map((p) => p.week).sort((a, b) => a - b);
      const maxWeek = weeks[weeks.length - 1];
      const secondMaxWeek = weeks[weeks.length - 2];
      const effectiveFrom = from ?? secondMaxWeek;
      const effectiveTo = to ?? maxWeek;

      if (effectiveFrom === effectiveTo) {
        error = `from and to are the same week (${effectiveFrom}). ` +
          `Pick two different weeks to compare.`;
        loading = false;
        return;
      }

      if (from == null || to == null) {
        const url = new URL($page.url);
        url.searchParams.set('from', String(effectiveFrom));
        url.searchParams.set('to', String(effectiveTo));
        void goto(url.toString().replace(url.origin, ''), {
          keepFocus: true, noScroll: true, replaceState: true,
        });
        return;
      }

      const d = await getStationWeekDiff(stationSlug, effectiveFrom, effectiveTo);
      if (myGen !== loadGeneration) return;
      diff = d;
    } catch (e) {
      if (myGen !== loadGeneration) return;
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      if (myGen === loadGeneration) loading = false;
    }
  }

  $: if (slug) void loadAll(slug, requestedFrom, requestedTo);

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

  // Picker options — sort by week so the dropdown reads top-to-bottom
  // chronologically. Each option labels its corresponding generation
  // timestamp so two playlists for the same week (impossible with the
  // current uniqueness constraint, but defensive) would still be
  // distinguishable.
  $: pickerOptions = [...playlists].sort((a, b) => a.week - b.week);
</script>

<svelte:head>
  <title>
    Week diff{diff ? ` (${diff.from.week} ↔ ${diff.to.week})` : ''}
    — Station Studio
  </title>
</svelte:head>

<main>
  <p class="crumbs">
    <a href="/">stations</a> ›
    <a href="/stations/{slug}">{station?.name ?? slug}</a> ›
    week diff
  </p>

  {#if loading}
    <p>Loading…</p>
  {:else if error}
    <p class="error" data-testid="week-diff-error">{error}</p>
  {:else if diff && station}
    <h1>
      Week diff
      <span class="header-meta">
        week {String(diff.from.week).padStart(3, '0')} ↔
        week {String(diff.to.week).padStart(3, '0')}
      </span>
    </h1>

    <!--
      Picker strip: two week selects + a summary count + an unchanged
      toggle. Each picker rewrites the URL so the comparison is
      bookmarkable + back-button-friendly.
    -->
    <div class="picker-strip" data-testid="picker-strip">
      <label>
        From
        <select
          data-testid="picker-from"
          value={diff.from.week}
          on:change={(e) => _onPickerChange('from', e)}
        >
          {#each pickerOptions as p (p.id)}
            <option value={p.week}>
              week {String(p.week).padStart(3, '0')}
              · {fmtDate(p.generated_at)}
            </option>
          {/each}
        </select>
      </label>
      <label>
        To
        <select
          data-testid="picker-to"
          value={diff.to.week}
          on:change={(e) => _onPickerChange('to', e)}
        >
          {#each pickerOptions as p (p.id)}
            <option value={p.week}>
              week {String(p.week).padStart(3, '0')}
              · {fmtDate(p.generated_at)}
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
          ? 'No changes between these two weeks.'
          : 'No rows to show — toggle "Show unchanged" to see the full trace.'}
      </p>
    {:else}
      <!--
        Side-by-side table. Mirrors §16.4.t1's diff page render so
        operators get a consistent visual across the diff-surfaces
        trio. Changed rows highlight; unchanged dim when "show all"
        is on.
      -->
      <table data-testid="diff-table">
        <thead>
          <tr>
            <th rowspan="2">#</th>
            <th rowspan="2">Unit · Row</th>
            <!--
              §16.23 R1 — surface version_no so a rolled-back or
              regenerated week is visible. Otherwise the operator
              can't tell why a "same week" diff shows differences:
              they could be looking at v3 of week 3 against v1 of
              week 2 without realizing a regen happened.
            -->
            <th colspan="4" class="side from">
              week {String(diff.from.week).padStart(3, '0')} (from, v{diff.from.version_no})
            </th>
            <th colspan="4" class="side to">
              week {String(diff.to.week).padStart(3, '0')} (to, v{diff.to.version_no})
            </th>
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
                <td colspan="4" class="missing">
                  — (no slot in week {String(diff.from.week).padStart(3, '0')})
                </td>
              {/if}
              {#if row.to}
                <td class="num mono">{row.to.pool_index ?? '—'}</td>
                <td>{row.to.track_name ?? '—'}</td>
                <td class="muted">{row.to.track_artist ?? ''}</td>
                <td class="mono">{row.to.source ?? '—'}</td>
              {:else}
                <td colspan="4" class="missing">
                  — (no slot in week {String(diff.to.week).padStart(3, '0')})
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</main>

<style>
  /* Mirrors `/playlists/[id]/compare/+page.svelte`'s styles —
     deliberately copied (not abstracted) so the two diff pages can
     evolve independently if the surfaces diverge. Three diff pages
     and they should share a component; two is just below that line. */
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
  th.side.from {
    background: rgba(255, 245, 230, 0.6);
  }
  th.side.to {
    background: rgba(230, 245, 255, 0.6);
  }
  tr.row-changed > td {
    background: rgba(255, 250, 200, 0.5);
  }
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
