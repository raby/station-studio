<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    getStation,
    getStationPlaylists,
    startGenerateJob,
    watchJob,
    cancelJob,
    JobStartConflict,
    deletePlaylist,
    deleteStation,
    ApiError,
  } from '$lib/api';
  import type { Station, PlaylistSummary, JobSnapshot } from '$lib/api';
  import ProgressIndicator from '$lib/components/ProgressIndicator.svelte';
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
  // §16.11 c5: live job snapshot driving the <ProgressIndicator>.
  // null when no job is in flight; otherwise the latest snapshot
  // yielded by watchJob's polling loop.
  let currentJob: JobSnapshot | null = null;

  // Delete state (DESIGN.md §16.3). One inline-error slot is reused for both
  // per-row playlist deletes and the station-nuke control — only one delete
  // can be in flight at a time and the message location is consistent.
  let deleting = false;
  let deleteError: string | null = null;

  // SvelteKit guarantees `slug` is present because this route only matches
  // when the param exists, but the param type is `string | undefined`.
  $: slug = $page.params.slug ?? '';

  // The 95% case — what the primary button generates.
  $: nextWeek =
    playlists.length === 0
      ? 1
      : Math.max(...playlists.map((p) => p.week)) + 1;

  // Reactively keep the advanced-form week pre-filled with nextWeek until the
  // user opens the disclosure and changes it. Once they touch the input we
  // stop tracking so we don't yank a value out from under the user mid-edit.
  //
  // Sticky-true: the flag never resets for the page lifetime, so the form
  // remembers the user's last typed value even after navigating around the
  // disclosure. Intentional — it would be jarring for a closed-then-reopened
  // disclosure to silently snap back to nextWeek.
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
    // The §16.4 cross-reference belongs in code comments, not the user-
    // facing dialog string — most readers won't know what §16.4 means.
    const existing = !opts.skipConfirm
      ? playlists.find((p) => p.week === week)
      : undefined;
    if (existing) {
      const ok = confirm(
        `Replace week ${week}? The current trace ${existing.trace_id.slice(0, 8)}… will be deleted. (Trace history across regenerations is on the roadmap — for now this is permanent.)`,
      );
      if (!ok) return;
    }

    generating = true;
    generateError = null;
    currentJob = null;

    // Per D3: seed only goes on the wire for rng:on stations. The
    // advanced form passes the user-chosen seed; per-row regen passes
    // a fresh random one. For rng:off, seed is omitted entirely.
    const seed = rngOn ? (opts.seed ?? seedInput) : undefined;

    // Start the job. JobStartConflict (409) means a generate is
    // already running for this slug — adopt that job rather than
    // failing the user's click. The polling loop below then surfaces
    // whatever happens to the EXISTING job.
    let jobId: string;
    try {
      const r = await startGenerateJob(slug, week, seed);
      jobId = r.job_id;
    } catch (e) {
      if (e instanceof JobStartConflict) {
        jobId = e.existing_job_id;
      } else {
        generateError = e instanceof ApiError ? e.message : String(e);
        generating = false;
        return;
      }
    }

    // Drive the polling loop. Each snapshot updates currentJob,
    // which Svelte reactivity flows into <ProgressIndicator>.
    try {
      for await (const snap of watchJob(jobId)) {
        currentJob = snap;
        if (snap.status === 'done' && snap.result) {
          // Lands on the trace viewer for the freshly generated
          // playlist. No `generating = false` — navigating away.
          await goto(`/playlists/${snap.result.playlist_id}`);
          return;
        }
        if (snap.status === 'error') {
          const err = snap.error;
          generateError = err
            ? `${err.kind}: ${err.message}`
            : 'generation failed';
          currentJob = null;
          generating = false;
          return;
        }
        if (snap.status === 'cancelled') {
          // User clicked Cancel. Quiet clear — no error to surface;
          // they meant to do it.
          currentJob = null;
          generating = false;
          return;
        }
      }
      // watchJob returned without a terminal status — job 404'd
      // mid-poll (server restarted / job GC'd). Surface a friendly
      // message + clear UI; the user can click Generate again.
      generateError = 'job vanished mid-poll (server may have restarted)';
      currentJob = null;
      generating = false;
    } catch (e) {
      generateError = e instanceof ApiError ? e.message : String(e);
      currentJob = null;
      generating = false;
    }
  }

  /**
   * <ProgressIndicator> Cancel-button handler. cancelJob already
   * absorbs 404, so the only error path here is a real network
   * failure — surfaced inline. The watchJob loop in `generate()`
   * sees the cancellation via either a status="cancelled" snapshot
   * (if the worker observed the cancel between yields) or a 404
   * (immediate-GC of cancelled jobs); either way it exits cleanly.
   */
  async function handleCancel() {
    if (!currentJob) return;
    try {
      await cancelJob(currentJob.job_id);
    } catch (e) {
      generateError = e instanceof ApiError ? e.message : String(e);
    }
  }

  async function regenRow(p: PlaylistSummary) {
    const ok = confirm(
      `Regenerate week ${p.week}? The current trace ${p.trace_id.slice(0, 8)}… will be deleted. (Trace history across regenerations is on the roadmap — for now this is permanent.)`,
    );
    if (!ok) return;
    // Per-row regen always uses a fresh random seed for rng:on (D3); the
    // explicit-seed path is the advanced form. Skip the generate()-level
    // confirm because we just asked. Awaited so any future code added
    // after this call is correctly sequenced after the navigation.
    await generate(p.week, { seed: rngOn ? randomSeed() : undefined, skipConfirm: true });
  }

  /**
   * Per-row playlist delete (DESIGN.md §16.3 D1, D2, D4, D5). Confirms with
   * the trace_id fragment so the user knows *which* trace is going. On 204:
   * stays on the page, refetches the playlist list (the row vanishes). On
   * error: inline error rendering, no navigation.
   */
  async function deleteRow(p: PlaylistSummary) {
    const ok = confirm(
      `Delete week ${p.week} (trace ${p.trace_id.slice(0, 8)}…)? This is permanent.`,
    );
    if (!ok) return;
    deleting = true;
    deleteError = null;
    try {
      await deletePlaylist(p.id);
      // Refetch — cheaper than a local splice and keeps the page in sync
      // with whatever else might have changed server-side.
      playlists = await getStationPlaylists(slug);
    } catch (e) {
      deleteError = e instanceof ApiError ? e.message : String(e);
    } finally {
      deleting = false;
    }
  }

  /**
   * Station nuke (DESIGN.md §16.3 D1, D2, D4, D5). Heavier dialog text per
   * D2 — includes computed playlist + entry counts so the user understands
   * the blast radius before clicking through. Counts come from page state;
   * no extra fetch needed. On 204: navigate to landing.
   */
  async function nukeStation() {
    if (!station) return;
    const totalEntries = playlists.reduce((a, p) => a + p.entry_count, 0);
    const ok = confirm(
      `Delete station '${station.slug}' and all ${playlists.length} playlist${playlists.length === 1 ? '' : 's'}, ${totalEntries.toLocaleString()} total entries? This is permanent.`,
    );
    if (!ok) return;
    deleting = true;
    deleteError = null;
    try {
      await deleteStation(slug);
      await goto('/');
    } catch (e) {
      deleteError = e instanceof ApiError ? e.message : String(e);
      deleting = false;
    }
    // No deleting = false on success — navigating away.
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
    <header class="station-header">
      <div>
        <h1>{station.name}</h1>
        <p class="slug">{station.slug}</p>
      </div>
      <!--
        Header actions (DESIGN.md §16.7 D1 + §16.3 D1). Visual order:
        Edit first (safe, filled), Delete last (destructive, outlined-red).
        Hierarchy matches blast radius (§16.3 D7) — safe = filled,
        destructive = outlined.
      -->
      <div class="header-actions">
        <!--
          §16.2.2 c4 — primary discoverability surface for the
          visual block editor. Read-only canvas today; §16.2.3+
          grows it into the authoring environment.
        -->
        <a
          href="/stations/{encodeURIComponent(slug)}/visualise"
          class="visualise-link"
          data-testid="visualise-station"
        >
          🗺 Visualise template
        </a>
        <!--
          §16.10 — per-station rule editor. Standalone route (mirrors
          the visualise route) until the §16.2 sidebar shell lands +
          embeds RulesPanel as one tab.
        -->
        <a
          href="/stations/{encodeURIComponent(slug)}/rules"
          class="rules-link"
          data-testid="rules-station"
        >
          ⚙ Rules
        </a>
        <a
          href="/stations/{encodeURIComponent(slug)}/edit"
          class="edit-link"
          data-testid="edit-station"
        >
          Edit station →
        </a>
        <button
          type="button"
          class="nuke"
          data-testid="delete-station"
          disabled={deleting || generating}
          on:click={nukeStation}
        >
          Delete station →
        </button>
      </div>
    </header>

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
          disabled={generating || deleting}
          on:click={() => generate(1)}
        >
          {generating ? 'Generating…' : 'Generate week 1 →'}
        </button>
      {:else}
        <button
          class="primary"
          type="button"
          data-testid="generate-next"
          disabled={generating || deleting}
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
            disabled={generating || deleting || !Number.isFinite(chosenWeek) || chosenWeek < 1}
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

      {#if currentJob !== null}
        <ProgressIndicator job={currentJob} on:cancel={handleCancel} />
      {/if}

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
                  disabled={generating || deleting}
                  on:click={() => regenRow(p)}
                  title="Regenerate this week — replaces the current trace"
                >
                  ↻ regen
                </button>
              </td>
              <td>
                <button
                  type="button"
                  class="link-btn"
                  data-testid="delete-row-{p.week}"
                  disabled={generating || deleting}
                  on:click={() => deleteRow(p)}
                  title="Delete this playlist permanently"
                >
                  ✕ delete
                </button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
      {#if deleteError}
        <p class="error" data-testid="delete-error">{deleteError}</p>
      {/if}
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
  .station-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 2rem;
  }
  .header-actions {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-shrink: 0;
  }
  /* Visualise link — peer of edit-link but visually softer (read-only
     surface today; §16.2.3+ promotes it to primary). */
  .visualise-link {
    display: inline-block;
    padding: 0.5rem 1rem;
    background: white;
    color: #2c7a7b;
    border: 1px solid #2c7a7b;
    text-decoration: none;
    border-radius: 4px;
    font-size: 0.9rem;
    white-space: nowrap;
  }
  .visualise-link:hover {
    background: #e6fffa;
  }
  /* Rules link mirrors the visualise-link's outlined-teal treatment —
     both are safe, navigational, secondary to Edit (§16.10). */
  .rules-link {
    display: inline-block;
    padding: 0.5rem 1rem;
    background: white;
    color: #2c7a7b;
    border: 1px solid #2c7a7b;
    text-decoration: none;
    border-radius: 4px;
    font-size: 0.9rem;
    white-space: nowrap;
  }
  .rules-link:hover {
    background: #e6fffa;
  }
  /* Edit link mirrors the trace viewer's `.button` styling — safe = filled,
     paired with the .nuke destructive button on its right (§16.7 D1). */
  .edit-link {
    display: inline-block;
    padding: 0.5rem 1rem;
    background: #2c3e50;
    color: white;
    text-decoration: none;
    border-radius: 4px;
    font-size: 0.9rem;
    white-space: nowrap;
  }
  .edit-link:hover {
    background: #1a252f;
  }
  .nuke {
    /* Distinct from the per-row .link-btn delete: this is a properly
       red destructive button so it's hard to misclick from anything
       else (DESIGN.md §16.3 D7). At least 1rem of gap from the
       title via the .station-header flex `gap`. */
    padding: 0.5rem 1rem;
    background: white;
    color: #c0392b;
    border: 1px solid #c0392b;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.9rem;
    white-space: nowrap;
  }
  .nuke:hover:not(:disabled) {
    background: #c0392b;
    color: white;
  }
  .nuke:disabled {
    opacity: 0.5;
    cursor: not-allowed;
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
