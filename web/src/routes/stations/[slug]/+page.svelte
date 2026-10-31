<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    getStation,
    getStationPlaylists,
    getStationOverview,
    isGenerateResult,
    startGenerateJob,
    watchJob,
    cancelJob,
    JobStartConflict,
    deletePlaylist,
    deleteStation,
    forkStation,
    ApiError,
  } from '$lib/api';
  import type {
    Station, PlaylistSummary, JobSnapshot, StationOverview,
  } from '$lib/api';
  import ProgressIndicator from '$lib/components/ProgressIndicator.svelte';
  import PoolImportPanel from '$lib/components/sources/PoolImportPanel.svelte';
  import StationServerPanel from '$lib/components/StationServerPanel.svelte';
  import ErrorBanner from '$lib/components/ErrorBanner.svelte';
  import { confirmDialog } from '$lib/confirm';
  import { page } from '$app/stores';

  let station: Station | null = null;
  let playlists: PlaylistSummary[] = [];
  let error: string | null = null;
  let loading = true;
  // §16.19 — rich overview bundle (template summary / pools / cursors /
  // generation rollup + staleness). NON-fatal: a failure here leaves
  // the core station page (header + generate + playlists) intact.
  let overview: StationOverview | null = null;

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

  // §16.2.8 — fork state. forkOpen toggles the inline form. The
  // form's defaults are derived from the source station; operator
  // can edit before submitting.
  let forkOpen = false;
  let forkNewSlug = '';
  let forkNewName = '';
  let forking = false;
  let forkError: string | null = null;

  function openForkForm(): void {
    if (!station) return;
    forkNewSlug = `${station.slug}-fork`;
    forkNewName = `${station.name} (fork)`;
    forkError = null;
    forkOpen = true;
  }

  function closeForkForm(): void {
    forkOpen = false;
    forkError = null;
  }

  async function submitFork(): Promise<void> {
    if (!station) return;
    forking = true;
    forkError = null;
    try {
      const next = await forkStation(station.slug, {
        new_slug: forkNewSlug.trim(),
        new_name: forkNewName.trim(),
      });
      // Land on the new station's detail page. SvelteKit goto's
      // client-side nav re-runs the page load (re-fetches getStation
      // for the new slug); previous mounts unmount cleanly.
      await goto(`/stations/${encodeURIComponent(next.slug)}`);
    } catch (e) {
      if (e instanceof ApiError) {
        forkError = e.message;
      } else {
        forkError = e instanceof Error ? e.message : String(e);
      }
    } finally {
      forking = false;
    }
  }

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
    // §16.19 — fetch the overview separately + non-fatally. The core
    // page already rendered above; the rich sections are additive, so
    // a failure (or a backend that predates the endpoint) just omits
    // them rather than blanking the page.
    await refreshOverview();
  });

  // §16.25 c5 review 🟡-1: extracted so the embedded <PoolImportPanel>
  // can `on:changed={refreshOverview}` — after any panel-driven import
  // / re-import / delete, the §16.19 pools card needs to re-fetch so
  // the "not imported" badge clears in real time. (Without this, the
  // embed feels broken: the user fixes a gap but the gap-indicator
  // stays until they reload.)
  async function refreshOverview() {
    try {
      overview = await getStationOverview(slug);
    } catch (e) {
      // Non-fatal by design (the cards are additive) — but keep a trace
      // so a real 500 doesn't vanish into "cards just didn't render".
      overview = null;
      console.warn('station overview fetch failed', e);
    }
  }

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
      // §16.4 — regeneration is no longer destructive. The prior
      // trace is preserved as a versioned row; the new generation
      // becomes the current version. Copy reflects the actual
      // semantics so the operator doesn't think they're about to
      // lose history.
      const ok = await confirmDialog({
        title: `Regenerate week ${week}?`,
        body:
          `The current trace ${existing.trace_id.slice(0, 8)}… will be saved as a prior version. ` +
          `The new generation becomes the current version. You can switch between versions ` +
          `from the trace viewer.`,
        confirmLabel: 'Regenerate',
        danger: true,
      });
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
        if (snap.status === 'done' && isGenerateResult(snap.result)) {
          // c3 review #1 batch C: migrated from the inline
          // `'playlist_id' in snap.result` narrow to the hoisted
          // `isGenerateResult` type guard (c3 single source of
          // truth for the JobSnapshot.result discriminator). A
          // preview job snapshot shouldn't land here (this route
          // only starts generate jobs) but the narrow is a cheap
          // belt-and-suspenders. Lands on the trace viewer for
          // the freshly generated playlist. No `generating =
          // false` — navigating away.
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
    // §16.4 — per-row regen mirrors the generate flow above: the
    // prior trace is preserved as a prior version, not deleted.
    const ok = await confirmDialog({
      title: `Regenerate week ${p.week}?`,
      body:
        `The current trace ${p.trace_id.slice(0, 8)}… will be saved as a prior version. ` +
        `The new generation becomes the current version. You can switch between versions ` +
        `from the trace viewer.`,
      confirmLabel: 'Regenerate',
      danger: true,
    });
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
    const ok = await confirmDialog({
      title: `Delete week ${p.week}?`,
      body: `Trace ${p.trace_id.slice(0, 8)}… will be removed. This is permanent.`,
      confirmLabel: 'Delete',
      danger: true,
    });
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
    const ok = await confirmDialog({
      title: `Delete station '${station.slug}'?`,
      body:
        `${playlists.length} playlist${playlists.length === 1 ? '' : 's'}, ` +
        `${totalEntries.toLocaleString()} total entries. ` +
        `This is permanent.`,
      confirmLabel: 'Delete',
      danger: true,
    });
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

<main id="main-content" tabindex="-1">
  <p class="crumbs"><a href="/">← stations</a></p>

  {#if loading}
    <p>Loading…</p>
  {:else if error}
    <!-- §16.5 c4 — load failure via ErrorBanner. Assertive
         severity: the page can't render anything else without
         station data, so the operator hit a hard wall. Per the
         c2/c3/c4 codebase convention (see DESIGN.md §16.5
         severity policy): assertive = "can't proceed", polite =
         "recoverable, try again". -->
    <ErrorBanner
      message={error}
      severity="assertive"
      prefix="Could not load station"
    />
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
          Visualise template
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
          Rules
        </a>
        <a
          href="/stations/{encodeURIComponent(slug)}/edit"
          class="edit-link btn-primary"
          data-testid="edit-station"
        >
          Edit station
        </a>
        <!-- §16.2.8 — Fork affordance. Click toggles an inline form
             instead of routing to a separate page (keeps operator in
             context). Defaults to slug+'-fork' / name+' (fork)'. -->
        <button
          type="button"
          class="fork"
          data-testid="fork-station"
          disabled={forking || generating}
          on:click={openForkForm}
        >
          Fork station
        </button>
        <button
          type="button"
          class="nuke"
          data-testid="delete-station"
          disabled={deleting || generating}
          on:click={nukeStation}
        >
          Delete station
        </button>
      </div>
      {#if forkOpen}
        <!-- §16.2.8 — inline fork form. Slug + name inputs default
             to source-derived values; operator submits or cancels.
             409 / 422 errors render in the same banner slot. -->
        <form
          class="fork-form"
          data-testid="fork-form"
          on:submit|preventDefault={submitFork}
        >
          <label>
            New slug
            <input
              type="text"
              data-testid="fork-new-slug"
              bind:value={forkNewSlug}
              required
              pattern="^[a-z0-9][a-z0-9-]*$"
              disabled={forking}
            />
          </label>
          <label>
            New name
            <input
              type="text"
              data-testid="fork-new-name"
              bind:value={forkNewName}
              required
              disabled={forking}
            />
          </label>
          <!-- §16.5 c4 — fork-error already had role=status (the
               pre-§16.5 form). Migrating to ErrorBanner for
               consistency with the other inline errors in this
               page; polite severity matches the prior contract.

               §16.5 c4-review R1+R2 — `compact` mode drops the
               bordered banner so the inline form layout
               doesn't grow ~50px on every retry; `prefix=""`
               suppresses the "Fork:" label because the
               operator is already inside the fork form and
               doesn't need the framing context read aloud. -->
          {#if forkError}
            <ErrorBanner
              message={forkError}
              severity="polite"
              prefix=""
              compact
              testId="fork-error"
            />
          {/if}
          <div class="fork-actions">
            <button
              type="submit"
              data-testid="fork-submit"
              disabled={forking || !forkNewSlug.trim() || !forkNewName.trim()}
            >
              {forking ? 'Forking…' : 'Fork station'}
            </button>
            <button
              type="button"
              class="ghost"
              data-testid="fork-cancel"
              disabled={forking}
              on:click={closeForkForm}
            >
              Cancel
            </button>
          </div>
        </form>
      {/if}
    </header>

    {#if overview}
      <!-- §16.19 — rich station detail. Additive sections; absent if the
           overview fetch failed (the page core still works). -->
      <section class="overview" data-testid="overview">
        <div class="overview-grid">
          <div class="card" data-testid="template-summary">
            <h3>Template</h3>
            <dl>
              <dt>Slots / week</dt>
              <dd>{overview.template_summary.slot_count.toLocaleString()}</dd>
              <dt>Layout</dt>
              <dd>{overview.template_summary.layout_form}</dd>
              <dt>Cursor</dt>
              <dd>{overview.template_summary.cursor_persist ? 'persistent' : 'stateless'}</dd>
              <dt>Strategies</dt>
              <dd>
                {#each Object.entries(overview.template_summary.strategy_counts) as [strat, n]}
                  <span class="pill">{strat} ×{n}</span>
                {/each}
                <span class="hint-inline">(by block)</span>
              </dd>
            </dl>
            <a
              href="/stations/{encodeURIComponent(slug)}/visualise"
              class="card-link"
              data-testid="overview-visualise-link"
            >🗺 Visualise →</a>
          </div>

          <div class="card" data-testid="pools-summary">
            <h3>Source pools</h3>
            <ul class="pool-list">
              {#each overview.pools as p}
                <li data-testid="pool-row" data-pool={p.name} class:missing={!p.imported}>
                  <span class="pool-name">{p.name}</span>
                  <span class="pool-role">{p.role}</span>
                  {#if p.imported}
                    <span class="pool-count">{(p.track_count ?? 0).toLocaleString()} tracks</span>
                  {:else}
                    <!-- §16.25 c5 (D3 secondary): deep-link the badge
                         to /sources with the pool name + role prefilled
                         on the add-form. The PRIMARY fix path is the
                         embedded <PoolImportPanel> below (same page,
                         no navigation); this link is the global-
                         context escape hatch. -->
                    <a
                      class="pool-missing"
                      data-testid="pool-missing"
                      href="/sources?pool={encodeURIComponent(p.name)}&role={encodeURIComponent(p.role)}"
                    >
                      not imported
                    </a>
                  {/if}
                </li>
              {/each}
            </ul>
          </div>

          {#if overview.template_summary.cursor_persist}
            <div class="card" data-testid="cursors-summary">
              <h3>Cursors</h3>
              {#if overview.cursors.length === 0}
                <p class="hint">No cursor state yet (generate to initialise).</p>
              {:else}
                <ul class="cursor-list">
                  {#each overview.cursors as c}
                    <li data-testid="cursor-row">
                      <span class="mono">{c.name}</span>
                      <span class="cursor-pos">{c.position.toLocaleString()}</span>
                    </li>
                  {/each}
                </ul>
              {/if}
            </div>
          {/if}

          <div class="card" data-testid="generations-summary">
            <h3>Generations</h3>
            <dl>
              <dt>Total</dt>
              <dd>{overview.generations.count.toLocaleString()}</dd>
              {#if overview.generations.last_generated_week !== null}
                <dt>Latest</dt>
                <dd>{overview.cadence.toUpperCase()} {String(overview.generations.last_generated_week).padStart(3, '0')}</dd>
              {/if}
            </dl>
            {#if overview.generations.stale_count > 0}
              <!-- §16.2.3 staleness hint, homed in §16.19. -->
              <p class="stale-hint" data-testid="stale-hint">
                {overview.generations.stale_count.toLocaleString()}
                {overview.generations.stale_count === 1 ? 'playlist was' : 'playlists were'}
                generated under a previous template — regenerate to apply your edits.
              </p>
            {/if}
          </div>
        </div>
      </section>

      <!-- §16.25 c5 (D3 primary): per-station <PoolImportPanel> embed.
           Scope filters the imported-pools list to ONLY this station's
           declared sources, so the user sees what's relevant here.
           The add-form is unconstrained (any name is acceptable, but
           the user typically came here to fill a "not imported" gap
           from the overview card above). -->
      <section class="overview-pool-import" data-testid="overview-pool-import">
        <h2>Manage source pools</h2>
        <PoolImportPanel
          scope={overview.pools.map((p) => p.name)}
          on:changed={refreshOverview}
        />
      </section>
    {/if}

    <StationServerPanel {slug} name={station?.name ?? slug} {playlists} />

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
          class="cta btn-primary"
          type="button"
          data-testid="generate-empty-cta"
          disabled={generating || deleting}
          on:click={() => generate(1)}
        >
          {generating ? 'Generating…' : 'Generate week 1 →'}
        </button>
      {:else}
        <button
          class="btn-primary"
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

      <!-- §16.5 c4 — generate failure via ErrorBanner. Polite
           severity per the §16.5 severity policy: this is the
           recoverable "regen failed, try again" path; assertive
           would interrupt SR speech on every retry. testId
           preserves existing e2e selectors.

           §16.5 c4-review R10 — re-add the {#if} guard so the
           call-site visual-grep contract ('banner shown iff
           generateError truthy') stays local. ErrorBanner's
           internal {#if message} would also suppress render —
           the guard is for reader-locality, not correctness. -->
      {#if generateError}
        <ErrorBanner
          message={generateError}
          severity="polite"
          prefix="Generate"
          testId="generate-error"
        />
      {/if}
    </section>

    {#if playlists.length === 0}
      <p class="hint">
        None generated yet — click the button above, or run
        <code>python -m scripts.bootstrap_classics</code> from the repo root.
      </p>
    {:else}
      <!-- data-testid set so e2e can disambiguate from §16.19 pools
           table + §16.25 PoolImportPanel embed (both also use <table>). -->
      <table data-testid="playlists-table" class="playlists-table">
        <thead>
          <!-- §16.5 c4 — visually-hidden labels for the three
               action columns (view, regen, delete). Empty <th>s
               read aloud as "blank" in SR-table-navigation mode;
               named columns give the screen reader something to
               announce as the user tabs through cells. The
               .visually-hidden utility lives in +layout.svelte. -->
          <tr>
            <th>Week</th>
            <th>Slots</th>
            <th>Generated</th>
            <th>Trace</th>
            <th><span class="visually-hidden">View</span></th>
            <th><span class="visually-hidden">Regenerate</span></th>
            <th><span class="visually-hidden">Delete</span></th>
          </tr>
        </thead>
        <tbody>
          <!-- §16.20 c2 — `data-label` attrs feed the
               narrow-viewport `td::before { content: attr(data-label) }`
               rule. The action-column cells (View/Regen/Delete)
               don't carry a label because their buttons already
               read the action; doubling would be noisy. -->
          {#each playlists as p}
            <tr>
              <td class="num" data-label="Week">{String(p.week).padStart(3, '0')}</td>
              <td class="num" data-label="Slots">{p.entry_count}</td>
              <td data-label="Generated">{fmtDate(p.generated_at)}</td>
              <td class="mono" data-label="Trace">{p.trace_id.slice(0, 8)}…</td>
              <!-- §16.20 c2-review R3 — `class="action-cell"`
                   replaces the `td:not([data-label])` selector
                   from c2. The negation was too permissive: a
                   future column added without data-label would
                   silently pick up the inline-block button-row
                   styling. A whitelist class makes the intent
                   explicit and protects against the drift. -->
              <td class="action-cell"><a href="/playlists/{p.id}">view →</a></td>
              <td class="action-cell">
                <button
                  type="button"
                  class="link-btn"
                  data-testid="regen-row-{p.week}"
                  disabled={generating || deleting}
                  on:click={() => regenRow(p)}
                  title="Regenerate this week — prior trace is saved as a version"
                >
                  ↻ regen
                </button>
              </td>
              <td class="action-cell">
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
      <!-- §16.5 c4 — delete failure via ErrorBanner. Polite —
           the row delete is recoverable (the row is still there).
           §16.5 c4-review R8 — `compact` keeps the bordered box
           from overweighting a per-row failure; R10 — re-add the
           {#if} guard for call-site visibility-grep. -->
      {#if deleteError}
        <ErrorBanner
          message={deleteError}
          severity="polite"
          prefix="Delete"
          compact
          testId="delete-error"
        />
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
    color: var(--muted);
    text-decoration: none;
  }
  .crumbs a:hover {
    text-decoration: underline;
  }
  h1 {
    margin: 0;
  }
  .slug {
    color: var(--text-muted);
    font-family: ui-monospace, monospace;
    margin-top: 0.1rem;
  }
  .station-header {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .station-header h1 {
    margin-bottom: 0.1rem;
  }
  .header-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
  }
  /* Station Player-style buttons: Edit is the main one, Delete is red
     text so it's hard to misclick (DESIGN.md §16.3 D7). */
  .visualise-link,
  .rules-link,
  .fork,
  .nuke {
    align-items: center;
    background: var(--panel-2);
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    color: var(--text);
    cursor: pointer;
    display: inline-flex;
    font-size: 14px;
    font-weight: 550;
    min-height: 36px;
    padding: 7px 14px;
    text-decoration: none;
    white-space: nowrap;
  }
  .visualise-link:hover,
  .rules-link:hover,
  .fork:hover:not(:disabled) {
    background: var(--panel-3);
    border-color: var(--faint);
    color: var(--text);
  }
  /* Edit station: the theme's main button, the header row's size. */
  .edit-link {
    align-items: center;
    display: inline-flex;
    font-size: 14px;
    min-height: 36px;
    padding: 7px 14px;
    white-space: nowrap;
  }
  .nuke {
    color: var(--danger);
    margin-left: auto;
  }
  .nuke:hover:not(:disabled) {
    background: var(--danger-soft);
    border-color: var(--danger);
  }
  .nuke:disabled,
  .fork:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .fork-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-top: 0.75rem;
    padding: 0.75rem;
    border: 1px solid var(--border, #ccc);
    border-radius: 4px;
    background: var(--bg-subtle, #fafafa);
    max-width: 28rem;
  }
  .fork-form label {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    font-size: 0.85rem;
  }
  .fork-form input {
    padding: 0.3rem 0.5rem;
    font-size: 0.9rem;
    border: 1px solid var(--border, #ccc);
    border-radius: 3px;
  }
  /* §16.5 c4 — `.fork-error` retired; styling now via ErrorBanner. */
  .fork-actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.25rem;
  }
  h2 {
    margin-top: 2rem;
  }
  /* §16.19 — overview cards */
  .overview {
    margin-top: 1.5rem;
  }
  /* §16.25 c5 — per-station pool-import embed. */
  .overview-pool-import {
    margin-top: 2rem;
    margin-bottom: 2rem;
  }
  .overview-pool-import h2 {
    margin-bottom: 0.75rem;
  }
  .overview-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 1rem;
  }
  .card {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.75rem 1rem;
    background: var(--panel);
  }
  .card h3 {
    margin: 0 0 0.5rem;
    font-size: 0.95rem;
  }
  .card dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 0.2rem 0.6rem;
    margin: 0;
    font-size: 0.85rem;
  }
  .card dt {
    color: var(--text-muted);
  }
  .card dd {
    margin: 0;
  }
  .card-link {
    display: inline-block;
    margin-top: 0.6rem;
    font-size: 0.85rem;
    color: var(--accent);
    text-decoration: none;
  }
  .card-link:hover {
    text-decoration: underline;
  }
  .pill {
    display: inline-block;
    background: var(--panel-2);
    border-radius: 999px;
    padding: 0.05rem 0.45rem;
    font-size: 0.75rem;
    margin: 0 0.2rem 0.2rem 0;
    font-family: ui-monospace, monospace;
  }
  .hint-inline {
    color: var(--text-muted);
    font-size: 0.75rem;
  }
  .pool-list,
  .cursor-list {
    list-style: none;
    padding: 0;
    margin: 0;
    font-size: 0.85rem;
  }
  .pool-list li,
  .cursor-list li {
    display: flex;
    gap: 0.5rem;
    align-items: baseline;
    padding: 0.2rem 0;
    border-bottom: 1px solid var(--border);
  }
  .pool-name {
    font-weight: 500;
  }
  .pool-role {
    color: var(--text-muted);
    font-size: 0.78rem;
  }
  .pool-count {
    margin-left: auto;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .pool-missing {
    margin-left: auto;
    color: var(--danger);
    font-size: 0.78rem;
    text-decoration: underline;
  }
  .pool-missing:hover {
    color: var(--danger);
  }
  .pool-list li.missing .pool-name {
    color: var(--danger);
  }
  .cursor-pos {
    margin-left: auto;
    font-variant-numeric: tabular-nums;
  }
  .stale-hint {
    margin: 0.5rem 0 0;
    padding: 0.4rem 0.6rem;
    background: var(--panel-2);
    border: 1px solid var(--warn);
    border-radius: 4px;
    font-size: 0.82rem;
  }
  .generate {
    margin: 1rem 0 1.5rem;
    padding: 0.75rem 1rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  .cta {
    font-size: 1.1rem;
    padding: 0.75rem 1.5rem;
  }
  .replace {
    padding: 0.5rem 1rem;
    background: var(--danger);
    color: var(--accent-text);
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .replace:hover:not(:disabled) {
    background: var(--danger);
  }
  .ghost {
    padding: 0.5rem 0.75rem;
    background: var(--panel);
    border: 1px solid var(--border);
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
    color: var(--muted);
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
    color: var(--muted);
  }
  .advanced-body input[type='number'] {
    padding: 0.4rem;
    border: 1px solid var(--border);
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
    border-bottom: 1px solid var(--border);
  }
  th {
    font-weight: 600;
    font-size: 0.85rem;
    color: var(--muted);
    border-bottom: 2px solid var(--border);
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .mono {
    font-family: ui-monospace, monospace;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .link-btn {
    background: none;
    border: none;
    color: var(--danger);
    cursor: pointer;
    font-size: 0.85rem;
    padding: 0;
  }
  .link-btn:hover:not(:disabled) {
    text-decoration: underline;
  }
  .link-btn:disabled {
    color: var(--text-muted);
  }
  /* §16.5 c4 — `.error` retired; styling now via ErrorBanner. */
  .hint {
    color: var(--muted);
    font-size: 0.9rem;
  }
  code {
    background: var(--panel-2);
    padding: 0.1em 0.3em;
    border-radius: 3px;
  }

  /* ---- §16.20 c2: mobile / narrow-viewport ---------------------- */
  /* Below 40rem the playlists table rotates to cards. Each <tr>
     becomes a stacked block with the column meaning surfaced via
     the data-label ::before pseudo-element. View / Regen / Delete
     buttons stack at the bottom of each card with a thin separator. */
  @media (max-width: 40rem) {
    .playlists-table,
    .playlists-table thead,
    .playlists-table tbody,
    .playlists-table tr,
    .playlists-table th,
    .playlists-table td {
      display: block;
    }
    .playlists-table thead {
      /* Visually-hidden but stays in the a11y tree (same pattern as
         the global `.visually-hidden` utility in +layout.svelte). */
      border: 0;
      clip: rect(0, 0, 0, 0);
      clip-path: inset(50%);
      height: 1px;
      margin: -1px;
      overflow: hidden;
      padding: 0;
      position: absolute;
      white-space: nowrap;
      width: 1px;
    }
    .playlists-table tr {
      border: 1px solid var(--border);
      border-radius: 4px;
      margin-bottom: 0.75rem;
      padding: 0.6rem;
    }
    .playlists-table td {
      padding: 0.2rem 0;
      text-align: left;
    }
    .playlists-table td[data-label]::before {
      content: attr(data-label);
      display: inline-block;
      min-width: 5rem;
      font-size: 0.75rem;
      color: var(--muted);
      font-weight: 600;
      text-transform: uppercase;
      margin-right: 0.5rem;
    }
    /* Action cells (View / Regen / Delete) — `action-cell`
       whitelist (c2-review R3) instead of `td:not([data-label])`
       so a future label-less column doesn't accidentally pick
       up the button-row styling. Stacks inline so the three
       cells share a row at the bottom of each card. */
    .playlists-table td.action-cell {
      display: inline-block;
      margin-right: 0.75rem;
      margin-top: 0.4rem;
    }
  }

  /* ---- §16.20 c2: filter bar wraps + station-header stacks ------ */
  @media (max-width: 40rem) {
    .station-header,
    .header-actions {
      flex-direction: column;
      align-items: flex-start;
      gap: 0.5rem;
    }
  }
</style>
