<script lang="ts">
  import {
    getPlaylist,
    getTrace,
    getStation,
    getStationPlaylists,
    getVersions,
    exportPlaylistToDisk,
    errorText,
    getStationServerSettings,
    getStationSidecar,
    getStationRules,
    sendToStationServer,
    rollbackPlaylist,
    setVersionTags,
    startGenerateJob,
    watchJob,
    isGenerateResult,
    JobStartConflict,
    ApiError,
  } from '$lib/api';
  import type {
    Playlist, PlaylistSummary, Trace, TraceSlot, Station, VersionSummary,
    StationServerTarget,
  } from '$lib/api';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import ChipMultiInput from '$lib/components/editor/ChipMultiInput.svelte';
  import ErrorBanner from '$lib/components/ErrorBanner.svelte';
  import StatusBanner from '$lib/components/StatusBanner.svelte';
  import { sentText } from '$lib/stationServer';
  import { onDestroy } from 'svelte';
  import SongList from '$lib/components/songs/SongList.svelte';
  import DetailPanel from '$lib/components/editor/DetailPanel.svelte';
  import { findSectionIdForRef, type ExpandedRef, type ParsedEditorSidecar } from '$lib/editor';
  import {
    queuedChanges, ruleDefaultsFrom, ruleKey, songsChanged, type RuleDefaults, type SongRow,
  } from '$lib/songList';
  import {
    createStagedEditSession,
    type StagedEditSession,
    type StagedEditState,
  } from '$lib/stagedEditSession';
  import { editsFromPanel, type PanelEvent } from '$lib/panelEdits';
  import { readStagedEdits } from '$lib/stagedEdits';

  let trace: Trace | null = null;
  // The station's template + sidecar (sections, macros) for the Songs
  // view. null when they couldn't be loaded: the Songs view isn't offered.
  let editorData: ParsedEditorSidecar | null = null;

  // Songs (each song with its rule and why) or the table by row. Kept in
  // localStorage for every week page; the table stays the default.
  const WEEK_VIEW_KEY = 'weekView';
  function readWeekView(): 'songs' | 'table' {
    try {
      return window.localStorage.getItem(WEEK_VIEW_KEY) === 'songs' ? 'songs' : 'table';
    } catch {
      return 'table';
    }
  }
  let view: 'songs' | 'table' = typeof window === 'undefined' ? 'table' : readWeekView();
  function setView(next: 'songs' | 'table') {
    view = next;
    try {
      window.localStorage.setItem(WEEK_VIEW_KEY, next);
    } catch {
      // Privacy mode or full: the choice just isn't remembered.
    }
  }
  let selectedSong: number | null = null;

  // ---- editing a song's rule (Songs view) --------------------------------
  // A staged-edit session for this week, made the first time a song is
  // picked: edits are queued and previewed for this week, and saved for
  // every week in one go. Only on the current version of the week (an
  // older version shows songs picked by rules that may since have
  // changed; edits apply to today's rules).
  let session: StagedEditSession | null = null;
  /** The session's state, kept here by subscription. */
  let editState: StagedEditState | null = null;
  let unsubscribe: (() => void) | null = null;
  /** The station the session is for. */
  let sessionStation = '';
  /** The picked song's rule, kept by ref so it survives edits. */
  let selectedRef: ExpandedRef | null = null;
  let justSaved = false;
  let regenerating = false;
  let regenerateError: string | null = null;

  $: viewingCurrent = requestedVersion == null || requestedVersion === currentV;
  $: editable = !!editorData && viewingCurrent;

  function endSession(): void {
    unsubscribe?.();
    unsubscribe = null;
    session?.destroy();
    session = null;
    editState = null;
    sessionStation = '';
    selectedRef = null;
    justSaved = false;
  }
  // Another station (a link) ends the session. Another week of the same
  // station keeps it: the changes are for every week, so they carry over
  // and are previewed on the new week, and the picked rule stays open.
  $: if (session && trace) {
    if (sessionStation !== trace.station) endSession();
    else if (editState && editState.week !== trace.week) session.setWeek(trace.week);
  }
  onDestroy(endSession);

  function startSession(t: Trace): void {
    session = createStagedEditSession(t.station, { week: t.week, previewOnlyWhenEdited: true });
    unsubscribe = session.subscribe((v) => (editState = v));
    sessionStation = t.station;
    void session.load();
  }

  function pickSong(row: SongRow): void {
    selectedSong = row.position;
    selectedRef = row.ref;
    if (editable && trace && !session) startSession(trace);
  }

  // Changes left unsaved on an earlier visit (here or in the station
  // editor) show straight away, not only once a song is clicked.
  $: if (editable && view === 'songs' && trace && !session && readStagedEdits(trace.station)) {
    startSession(trace);
  }

  // The bar only when it has something to say.
  $: showEditBar = !!editState && (
    editState.recovered !== null || editState.opLog.length > 0 || justSaved ||
    !!editState.loadError || !!editState.previewError || !!editState.saveError ||
    editState.conflict || editState.failedAtIdx > 0 || !!regenerateError
  );

  function closePanel(): void {
    selectedRef = null;
    selectedSong = null;
  }

  function onPanel(event: PanelEvent): void {
    // The rule's own change, then the same to each identical rule the
    // operator ticked.
    const edits = editsFromPanel(event);
    if (edits.length > 0 && session) {
      for (const edit of edits) session.stage(edit);
      justSaved = false;
    }
  }

  async function saveRules(): Promise<void> {
    if (session && (await session.save())) justSaved = true;
  }

  // The panel needs the picked block's section, looked up in the edited
  // sidecar so it follows moves; a block that has gone closes it.
  $: editing = editState && !editState.loading && editState.staged.template && editState.staged.sidecar
    ? editState
    : null;
  // Not on an older version of the week: it shows what was picked then,
  // and rules can only be changed from the current one.
  $: panelSelection = (() => {
    if (!editing || !selectedRef || !viewingCurrent) return null;
    const sidecar = editing.staged.sidecar!;
    const id = findSectionIdForRef(sidecar, selectedRef);
    const section = id ? sidecar.sections.find((x: { id: string }) => x.id === id) : undefined;
    return section ? { ref: selectedRef, section } : null;
  })();

  // What the list shows: the week with the queued edits once that preview
  // is in, marked against the same week under the saved rules; otherwise
  // the week as generated.
  // The queued changes in plain words, against the rules as saved; a
  // change that went to identical rules too is one line.
  $: queued = editState && editState.template && editState.sidecar && editState.opLog.length > 0
    ? queuedChanges(editState.opLog, editState.template, editState.sidecar, ruleDefaults)
    : [];

  /** Undo one change: every queued edit it is, last first so the
   *  places of the rest don't shift. */
  function undoChange(indices: number[]): void {
    for (const i of [...indices].reverse()) session?.unstage(i);
  }
  function movedText(n: number, of: number): string {
    if (n === 0) return `No songs in week ${trace?.week} change.`;
    return `${n} of ${of} ${of === 1 ? 'song' : 'songs'} in week ${trace?.week} change.`;
  }

  $: showingEdits = viewingCurrent && !!editing && editing.opLog.length > 0 && editing.stagedTrace !== null;

  /** Remake this week with the saved rules. rng='on' reuses the week's
   *  seed, so only songs the rule change touches should move. */
  async function regenerateWeek(): Promise<void> {
    if (trace) await regenerateWeeks([trace.week]);
  }

  /** The station's generated weeks after this one: they were made with
   *  the old rules too. */
  $: laterWeeks = trace
    ? stationPlaylists.map((p) => p.week).filter((w) => w > trace!.week).sort((a, b) => a - b)
    : [];

  /** "4–6", or "4, 6 and 9" when there are gaps. */
  function weekList(weeks: number[]): string {
    const contiguous = weeks.every((w, i) => i === 0 || w === weeks[i - 1] + 1);
    if (weeks.length === 1) return `week ${weeks[0]}`;
    if (contiguous) return `weeks ${weeks[0]}–${weeks[weeks.length - 1]}`;
    return `weeks ${weeks.slice(0, -1).join(', ')} and ${weeks[weeks.length - 1]}`;
  }

  /** Which week is being remade, for the progress note. */
  let regenerateProgress: { week: number; done: number; of: number } | null = null;

  /** Said once a run of regenerations finishes (the bar closes then, and
   *  later weeks don't show here), until the page moves to another week. */
  let regenerated: { text: string; at: string } | null = null;
  $: if (regenerated && trace && regenerated.at !== `${trace.station}:${trace.week}`) regenerated = null;

  /** Remake these weeks with the saved rules, one at a time in order
   *  (each continues from the one before: last week's songs, cursors).
   *  The server starts each from the week's own seed and cursors, so
   *  only songs the rule change touches should move. Stops at the
   *  first that fails, and says which. */
  async function regenerateWeeks(weeks: number[]): Promise<void> {
    if (!trace || regenerating || weeks.length === 0) return;
    regenerating = true;
    regenerateError = null;
    const slug = trace.station;
    const thisWeek = trace.week;
    try {
      for (const [i, week] of weeks.entries()) {
        regenerateProgress = { week, done: i, of: weeks.length };
        // No seed: the server reuses the week's own (rng='on').
        let outcome: string | null;
        try {
          outcome = await generateOne(slug, week);
        } catch (e) {
          outcome = errorText(e);
        }
        if (outcome !== null) {
          regenerateError = weeks.length > 1 ? `week ${week}: ${outcome}` : outcome;
          // The weeks before it did regenerate: show this one as it now
          // is, keeping the bar (and this message) up.
          if (i > 0) await loadAll(id, requestedVersion);
          return;
        }
      }
      await reloadWeek();
      regenerated = {
        text: weeks.length === 1
          ? `Regenerated week ${weeks[0]} with the saved rules.`
          : `Regenerated ${weekList(weeks)} with the saved rules.`,
        at: `${slug}:${thisWeek}`,
      };
    } catch (e) {
      regenerateError = errorText(e);
    } finally {
      regenerating = false;
      regenerateProgress = null;
    }
  }

  /** Generate one week and wait for it: null when done, else why not.
   *  One generate runs per station at a time: if another is running (a
   *  week started from the station page, say), wait for it to finish,
   *  then start this one. That job is a different generation, so its
   *  finishing isn't this week's. */
  async function generateOne(slug: string, week: number): Promise<string | null> {
    let jobId: string | null = null;
    for (let attempt = 0; jobId === null; attempt++) {
      try {
        jobId = (await startGenerateJob(slug, week)).job_id;
      } catch (e) {
        if (!(e instanceof JobStartConflict) || attempt >= 5) throw e;
        try {
          for await (const snap of watchJob(e.existing_job_id)) {
            if (snap.status !== 'running') break;
          }
        } catch {
          // Gone already: try starting this week again.
        }
      }
    }
    for await (const snap of watchJob(jobId)) {
      if (snap.status === 'done' && isGenerateResult(snap.result)) return null;
      if (snap.status === 'error') return snap.error ? snap.error.message : 'regeneration failed';
      if (snap.status === 'cancelled') return 'cancelled';
    }
    return 'regeneration ended without a result';
  }

  /** Show this week as now generated (the current version). */
  async function reloadWeek(): Promise<void> {
    endSession();
    if (requestedVersion != null) {
      const url = new URL($page.url);
      url.searchParams.delete('v');
      await goto(url.pathname + url.search);
    } else {
      await loadAll(id, null);
    }
  }
  let playlist: Playlist | null = null;
  let station: Station | null = null;
  let stationPlaylists: PlaylistSummary[] = [];
  let ruleDefaults: RuleDefaults = {};
  let error: string | null = null;
  let loading = true;

  // Disk-export state (DESIGN.md §16.6 D3). Two reactive vars rather than a
  // single string-with-flag so the template can apply distinct styles cleanly.
  let diskExporting = false;
  let diskExportStatus: string | null = null;
  let diskExportError: string | null = null;

  // Send to Station Server (engine/stationserver.py): shown once this
  // station has a Station Server station to send to (its page sets it).
  let serverTarget: StationServerTarget | null = null;
  let sending = false;
  let sendStatus: string | null = null;
  let sendError: string | null = null;

  // Filter state (DESIGN.md §16.8). Three independent inputs that AND
  // together (D3); empty string = no constraint, not wildcard.
  let searchQuery = '';
  let sourceFilter = '';
  let strategyFilter = '';

  $: id = Number($page.params.id);

  // §16.4 — version-picker state. `requestedVersion` parses the
  // `?v=N` URL search-param; null means "no explicit selection →
  // serve the current version". `versions` is the dropdown's data
  // source (oldest first, slots-free per `getVersions`).
  // `currentV` is the server-reported max (used so the picker
  // labels the latest correctly even before a fresh /versions
  // round-trip completes).
  $: requestedVersion = (() => {
    const raw = $page.url.searchParams.get('v');
    if (raw == null) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n > 0 ? n : null;
  })();
  let versions: VersionSummary[] = [];
  let currentV = 0;

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
  async function loadAll(playlistId: number, version: number | null) {
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
      // station / playlist-list / versions fetches are independent and
      // parallelise (review N2). Buffer all results in locals; swap to
      // component state at the end so reloads don't flash a half-state
      // where the header is week 4 but the rows are still week 3.
      //
      // §16.4 — `version` selects a specific PlaylistVersion via
      // `?v=N`; null falls back to current. The trace's `trace_id`
      // tells us which version we got, so the picker can show the
      // right selection even without a separate /versions race.
      const t = await getTrace(playlistId, version);
      if (myGen !== loadGeneration) return;

      // §16.4 — versions list feeds the picker dropdown. Failure is
      // non-fatal (matches stationPlaylists pattern): the dropdown
      // just won't render. .catch inside Promise.all so a flaky
      // /versions endpoint doesn't poison the others.
      const [p, s, list, vlist, links, ed, rules] = await Promise.all([
        getPlaylist(playlistId),
        getStation(t.station),
        getStationPlaylists(t.station).catch(() => [] as PlaylistSummary[]),
        getVersions(playlistId).catch(
          () => ({ playlist_id: playlistId, current_v: 0, versions: [] }),
        ),
        getStationServerSettings().catch(() => null),
        // Songs view only: failure just means no Songs view.
        getStationSidecar(t.station).catch(() => null),
        // The station's defaults for rule settings, for the rules in
        // words; without them the engine's own defaults are said.
        getStationRules(t.station).catch(() => null),
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
      serverTarget = links?.stations?.[t.station]?.station ? links.stations[t.station] : null;
      versions = vlist.versions;
      currentV = vlist.current_v;
      sourceColours = Object.fromEntries(sources.map((src) => [src.name, src.colour]));
      rowGroups = groupByRow(t.slots);
      editorData = ed;
      ruleDefaults = ruleDefaultsFrom(rules?.rules ?? []);
      selectedSong = null;
    } catch (e) {
      if (myGen !== loadGeneration) return;
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      if (myGen === loadGeneration) loading = false;
    }
  }

  // Reactive: re-fetch whenever the route param OR the ?v=N
  // search-param changes. Fires once on initial mount AND on every
  // subsequent navigation between sibling playlists OR version
  // switches. The `void` annotation is just to acknowledge the
  // dropped promise — errors land in `error` via loadAll's catch.
  $: if (!isNaN(id)) void loadAll(id, requestedVersion);

  /**
   * §16.4 — version-picker change handler. Updates the URL via
   * `goto(?v=N)`; the reactive watcher above re-fires loadAll with
   * the new version. URL is the source of truth so permalinks +
   * back-button work; component state follows.
   */
  function onVersionChange(event: Event) {
    const target = event.currentTarget as HTMLSelectElement;
    const next = Number(target.value);
    if (!Number.isInteger(next) || next < 1) return;
    const url = new URL($page.url);
    url.searchParams.set('v', String(next));
    void goto(url.toString().replace(url.origin, ''), {
      keepFocus: true,
      noScroll: true,
      replaceState: false,
    });
  }

  // §16.4 — the version actually rendered. Trace's `trace_id` is the
  // authoritative answer (the server picked it); we cross-reference
  // it against the versions list to label the picker selection. Pre-
  // /versions-arrival (race), fall back to the URL's requested value
  // OR currentV so the dropdown isn't blank.
  $: selectedVersionNo = (() => {
    if (trace && versions.length > 0) {
      const match = versions.find((v) => v.trace_id === trace!.trace_id);
      if (match) return match.version_no;
    }
    if (requestedVersion != null) return requestedVersion;
    return currentV;
  })();

  // §16.4.t4 — show rollback affordance only when viewing a non-
  // current version. Rolling back to current would be a no-op
  // (backend 400s); hiding the button is the obvious UI guard.
  $: canRollback =
    versions.length > 1
    && selectedVersionNo !== currentV
    && selectedVersionNo > 0;

  // §16.4.t5 — tags for the selected version. Pulled from the
  // versions list (single source of truth); the PATCH path mutates
  // BOTH the server + the local list so the picker labels stay in
  // sync without an extra getVersions() round-trip.
  $: selectedVersion = versions.find((v) => v.version_no === selectedVersionNo);
  // Defensive `?? []` — `tags` is a §16.4.t5 addition to
  // VersionSummary; legacy fixtures (and any pre-§16.4.t5 cached
  // response a stale tab might receive) won't carry the field.
  $: selectedTags = selectedVersion?.tags ?? [];
  let tagsSaving = false;
  let tagsError: string | null = null;

  async function handleTagsChange(event: CustomEvent<string[]>) {
    if (!playlist || !selectedVersion) return;
    // ChipMultiInput dispatches the full new array as event.detail.
    const next = event.detail;
    tagsSaving = true;
    tagsError = null;
    try {
      const updated = await setVersionTags(
        playlist.id, selectedVersion.version_no, next,
      );
      // Adopt the server's canonical form (trim + dedupe) by patching
      // the versions list in place. Avoids a fresh getVersions
      // round-trip + keeps the picker labels reactive immediately.
      versions = versions.map((v) =>
        v.version_no === updated.version_no
          ? { ...v, tags: updated.tags }
          : v,
      );
    } catch (e) {
      tagsError = e instanceof ApiError ? e.message : String(e);
    } finally {
      tagsSaving = false;
    }
  }

  /**
   * §16.4.t4 — roll back to the selected version. POSTs to
   * /rollback?to=N + on success: refetches versions + navigates to
   * the new current (clears ?v=N so the picker lands on the new
   * max). Confirm dialog explains the non-destructive semantics so
   * the operator doesn't fear losing the current.
   */
  let rollingBack = false;
  let rollbackError: string | null = null;
  async function handleRollback() {
    if (!canRollback || !playlist) return;
    const targetVersion = selectedVersionNo;
    const ok = window.confirm(
      `Roll back to v${targetVersion}?\n\n` +
        `A new current version will be created with v${targetVersion}'s ` +
        `content. The existing current (v${currentV}) stays in history ` +
        `as a prior version — nothing is deleted.`,
    );
    if (!ok) return;
    rollingBack = true;
    rollbackError = null;
    try {
      await rollbackPlaylist(playlist.id, targetVersion);
      // Land on the new current by clearing ?v= from the URL; the
      // reactive watcher refetches.
      const url = new URL($page.url);
      url.searchParams.delete('v');
      await goto(url.toString().replace(url.origin, ''), {
        keepFocus: true, noScroll: true,
      });
    } catch (e) {
      rollbackError = e instanceof ApiError ? e.message : String(e);
    } finally {
      rollingBack = false;
    }
  }

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
  // §16.4.t2 — when the operator is viewing a historical version
  // (`?v=N`, N ≠ currentV), the export buttons target THAT version.
  // Returns the version_no to send to the export endpoints, or null
  // for the current-version path (which keeps the default filename).
  $: exportVersion = (() => {
    if (!versions || versions.length === 0) return null;
    if (selectedVersionNo === currentV) return null;
    return selectedVersionNo;
  })();

  /**
   * §16.4.t6 — archival opt-in. Persisted per-station so an operator
   * who flips it on for Classics doesn't have to re-flip it on the
   * next session. When ON + the operator exports a CURRENT version,
   * the API gets `?versioned=true` → filename gets the current
   * version_no suffix → each regen accumulates one file rather than
   * overwriting.
   *
   * Identity key: derived from `trace.station` (which is the
   * station slug — populated after the trace fetch resolves; null
   * before then). Until the slug arrives, the checkbox stays in
   * its session-default OFF state.
   */
  let archiveDiskExports = false;

  function _archiveDiskExportsKey(slug: string): string {
    return `archiveDiskExports:${slug}`;
  }

  function readArchiveDiskExports(slug: string): boolean {
    if (typeof window === 'undefined') return false;
    try {
      return window.localStorage.getItem(_archiveDiskExportsKey(slug)) === 'on';
    } catch {
      return false;
    }
  }

  function writeArchiveDiskExports(slug: string, on: boolean): void {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(
        _archiveDiskExportsKey(slug), on ? 'on' : 'off',
      );
    } catch {
      // Quota / privacy-mode block — checkbox state just doesn't
      // persist for the rest of the session.
    }
  }

  // Hydrate the checkbox once the slug is available (i.e. once
  // `trace.station` resolves). Reactive on `trace?.station` rather
  // than `playlist?.id` because the page's "loading transitions
  // between sibling weeks" pattern can keep `playlist` stale during
  // the swap; trace.station IS the slug.
  $: if (trace?.station) {
    archiveDiskExports = readArchiveDiskExports(trace.station);
  }

  function toggleArchiveDiskExports(event: Event) {
    if (!trace?.station) return;
    archiveDiskExports = (event.currentTarget as HTMLInputElement).checked;
    writeArchiveDiskExports(trace.station, archiveDiskExports);
  }

  async function exportToDisk() {
    if (!playlist) return;
    diskExporting = true;
    diskExportStatus = null;
    diskExportError = null;
    try {
      // §16.4.t6 — pass `archiveDiskExports` as the `versioned` flag.
      // The backend ignores it when `version` is set (historical
      // path's filename is already suffixed); the flag is meaningful
      // only on the current-version path.
      const r = await exportPlaylistToDisk(
        playlist.id, exportVersion, archiveDiskExports,
      );
      // §16.5.t2 — the "Wrote" verb now lives in StatusBanner's
      // `prefix` prop so the rendered banner reads "Wrote:
      // /abs/.../001.xml (849 KB)". Setting "Wrote to ..." here
      // would render "Wrote: Wrote to ..." (caught in live
      // verification).
      diskExportStatus = `${r.path} (${formatBytes(r.bytes)})`;
      // A station set to send each week also sent it to Station Server.
      if (r.station_server) {
        sendStatus = sentText(r.station_server);
        sendError = null;
      } else if (r.station_server_error) {
        sendError = r.station_server_error;
        sendStatus = null;
      }
    } catch (e) {
      diskExportError = e instanceof ApiError ? e.message : String(e);
    } finally {
      diskExporting = false;
    }
  }

  async function sendToServer() {
    if (!playlist) return;
    sending = true;
    sendStatus = null;
    sendError = null;
    try {
      sendStatus = sentText(await sendToStationServer(playlist.id));
    } catch (e) {
      sendError = errorText(e);
    } finally {
      sending = false;
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

<main id="main-content" tabindex="-1">
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
        <!--
          §16.23 — "Diff with prev week" link. Only renders when a
          prev sibling exists (no prev → nothing to compare). Targets
          `/stations/[slug]/diff?from=<prev>&to=<current>`, mirroring
          the operator's "what changed in this week vs the prior?"
          framing. The week-diff page is station-scoped (not
          playlist-scoped), so the href routes there rather than
          back to /playlists/.
        -->
        {#if siblings.prev && trace}
          <a
            href="/stations/{trace.station}/diff?from={siblings.prev.week}&to={trace.week}"
            class="week-nav-link diff-link"
            data-testid="week-diff-link"
          >
            ⇆ diff with prev week
          </a>
        {/if}
      </nav>
    {/if}

    <!--
      §16.4 trace versioning v1 — picker dropdown.

      Renders only when the playlist has >1 version (single-version
      playlists don't need a chooser; the trace IS that version).
      Selecting an entry calls `goto(?v=N)`; the reactive
      `requestedVersion` derivation drives loadAll, which calls
      getTrace(id, N). URL is the source of truth so permalinks +
      back-button work without extra plumbing.

      The "current" label on the latest row mirrors the server's
      `current_v` so the operator knows which version is the one
      iTunes will export.
    -->
    {#if versions.length > 1}
      <div class="version-picker" data-testid="version-picker">
        <label for="version-select">Version</label>
        <select
          id="version-select"
          data-testid="version-select"
          value={selectedVersionNo}
          on:change={onVersionChange}
        >
          {#each versions as v (v.version_no)}
            <option value={v.version_no} data-version={v.version_no}>
              v{v.version_no}{v.version_no === currentV ? ' (current)' : ''}
              · {fmtDate(v.generated_at)}
              {v.seed !== null ? ` · seed ${v.seed}` : ''}
              {(v.tags ?? []).length > 0 ? ` · [${(v.tags ?? []).join(', ')}]` : ''}
            </option>
          {/each}
        </select>
        <span class="version-meta" data-testid="version-meta">
          {versions.length} version{versions.length === 1 ? '' : 's'}
        </span>
        <!--
          §16.4.t1 — only renders alongside the picker (i.e. when
          multiple versions exist). Defaults from/to to the
          (current-1, current) pair via the compare page's
          derivation; the operator can then pivot from there.
        -->
        <a
          href="/playlists/{id}/compare"
          class="compare-link"
          data-testid="compare-link"
        >
          Compare versions →
        </a>
        <!--
          §16.4.t4 — rollback affordance. Hidden when viewing the
          current version (rolling back to current = no-op).
          Non-destructive — creates a new max-version row with the
          target's content; current stays as a prior version.
        -->
        {#if canRollback}
          <button
            type="button"
            class="rollback-btn"
            data-testid="rollback-btn"
            disabled={rollingBack}
            on:click={handleRollback}
          >
            {rollingBack ? 'Rolling back…' : `↶ Roll back to v${selectedVersionNo}`}
          </button>
        {/if}
      </div>
      <!-- §16.5 c2 — ErrorBanner gives the rollback failure
           message an aria-live region + a consistent visual
           treatment with every other error in the app. The
           `testId` keeps the existing `rollback-error`
           data-testid live for any external selector.
           §16.5 c5 closeout — downgraded to polite per the
           codebase severity policy: rollback failure is
           recoverable (try again, the prior version still
           exists), so it queues politely rather than
           interrupting SR speech. -->
      <ErrorBanner
        message={rollbackError}
        severity="polite"
        prefix="Rollback failed"
        testId="rollback-error"
      />
      <!--
        §16.4.t5 — inline tag editor for the SELECTED version. Reuses
        the §16.2.4 `<ChipMultiInput>` component. Tags PATCH on every
        change; the response's normalized list is patched back into
        the local `versions` array so the picker labels stay in sync
        without an extra getVersions() round-trip.
      -->
      <div class="version-tags" data-testid="version-tags">
        <!-- `<span>` not `<label>`: ChipMultiInput owns its own
             `<label for=...>` internally; this is a header for the
             group, not an HTML form-label. -->
        <span class="tags-label">Tags (v{selectedVersionNo})</span>
        <ChipMultiInput
          values={selectedTags}
          testIdPrefix="version-tag"
          inputLabel="Add a tag"
          placeholder="e.g. sent-to-iTunes"
          disabled={tagsSaving}
          on:change={handleTagsChange}
        />
      </div>
      <!-- §16.5 c2 — tag-save errors via ErrorBanner. Polite
           severity (the operator can retry; the page isn't broken).
           c2-review R3 — `prefix="Tag save failed"` (not "Tags")
           so the polite announce parses as a failure, not as a
           label echo of the sibling `.tags-label`. -->
      <ErrorBanner
        message={tagsError}
        severity="polite"
        prefix="Tag save failed"
        testId="tags-error"
      />
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
      <!--
        §16.4.t2 — when viewing a historical version, the download
        anchor carries `?v=N` so the server exports THAT version +
        the Content-Disposition filename gets a `-v{N}` suffix. The
        anchor's `data-testid` stays stable; the href is the only
        thing that flexes.
      -->
      <a
        href={exportVersion != null
          ? `/api/playlists/${playlist.id}/export?v=${exportVersion}`
          : `/api/playlists/${playlist.id}/export`}
        class="btn-primary"
        data-testid="download-xml"
      >
        Download iTunes XML{exportVersion != null ? ` (v${exportVersion})` : ''}
      </a>
      <button
        type="button"
        class="btn-primary"
        data-testid="export-to-disk"
        disabled={diskExporting}
        on:click={exportToDisk}
      >
        {diskExporting ? 'Writing…' : 'Export to disk'}
      </button>
      {#if serverTarget && exportVersion == null}
        <button
          type="button"
          class="btn-primary"
          data-testid="send-to-station-server"
          disabled={sending || diskExporting}
          on:click={sendToServer}
          title="Send this week straight to Station Server, on the station's computer"
        >
          {sending ? 'Sending…' : 'Send to Station Server'}
        </button>
      {/if}
    </div>
    <!--
      §16.4.t6 archival opt-in. Renders only when there's >=1
      version on the playlist (no versions = no v-suffix to derive
      anyway). Per-station localStorage; flipping it on for one
      station doesn't affect others. The label tells the operator
      what the filename will look like so the decision is concrete.
    -->
    {#if versions.length > 0}
      <label class="archive-toggle" data-testid="archive-disk-exports-label">
        <input
          type="checkbox"
          data-testid="archive-disk-exports"
          checked={archiveDiskExports}
          on:change={toggleArchiveDiskExports}
          disabled={diskExporting}
        />
        Archive every version to disk
        <span class="hint-inline">
          ({trace?.week ? String(trace.week).padStart(3, '0') : '###'}-v{currentV || '?'}.xml
          instead of {trace?.week ? String(trace.week).padStart(3, '0') : '###'}.xml)
        </span>
      </label>
    {/if}
    <!-- §16.5.t2 — disk-export success via StatusBanner. The
         sibling component matches ErrorBanner's ARIA shape
         (role=status + aria-live=polite) so success + failure
         on the same surface read consistently. testId
         preserves the existing e2e/vitest selector. -->
    {#if diskExportStatus}
      <StatusBanner
        message={diskExportStatus}
        prefix="Wrote"
        testId="disk-export-status"
      />
    {/if}
    <!-- §16.5 c2 — disk-export failures via ErrorBanner. Polite
         severity matches the §16.6 D3 "inline status, not toast"
         decision; assertive would interrupt the screen reader on
         every failure, which is overkill for a retry-able write. -->
    <ErrorBanner
      message={diskExportError}
      severity="polite"
      prefix="Disk export"
      testId="disk-export-error"
    />
    {#if sendStatus}
      <StatusBanner message={sendStatus} prefix="Sent" testId="station-server-sent" />
    {/if}
    <ErrorBanner
      message={sendError}
      severity="polite"
      prefix="Station Server"
      testId="station-server-error"
    />

    <h2>Your working</h2>
    {#if editorData}
      <div class="view-switch" role="group" aria-label="Show the week as" data-testid="week-view">
        <button
          type="button"
          aria-pressed={view === 'songs'}
          data-testid="week-view-songs"
          on:click={() => setView('songs')}
        >Songs</button>
        <button
          type="button"
          aria-pressed={view === 'table'}
          data-testid="week-view-table"
          on:click={() => setView('table')}
        >Table</button>
      </div>
    {/if}
    {#if view === 'songs' && editorData}
    <p class="hint">
      Each song with the rule that put it there and why, in playing order.
      {#if editable}Click a song to change its rule.{/if}
    </p>
    {#if regenerated}
      <p class="hint" role="status" data-testid="song-regenerated">{regenerated.text}</p>
    {/if}
    {#if !viewingCurrent}
      <p class="hint" data-testid="song-edit-older">
        This is an older version of the week. Switch to the current version to change rules.
      </p>
    {/if}

    <div class="songs-area" class:with-panel={!!(editing && panelSelection)}>
    {#if editState && showEditBar}
      <div class="edit-bar" role="region" aria-label="Rule changes" data-testid="song-edit-bar">
        {#if editState.recovered}
          <p>
            {editState.recovered.count}
            {editState.recovered.count === 1 ? 'unsaved change' : 'unsaved changes'} from an earlier
            visit, made before this station last changed.
          </p>
          <div class="edit-actions">
            <button type="button" data-testid="song-edit-keep" on:click={() => session?.acceptRecovered()}>Keep them</button>
            <button type="button" data-testid="song-edit-drop-recovered" on:click={() => session?.discardRecovered()}>Discard them</button>
          </div>
        {:else if editState.opLog.length > 0}
          <p data-testid="song-edit-count">
            <strong>{queued.length || editState.opLog.length} {(queued.length || editState.opLog.length) === 1 ? 'change' : 'changes'}</strong>
            to {editState.opLog.length > queued.length && queued.length > 0
              ? `${editState.opLog.length} of this station's rules`
              : "this station's rules"}, for every week, not just this one.
            {#if editState.previewing}<span class="muted" data-testid="song-edit-previewing">Previewing week {trace.week}…</span>
            {:else if showingEdits && editing?.stagedTrace}<span class="muted" data-testid="song-edit-moved">{movedText(songsChanged(editing.stagedTrace, editing.savedTrace), editing.stagedTrace.slots.length)}</span>{/if}
          </p>
          <ol class="queued" data-testid="song-edit-list">
            {#each queued as change (change.indices.join(','))}
              <li data-testid="song-edit-item">
                <span>{change.text}</span>
                <button type="button" class="link" data-testid="song-edit-undo" disabled={editState.saving}
                  on:click={() => undoChange(change.indices)}>Undo</button>
              </li>
            {/each}
          </ol>
          <div class="edit-actions">
            <button type="button" class="btn-primary" data-testid="song-edit-save" disabled={editState.saving} on:click={saveRules}>
              {editState.saving ? 'Saving…' : 'Save changes'}
            </button>
            <button type="button" data-testid="song-edit-discard" disabled={editState.saving} on:click={() => session?.discard()}>Discard</button>
          </div>
        {:else if justSaved}
          <p data-testid="song-edit-saved" aria-live="polite">
            {#if regenerateProgress && regenerateProgress.of > 1}
              Regenerating week {regenerateProgress.week} ({regenerateProgress.done + 1} of {regenerateProgress.of})…
            {:else if laterWeeks.length > 0}
              Saved. Week {trace.week} and the {laterWeeks.length === 1 ? 'week' : `${laterWeeks.length} weeks`}
              after it ({weekList(laterWeeks)}) were made with the old rules: regenerate to hear the change.
            {:else}
              Saved. Week {trace.week} was made with the old rules: regenerate it to hear the change.
            {/if}
          </p>
          <div class="edit-actions">
            <button type="button" class="btn-primary" data-testid="song-edit-regenerate" disabled={regenerating} on:click={regenerateWeek}>
              {regenerating && regenerateProgress?.of === 1 ? 'Regenerating…' : 'Regenerate this week'}
            </button>
            {#if laterWeeks.length > 0}
              <button type="button" data-testid="song-edit-regenerate-later" disabled={regenerating}
                on:click={() => trace && regenerateWeeks([trace.week, ...laterWeeks])}>
                Regenerate this week and {laterWeeks.length === 1 ? 'the next' : `the ${laterWeeks.length} after`}
              </button>
            {/if}
          </div>
        {/if}
        <ErrorBanner message={editState.loadError} severity="polite" prefix="Couldn't load the station's rules" testId="song-edit-load-error" />
        <ErrorBanner message={editState.previewError} severity="polite" prefix="Preview" testId="song-edit-preview-error" />
        <ErrorBanner message={editState.saveError} severity="polite" prefix="Save" testId="song-edit-save-error" />
        {#if editState.failedAtIdx > 0}
          <button type="button" data-testid="song-edit-drop-failed" on:click={() => session?.dropFailed()}>
            Drop that change and save the rest
          </button>
        {/if}
        {#if editState.conflict}
          <p class="conflict" data-testid="song-edit-conflict">
            This station was changed somewhere else since you started.
            <button type="button" on:click={() => session?.load()}>Reload it</button>
            (your changes are kept).
          </p>
        {/if}
        <ErrorBanner message={regenerateError} severity="polite" prefix="Regenerate" testId="song-edit-regenerate-error" />
      </div>
    {/if}

    <!-- One list, so a search survives switching between the week with
         the queued changes (marked against the week as saved) and the
         week as generated, labelled with the rules as saved now (after a
         save, songs made by a since-changed rule say so). -->
    <SongList
      trace={showingEdits && editing?.stagedTrace ? editing.stagedTrace : trace}
      saved={showingEdits && editing ? editing.savedTrace : null}
      template={showingEdits && editing
        ? (editing.staged.template ?? editorData.template)
        : (editing?.template ?? editorData.template)}
      sidecar={showingEdits && editing
        ? (editing.staged.sidecar ?? editorData.sidecar)
        : (editing?.sidecar ?? editorData.sidecar)}
      selected={selectedSong}
      related={selectedRef ? ruleKey(selectedRef) : null}
      onselect={pickSong}
      {ruleDefaults}
    />

    </div>

    {#if editing && panelSelection}
      <DetailPanel
        saveLabel="Apply"
        {ruleDefaults}
        selected={panelSelection}
        template={editing.staged.template ?? editorData.template}
        sidecar={editing.staged.sidecar ?? editorData.sidecar}
        on:close={closePanel}
        on:save={(e) => onPanel({ type: 'save', detail: e.detail })}
        on:retype={(e) => onPanel({ type: 'retype', detail: e.detail })}
        on:macroSave={(e) => onPanel({ type: 'macroSave', detail: e.detail })}
        on:blockMove={(e) => onPanel({ type: 'blockMove', detail: e.detail })}
        on:structuralEdit={(e) => onPanel({ type: 'structuralEdit', detail: e.detail })}
      />
    {/if}
    {:else}
    <p class="hint">Each row below is one unit of the layout (a day for Classics). Click a row header to collapse it.</p>

    <!--
      Filter bar (DESIGN.md §16.8). Three independent inputs that AND
      together (D3); options for source / strategy come from the trace's
      actual content. Counter shows "Showing N of M" with locale-aware
      thousands separator (D10). Clear button only renders when at least
      one filter is active.
    -->
    <!--
      §16.5 c2 — placeholder text is sighted-only and isn't a
      WCAG-valid label; each control gets `aria-label` so screen
      readers announce "Search track or artist", "Filter by source",
      etc instead of the bare role + value.
    -->
    <div class="filters" data-testid="filter-bar">
      <input
        type="text"
        placeholder="Search track or artist…"
        aria-label="Search track or artist"
        data-testid="filter-search"
        autocomplete="off"
        bind:value={searchQuery}
      />
      <select
        data-testid="filter-source"
        aria-label="Filter by source"
        bind:value={sourceFilter}
      >
        <option value="">All sources</option>
        {#each sourceOptions as opt}
          <option value={opt}>{opt}</option>
        {/each}
      </select>
      <select
        data-testid="filter-strategy"
        aria-label="Filter by strategy"
        bind:value={strategyFilter}
      >
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
        <!--
          §16.5 c2 — `aria-expanded` announces the collapse state
          to screen readers (the visible caret is sighted-only);
          `aria-controls` ties the header to the table it toggles
          so AT can offer "go to controlled region" jumps.
        -->
        <!--
          §16.5 c2-review R4 — Space on a fake-button `<header
          role="button">` scrolls the viewport unless the keydown
          handler calls preventDefault(). Pre-c2 this was a
          minor sighted-keyboard nit; post-c2 the new
          `aria-expanded` makes the toggle a load-bearing SR
          interaction, so the scroll jolt would confound the
          announce. Block both Enter + Space defaults.
          c2-review R8 — `aria-controls` references
          `row-${g.row}-table` even when the row is collapsed
          and the table is unmounted. ARIA spec permits the
          dangling IDREF; keeping it preserves the SR
          'jump-to-controlled-region' affordance. Don't drop it
          when collapsed.
        -->
        <header
          on:click={() => toggleRow(g.row)}
          on:keydown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              toggleRow(g.row);
            }
          }}
          role="button"
          tabindex="0"
          aria-expanded={!collapsed[g.row]}
          aria-controls={`row-${g.row}-table`}
        >
          <span class="caret">{collapsed[g.row] ? '▸' : '▾'}</span>
          <span class="unit-label">{g.unit}</span>
          <span class="row-meta">Row {g.row} · {g.slots.length} slots</span>
        </header>

        {#if !collapsed[g.row]}
          <table id={`row-${g.row}-table`}>
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
  {/if}
</main>

<style>
  /* The rule panel is a fixed 320px drawer on the right: while it's open,
     keep the songs and the edit bar (Save) clear of it. */
  @media (min-width: 60rem) {
    .songs-area.with-panel {
      margin-right: 340px;
    }
  }
  .edit-bar {
    position: sticky;
    top: 0;
    z-index: 5;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem 1rem;
    margin: 0 0 0.75rem;
    padding: 0.6rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--panel);
    box-shadow: var(--shadow-sm);
  }
  .edit-bar p {
    margin: 0;
    flex: 1 1 20rem;
  }
  .edit-actions {
    display: flex;
    gap: 0.5rem;
  }
  .edit-bar .muted {
    margin-left: 0.5rem;
    color: var(--muted);
  }
  .edit-bar .conflict {
    flex-basis: 100%;
  }
  /* Under the count and buttons; the bar is sticky, so a long queue
     scrolls within it rather than covering the songs. */
  .edit-bar .queued {
    order: 1;
    flex-basis: 100%;
    max-height: 7.5rem;
    overflow-y: auto;
    margin: 0;
    padding-left: 1.4rem;
    font-size: 0.9em;
  }
  .edit-bar .queued li {
    padding: 0.1rem 0;
  }
  .edit-bar .queued .link {
    margin-left: 0.5rem;
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    text-decoration: underline;
    cursor: pointer;
  }
  .view-switch {
    display: inline-flex;
    margin: 0 0 0.75rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .view-switch button {
    padding: 0.3rem 0.9rem;
    border: 0;
    background: var(--panel);
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }
  .view-switch button:hover:not([aria-pressed='true']) {
    background: var(--panel-3);
  }
  .view-switch button + button {
    border-left: 1px solid var(--border);
  }
  .view-switch button[aria-pressed='true'] {
    background: var(--accent);
    color: var(--accent-text, white);
  }
  main {
    font-family: system-ui, sans-serif;
    max-width: 1100px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .crumbs {
    font-size: 0.85rem;
    margin-bottom: 1rem;
    color: var(--muted);
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
  .week {
    color: var(--text-muted);
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
    color: var(--muted);
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
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--accent);
    text-decoration: none;
    font-variant-numeric: tabular-nums;
  }
  .week-nav-link:hover {
    background: var(--panel-2);
  }
  .week-nav-link.disabled {
    color: var(--text-muted);
    background: var(--panel);
    cursor: not-allowed;
  }

  /* §16.4 trace-version picker. Inline label + select + count
     summary; sits between the week-nav strip and the meta dl. */
  .version-picker {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.4rem 0 1rem;
    font-size: 0.85rem;
  }
  .version-picker label {
    color: var(--muted);
  }
  .version-picker select {
    padding: 0.25rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 3px;
    background: var(--panel);
    font: inherit;
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
  }
  .version-meta {
    color: var(--text-muted);
  }
  /* §16.4.t1 — Compare-versions affordance. Inline link styling
     mirrors the existing "view →" cells in the playlists table on
     the station page. */
  .compare-link {
    margin-left: auto;
    color: var(--accent);
    text-decoration: none;
    font-weight: 500;
  }
  .compare-link:hover {
    text-decoration: underline;
  }
  /* §16.4.t4 — rollback button. Compact + clearly an action (not
     destructive — confirm dialog explains the non-destructive
     semantics). */
  .rollback-btn {
    padding: 0.25rem 0.6rem;
    background: var(--panel);
    border: 1px solid var(--danger);
    color: var(--danger);
    border-radius: 3px;
    cursor: pointer;
    font: inherit;
    font-size: 0.85rem;
  }
  .rollback-btn:hover:not(:disabled) {
    background: var(--panel-2);
  }
  .rollback-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  /* §16.4.t5 — tag editor row. Inline label + ChipMultiInput
     directly below the picker strip. */
  .version-tags {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.5rem 0 1rem;
    font-size: 0.85rem;
  }
  .tags-label {
    color: var(--muted);
    flex-shrink: 0;
  }

  .actions {
    margin: 1rem 0 0.5rem;
    display: flex;
    gap: 0.5rem;
  }
  /* §16.5.t2 — `.disk-status` retired; success styling now lives
     in StatusBanner, error styling in ErrorBanner. */
  /* §16.4.t6 — archive-every-version checkbox. Inline with the
     hint text so the operator sees what filename they'll get
     before they click Export. */
  .archive-toggle {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin: 0 0 1rem;
    font-size: 0.85rem;
    color: var(--muted);
    cursor: pointer;
  }
  .archive-toggle input[type='checkbox'] {
    margin: 0;
    cursor: pointer;
  }
  .archive-toggle .hint-inline {
    color: var(--text-muted);
    font-family: ui-monospace, monospace;
    font-size: 0.8rem;
  }
  h2 {
    margin-top: 2rem;
    margin-bottom: 0.25rem;
  }
  .hint {
    color: var(--muted);
    font-size: 0.85rem;
    margin-top: 0;
  }

  section.row {
    border: 1px solid var(--border);
    border-radius: 4px;
    margin-bottom: 0.75rem;
    overflow: hidden;
    /*
      §16.18 — browser-managed virtualisation. `content-visibility:
      auto` tells the browser to skip layout + paint for off-screen
      row-groups; `contain-intrinsic-size: auto 10000px` is the
      height hint the browser uses to reserve space before the
      group is realized (otherwise scroll-position would jump as
      groups materialise). The `auto` keyword tells the browser to
      LEARN the actual height after first realize + remember it;
      10000px is the FALLBACK seed used before the learn (Classics's
      biggest day is ~430 rows × ~24px ≈ 10300px, so the seed is on
      the same order — under-estimation would cause one paint cycle
      of jiggle on first realize, over-estimation would make the
      scroll bar feel stretched).

      All interactions stay intact: rows remain in the DOM, click /
      filter / collapse / excluded-candidates / chip-flash / jump-
      to-row all work as before. Assistive tech reads the full
      table. The perf win comes from the browser short-circuiting
      layout for groups outside the viewport.

      Browser support: Chrome/Edge 85+, Firefox 125+ (April 2024),
      Safari 18+ (Sept 2024). Older browsers fall back to today's
      no-virtualisation behavior — degraded perf, not broken UX.

      §16.18.t1 (queued): if a future station ships ONE mega-group
      of 10K+ rows, per-row virtualisation would be needed inside
      that single group. Classics's 7 × 430 layout fits this
      approach cleanly.
    */
    content-visibility: auto;
    contain-intrinsic-size: auto 10000px;
  }
  section.row header {
    display: flex;
    align-items: baseline;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem;
    background: var(--panel-2);
    cursor: pointer;
    user-select: none;
  }
  section.row header:hover {
    background: var(--panel-2);
  }
  .caret {
    font-size: 0.8rem;
    color: var(--text-muted);
    width: 1em;
  }
  .unit-label {
    font-weight: 600;
  }
  .row-meta {
    color: var(--text-muted);
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
    border-bottom: 1px solid var(--border);
  }
  th {
    font-weight: 600;
    font-size: 0.8rem;
    color: var(--muted);
    background: var(--panel);
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .muted {
    color: var(--text-muted);
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
    color: var(--danger);
  }

  /* ---- §16.8 filter bar ---------------------------------------------- */
  .filters {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
    margin: 0.5rem 0 1rem;
    padding: 0.5rem 0.75rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 0.9rem;
  }
  .filters input[type='text'] {
    flex: 1 1 14rem;
    min-width: 12rem;
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 3px;
    font: inherit;
  }
  .filters select {
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 3px;
    font: inherit;
    background: var(--panel);
  }
  .filters .counter {
    margin-left: auto;
    color: var(--muted);
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
  }
  .clear-btn {
    padding: 0.3rem 0.7rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
    font: inherit;
    font-size: 0.85rem;
  }
  .clear-btn:hover {
    background: var(--panel-2);
  }

  .empty-filter {
    margin: 1rem 0;
    padding: 1.25rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    text-align: center;
    color: var(--muted);
  }
  .empty-filter .clear-btn {
    margin-left: 0.5rem;
  }

  /* ---- §14.23 excluded-candidates disclosure ------------------------- */
  .excluded-toggle {
    margin-left: 0.5rem;
    padding: 0 0.35rem;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    font: inherit;
    font-size: 0.75rem;
    cursor: pointer;
    vertical-align: baseline;
  }
  .excluded-toggle:hover {
    background: var(--panel-2);
    color: var(--muted);
  }
  .excluded-row > td {
    /* Strip the default row border so the sub-table reads as a
       continuation of the slot above, not a separate row. */
    border-bottom: none;
    background: var(--panel);
    padding: 0.4rem 0.5rem 0.6rem;
  }
  .excluded-table {
    font-size: 0.82rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 3px;
  }
  .excluded-table th {
    background: var(--panel-2);
    font-size: 0.72rem;
  }
  .excluded-table td,
  .excluded-table th {
    padding: 0.2rem 0.55rem;
  }
</style>
