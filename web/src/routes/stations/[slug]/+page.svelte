<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    getStation,
    getStationPlaylists,
    generatePlaylist,
    ApiError,
  } from '$lib/api';
  import type { Station, PlaylistSummary } from '$lib/api';
  import { page } from '$app/stores';

  let station: Station | null = null;
  let playlists: PlaylistSummary[] = [];
  let error: string | null = null;
  let loading = true;

  // Generate-panel state. See DESIGN.md §16.1 for the decision matrix.
  let advancedOpen = false;
  let chosenWeek = 1; // populated post-load
  let seedInput = randomSeed();
  let generating = false;
  let generateError: string | null = null;

  // SvelteKit guarantees `slug` is present because this route only matches
  // when the param exists, but the param type is `string | undefined`.
  $: slug = $page.params.slug ?? '';

  // The 95% case — what the primary button generates.
  $: nextWeek =
    playlists.length === 0
      ? 1
      : Math.max(...playlists.map((p) => p.week)) + 1;

  // Reactively keep the advanced-form week pre-filled with nextWeek until the
  // user opens the disclosure and changes it. Once advancedOpen flips true we
  // stop tracking so we don't yank a value out from under the user mid-edit.
  let userTouchedAdvanced = false;
  $: if (!userTouchedAdvanced) chosenWeek = nextWeek;

  // Does the chosen week already exist? Drives the "Replace week N" affordance.
  $: chosenWeekExists = playlists.some((p) => p.week === chosenWeek);

  // Show the seed input only for rng:on stations (DESIGN.md §16.1 D3).
  $: rngOn = (station?.template?.rng as string | undefined) === 'on';

  function randomSeed(): number {
    // 32-bit positive int — matches what `random.Random(seed)` accepts on the
    // backend without overflow worries.
    return Math.floor(Math.random() * 2 ** 31);
  }

  function rerollSeed() {
    seedInput = randomSeed();
  }

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

  async function generate(week: number, opts: { seed?: number; skipConfirm?: boolean } = {}) {
    // Confirm-on-overwrite (DESIGN.md §16.1 D2). The per-row regen path
    // bypasses this because the row's own click handler has already
    // confirmed (with row-specific copy carrying the existing trace_id).
    if (!opts.skipConfirm && playlists.some((p) => p.week === week)) {
      const existing = playlists.find((p) => p.week === week)!;
      const ok = confirm(
        `Replace week ${week}? The current trace ${existing.trace_id.slice(0, 8)}… will be deleted (see DESIGN.md §16.4 for the trace-history feature).`,
      );
      if (!ok) return;
    }

    generating = true;
    generateError = null;
    try {
      // Per D3: seed only goes on the wire for rng:on stations. The advanced
      // form passes the user-chosen seed; per-row regen passes a fresh
      // random one (so the user lands on a *different* output, not a
      // byte-identical replay). For rng:off, the seed is omitted entirely.
      const seed = rngOn ? (opts.seed ?? seedInput) : undefined;
      const r = await generatePlaylist(slug, week, seed);
      // Lands on the trace viewer for the freshly generated playlist.
      await goto(`/playlists/${r.playlist_id}`);
    } catch (e) {
      generateError = e instanceof ApiError ? e.message : String(e);
      generating = false;
    }
    // Note: no `generating = false` on success — we're navigating away.
  }

  function regenRow(p: PlaylistSummary) {
    const ok = confirm(
      `Regenerate week ${p.week}? The current trace ${p.trace_id.slice(0, 8)}… will be deleted (see DESIGN.md §16.4 for the trace-history feature).`,
    );
    if (!ok) return;
    // Per-row regen always uses a fresh random seed for rng:on (D3); the
    // explicit-seed path is the advanced form. Skip the generate()-level
    // confirm because we just asked.
    generate(p.week, { seed: rngOn ? randomSeed() : undefined, skipConfirm: true });
  }

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

    <!--
      Generate panel. Two visual variants:
        - Empty state: big primary "Generate week 1 →" CTA.
        - Populated:   inline button + advanced disclosure.
      See DESIGN.md §16.1 D1/D4.
    -->
    <section class="generate" data-testid="generate-panel">
      {#if playlists.length === 0}
        <button
          class="cta"
          type="button"
          data-testid="generate-empty-cta"
          disabled={generating}
          on:click={() => generate(1)}
        >
          {generating ? 'Generating…' : 'Generate week 1 →'}
        </button>
      {:else}
        <button
          class="primary"
          type="button"
          data-testid="generate-next"
          disabled={generating}
          on:click={() => generate(nextWeek)}
        >
          {generating ? 'Generating…' : `Generate next week (${nextWeek})`}
        </button>
      {/if}

      <details bind:open={advancedOpen} class="advanced">
        <summary>advanced — pick a week{rngOn ? ' / seed' : ''}</summary>
        <div class="advanced-body">
          <label>
            Week
            <input
              type="number"
              min="1"
              data-testid="advanced-week"
              bind:value={chosenWeek}
              on:input={() => (userTouchedAdvanced = true)}
            />
          </label>
          {#if rngOn}
            <label>
              Seed
              <input
                type="number"
                data-testid="advanced-seed"
                bind:value={seedInput}
              />
            </label>
            <button type="button" class="ghost" on:click={rerollSeed}>
              🎲 reroll
            </button>
          {/if}
          <button
            type="button"
            class={chosenWeekExists ? 'replace' : 'primary'}
            data-testid="advanced-submit"
            disabled={generating || chosenWeek < 1}
            on:click={() => generate(chosenWeek)}
          >
            {generating
              ? 'Generating…'
              : chosenWeekExists
                ? `Replace week ${chosenWeek}`
                : `Generate week ${chosenWeek}`}
          </button>
        </div>
      </details>

      {#if generateError}
        <p class="error" data-testid="generate-error">{generateError}</p>
      {/if}
    </section>

    {#if playlists.length === 0}
      <p class="hint">
        None generated yet — click the button above, or run
        <code>python -m scripts.bootstrap_classics</code> from the repo root.
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
              <td>
                <button
                  type="button"
                  class="link-btn"
                  data-testid="regen-row-{p.week}"
                  disabled={generating}
                  on:click={() => regenRow(p)}
                  title="Regenerate this week — replaces the current trace"
                >
                  ↻ regen
                </button>
              </td>
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
  .generate {
    margin: 1rem 0 1.5rem;
    padding: 0.75rem 1rem;
    background: #fafafa;
    border: 1px solid #eee;
    border-radius: 6px;
  }
  .cta {
    font-size: 1.1rem;
    padding: 0.75rem 1.5rem;
    background: #2c7a7b;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .cta:hover:not(:disabled) {
    background: #285e61;
  }
  .primary {
    padding: 0.5rem 1rem;
    background: #2c7a7b;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .primary:hover:not(:disabled) {
    background: #285e61;
  }
  .replace {
    padding: 0.5rem 1rem;
    background: #c0392b;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .replace:hover:not(:disabled) {
    background: #962d22;
  }
  .ghost {
    padding: 0.5rem 0.75rem;
    background: white;
    border: 1px solid #ccc;
    border-radius: 4px;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .advanced {
    margin-top: 0.75rem;
  }
  .advanced summary {
    font-size: 0.85rem;
    color: #555;
    cursor: pointer;
  }
  .advanced-body {
    display: flex;
    align-items: end;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-top: 0.5rem;
  }
  .advanced-body label {
    display: flex;
    flex-direction: column;
    font-size: 0.85rem;
    color: #555;
  }
  .advanced-body input[type='number'] {
    padding: 0.4rem;
    border: 1px solid #ccc;
    border-radius: 4px;
    width: 7rem;
    font-variant-numeric: tabular-nums;
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
  .link-btn {
    background: none;
    border: none;
    color: #c0392b;
    cursor: pointer;
    font-size: 0.85rem;
    padding: 0;
  }
  .link-btn:hover:not(:disabled) {
    text-decoration: underline;
  }
  .link-btn:disabled {
    color: #999;
  }
  .error {
    color: crimson;
    margin-top: 0.5rem;
  }
  .hint {
    color: #666;
    font-size: 0.9rem;
  }
  code {
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
  }
</style>
