<script lang="ts">
  /**
   * §16.2.2 c2 — visualise route scaffold.
   *
   * Fetches GET /api/stations/{slug}/sidecar and renders a minimal
   * placeholder. The real canvas (Section / ChipFlow / Chip / etc.)
   * lands in c3. This file's job: prove the load path + types are
   * wired before c3 starts composing components.
   *
   * Three render states:
   *   - loading       → "Loading…"
   *   - error (404)   → station-not-found message + back link
   *   - loaded        → counts summary + sidecar_error warning (if any)
   *
   * Discoverability link from /stations/[slug] lands in c4 (per the
   * design's "first-impression" treatment); c2 is reachable only by
   * direct URL or test.
   */
  import { onDestroy, onMount, tick } from 'svelte';
  import { page } from '$app/stores';
  import {
    ApiError,
    applyBatch,
    BlockPatchConflict,
    cancelJob,
    createSection,
    deleteSection,
    exportStationTemplate,
    getStationSidecar,
    isPreviewResult,
    moveBlock,
    type Trace,
    patchBlock,
    patchMacro,
    renameSection,
    reorderSections,
    retypeBlock,
    SidecarBatchFailure,
    startPreviewJob,
    structuralBlockEdit,
    watchJob,
    type BlockAddress,
    type BlockPatchResponse,
    type BlockRetypeResponse,
    type MacroPatchResponse,
    type SidecarMutationResponse,
    type StructuralBlockEdit,
  } from '$lib/api';

  // §16.2.5.t11 — `withSaveLifecycle`'s `doPatch` callback may resolve
  // to any of the four mutation-endpoint response shapes. Previously
  // typed as `Promise<unknown>`; tightening to this union lets
  // `_isSidecarMutationResponse` narrow at the type level (it's a true
  // discriminator — only SidecarMutationResponse carries `sidecar`).
  // The runtime guard remains load-bearing because some §16.2.3 /
  // §16.2.4 routes fall through to the GET-refetch branch.
  type SaveResponse =
    | SidecarMutationResponse
    | BlockPatchResponse
    | BlockRetypeResponse
    | MacroPatchResponse;
  import {
    classifySlots,
    findChipRef,
    findSectionIdForRef,
    parseEditorSidecarResponse,
    refKey,
    summarizeClassifications,
    type ExpandedRef, type Sidecar, type StationTemplate,
  } from '$lib/editor';
  import { applyEdits, attributeOpsToSlot, type Edit } from '$lib/edit-ops';
  import EditorCanvas from '$lib/components/editor/EditorCanvas.svelte';
  import MacroPalette from '$lib/components/editor/MacroPalette.svelte';
  import TracePane from '$lib/components/editor/TracePane.svelte';
  import NarrowScreenNotice from '$lib/components/NarrowScreenNotice.svelte';

  let template: StationTemplate | null = null;
  let sidecar: Sidecar | null = null;
  let sidecarError: string | null = null;
  let loadError: string | null = null;
  let loading = true;

  // §16.2.3 c2 — optimistic-concurrency token + edit/export status.
  let updatedAt: string | null = null;
  let saveError: string | null = null;
  let conflict = false;
  let exportMsg: string | null = null;

  // §16.2.6 c2 — live-preview state. Toggle defaults OFF; persists
  // per-station to localStorage. When ON: every edit's
  // `withSaveLifecycle` success schedules a debounced preview job
  // that watches via §16.11's job-id transport; the resulting trace
  // renders in <TracePane>. The toggle gates VISIBILITY of the
  // preview pane + the scheduler — toggle OFF is identical to
  // §16.2.5 behavior (no scheduler, no TracePane, no new event
  // listeners). c3 adds the staging buffer (opLog primary +
  // stagedSidecar derived), Save/Discard, partial-failure modal,
  // and the DetailPanel ↔ TracePane mutual-exclusivity (D2(b)).
  // c2 ships the scheduler skeleton + visualisation only.
  let livePreviewOn = false;
  // §16.2.4.t1 — macro palette drawer state. Persists per-station to
  // localStorage so a returning operator picks up where they left off.
  // Independent of livePreviewOn — the palette is a browse affordance,
  // not tied to preview. When OPEN, the right-side column renders even
  // if livePreviewOn is false (palette occupies it solo).
  let paletteOpen = false;
  let previewTrace: Trace | null = null;
  let previewing = false;
  let previewError: string | null = null;
  // §16.2.6.t8 — operator-selected preview week. Default 1 mirrors
  // the c2 hardcoded value; hydration from localStorage in setSlug
  // overrides if a prior value was stored for this station. The
  // backend's PreviewRequest.week strict-typed pin (§16.2.6.t5) is
  // the authoritative bound; client-side min=1 in TracePane is a
  // hint only.
  let previewWeek = 1;

  // §16.2.7 c1 — compare-against-saved diff state. `showDiff` is the
  // operator's opt-in toggle (D1); `savedTrace` caches the SAVED-state
  // preview so we don't re-fire it per edit (D2 — one preview per Save
  // cycle, not per edit). `savedTraceError` surfaces a one-line
  // "Diff unavailable: ..." in TracePane (D6) without blocking the
  // staged-trace flow.
  //
  // Cache invalidations (per D2's "(c) chosen" path):
  //   - slug change       → setSlug clears
  //   - previewWeek change → handleTraceWeekChange clears
  //   - Save success      → handleStagedSave clears post-_finish
  //   - showDiff OFF      → toggle handler clears
  let showDiff = false;
  let savedTrace: Trace | null = null;
  let savedTraceError: string | null = null;
  // §16.2.7.t1 review F2 fix — savedTracePending removed. The pending
  // guard (`if (savedTracePending) return;`) raced with generation
  // bumps from handleStagedSave + handleWeekChange — a new refresh
  // triggered by Save/week change while an OLD invalidated refresh was
  // still in flight would short-circuit, leaving the cache empty
  // forever. The generation gate + server-side POLICY_CANCEL_EXISTING
  // together make concurrent refreshes safe (old job gets cancelled,
  // new job's done lands), so the guard wasn't load-bearing.
  // §16.2.7 c1 review R1 — generation counter for the savedTrace
  // refresh. Mirrors `previewGeneration` but kept SEPARATE so that
  // _schedulePreview's bumps (which cancel the staged side) don't
  // also invalidate an in-flight saved-trace fetch. Bumped at every
  // savedTrace invalidation point (setSlug / handleWeekChange /
  // handleStagedSave success / handleShowDiffChange OFF). The async
  // fetch captures the current generation at start + checks before
  // every state assignment — late results from a previous context
  // (different slug / week / pre-Save baseline) get discarded
  // instead of polluting the cleared cache.
  let savedTraceGeneration = 0;

  // §16.2.7.t4 — multi-week diff aggregator state. `compareNextN`
  // is the number of ADDITIONAL weeks beyond `previewWeek` the
  // operator wants to aggregate (D1: 0-5, default 0 = single-week
  // mode = current behavior). `weekDiffSummary` caches the
  // per-week aggregate counts (D3); populated by
  // `_refreshMultiWeekDiff` firing N saved+staged preview pairs
  // serially (D4). `multiWeekFetching` is a boolean for the
  // "Computing N-week diff…" UI placeholder.
  //
  // Cache invalidations (D3):
  //   - slug change       → setSlug clears
  //   - previewWeek change → handleWeekChange clears + refires
  //   - compareNextN change → setter clears + refires
  //   - Save success      → handleStagedSave clears + refires
  //   - showDiff OFF      → _applyShowDiffChange clears
  //
  // Generation gate (mirror of savedTraceGeneration) — separate
  // counter so saved-trace invalidations don't cross-invalidate
  // multi-week + vice versa.
  type WeekSummary =
    | { changed: number; added: number; removed: number }
    | { error: string };
  let compareNextN = 0;
  let weekDiffSummary: Map<number, WeekSummary> = new Map();
  let multiWeekFetching = false;
  let multiWeekGeneration = 0;

  // §16.2.6.t3 — click-trace-row → jump-to-chip state. `_jumpFlashEl`
  // tracks the element currently wearing the .chip--jump-flash class
  // so we can strip it on a rapid second click (D4 race semantics —
  // the second click moves the flash to the new chip; the old chip
  // doesn't leak the class). `_jumpFlashTimer` is the 800ms strip
  // timer; rapid clicks cancel + reschedule it.
  let _jumpFlashEl: HTMLElement | null = null;
  let _jumpFlashTimer: ReturnType<typeof setTimeout> | null = null;
  const JUMP_FLASH_MS = 800;
  // Debounce + cancel tracking. The 500ms idle window matches §16.2.0
  // D4's debounce target. `currentPreviewJobId` is the latest spawned
  // job — stored so onDestroy + a new debounce-fire can cancel it
  // (POLICY_CANCEL_EXISTING on the server also supersedes, but the
  // client cancel keeps the polling loop from hanging on a stale id).
  let previewDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  let currentPreviewJobId: string | null = null;
  // Token incremented per scheduled preview so an in-flight watchJob
  // loop can self-cancel when superseded. The watchJob async iterator
  // checks its captured token against this on every yield + bails if
  // they diverge.
  let previewGeneration = 0;
  const PREVIEW_DEBOUNCE_MS = 500;

  // §16.2.6 c3 — staging buffer. opLog is the AUTHORITATIVE store;
  // `stagedTemplate` + `stagedSidecar` are derived. Save replays
  // opLog as sequential PATCHes; Discard clears it.
  //
  // **Intercepted edit kinds (toggle ON queues to opLog):**
  //   - `patchBlock` (c3 — chip-edit DetailPanel Save)
  //   - `patchMacro` (c3b — macro-edit DetailPanel Save)
  //   - `renameSection` (c3b — inline section rename)
  //   - `sectionCreate` (c3c — Add Section input)
  //   - `sectionDelete` (c3c — SectionHeader Delete button)
  //   - `reorderSections` (c3c — section-header drag-reorder)
  //   - `moveBlock` (c3d — chip drag between sections)
  //   - `retypeBlock` (c3d — DetailPanel retype panel)
  //   - `structuralBlockEdit` (c3d-follow-up — size / add / remove
  //     with slot_index shifts; closes the 9th + final kind)
  //
  // All 9 staging kinds are now intercepted. No fall-through paths
  // remain when the toggle is ON.
  //
  // **Intercept-pattern extraction landed in c3c** as the
  // `_interceptToOpLog(op)` helper just above handleSave — c3b
  // review #1 finding #8. Every handler uses it.
  //
  // saveError surfacing during Save replay reuses the existing
  // `saveError` banner — the Retry/Drop modal lands in c3d /
  // polish. On any replay error the loop stops + the operator
  // sees which edit failed; remaining edits stay in opLog so a
  // fix-and-retry sequence works.
  let opLog: Edit[] = [];
  let saving = false;

  // §16.2.7.t6 — derived set of slot indices that have at least one
  // DIRECT op in the opLog. Used by TracePane to enable/disable the
  // per-row Revert button. Cheap: O(opLog), recomputed reactively
  // whenever opLog changes. Slots NOT in this set are cascade-only
  // (an earlier structural edit shifted them but no op directly
  // targets them); revert affordance is disabled for them with a
  // tooltip per D1.
  $: revertableSlots = (() => {
    const out = new Set<number>();
    for (const op of opLog) {
      if (
        (op.kind === 'patchBlock' || op.kind === 'retypeBlock' ||
         op.kind === 'moveBlock') &&
        op.address.slot_index !== undefined
      ) {
        out.add(op.address.slot_index);
      } else if (op.kind === 'structuralBlockEdit') {
        out.add(op.edit.slot_index);
      }
    }
    return out;
  })();

  // Derived: when toggle ON + opLog non-empty, stagedTemplate +
  // stagedSidecar reflect (base + applyEdits). Otherwise they're
  // the same references as the loaded template + sidecar. The
  // preview endpoint receives whichever is current.
  //
  // Review #1 fix #8: ternary instead of IIFE. The IIFE worked
  // (Svelte's reactive dependency tracker handles the nested
  // function), but read as defensive-against-Svelte; the ternary
  // makes the data flow obvious + drops a function literal.
  $: stagedView = livePreviewOn && opLog.length > 0 && template && sidecar
    ? applyEdits(template, sidecar as unknown, opLog) as { template: StationTemplate; sidecar: Sidecar }
    : { template, sidecar };
  $: stagedTemplate = stagedView.template;
  $: stagedSidecar = stagedView.sidecar;

  // §16.2.6 c3e c2 — D2(b) DetailPanel ↔ TracePane mutual-
  // exclusivity. EditorCanvas dispatches `detailOpen: { open }` on
  // every selection change; the page mirrors it locally so the
  // template can conditionally render TracePane. When both want
  // the right-side real estate, DetailPanel wins (selection is the
  // operator's explicit intent; the trace is ambient).
  let detailPanelOpen = false;

  // §16.2.6 c3e c3 — Retry/Drop partial-failure modal. When
  // `handleStagedSave` stops mid-replay on a non-conflict error,
  // the operator gets a 3-option modal instead of just a banner:
  //
  //   - **Retry the failed edit** — re-run replayOpLog from the
  //     head (the failed op stayed at opLog[0] per replay
  //     semantics). Right move when the failure was transient
  //     (network blip, race on updated_at, etc.).
  //   - **Drop the failed edit and continue** — pop opLog[0], then
  //     re-run replayOpLog on the rest. Right move when the
  //     failure is permanent (bad input, server rejected the
  //     operation outright) and the operator wants the OTHER
  //     staged edits to land.
  //   - **Cancel** — close the modal; saveError banner stays. The
  //     operator can examine the failed op, fix it (maybe re-open
  //     the chip in DetailPanel + re-stage), then click Save again.
  //
  // The conflict path (`conflict = true`) gets its own banner
  // (with Reload latest) — the modal is for non-conflict errors
  // only (validation, server reject, etc.).
  let showRetryModal = false;

  // §16.2.6 c3e — "operator has unsaved staged edits" predicate.
  // Reactive to `opLog`, NOT to `livePreviewOn`. The reactive-to-opLog-
  // only contract matters for two reasons:
  //
  //   1. The toggle-off path reads this BEFORE clearing `opLog` via
  //      `_resetStagedState()`, so the prompt actually fires on the
  //      "still has staged edits" snapshot.
  //   2. The beforeunload listener fires the warning ON RESIDUAL
  //      `opLog` content even if some future code path leaves it
  //      non-empty after a toggle OFF (c3e c1 routes through
  //      `_resetStagedState` which clears it, but the predicate
  //      shouldn't trust callers to do that — it's a safety net).
  $: hasStagedEdits = opLog.length > 0;

  $: slug = $page.params.slug ?? '';

  // §16.2.6 c2 review #1 fix #10: re-hydrate the toggle when slug
  // changes. SvelteKit may re-use the same route component across
  // station navigations (without firing a fresh onMount), so the
  // initial-mount hydration alone leaves the toggle stuck on the
  // previous station's value. This reactive re-reads localStorage
  // for the CURRENT slug whenever slug changes; pair with a
  // _cancelPreview to drop any in-flight preview from the old
  // station (its template/sidecar are stale for the new slug).
  let _lastHydratedSlug: string | null = null;
  $: if (slug && slug !== _lastHydratedSlug) {
    _cancelPreview();
    previewTrace = null;
    previewError = null;
    livePreviewOn = readLivePreview(slug);
    // §16.2.4.t1 — hydrate macro-palette open-state per-station.
    paletteOpen = readMacroPalette(slug);
    // §16.2.6.t8: hydrate previewWeek per-station too. Default 1
    // when no entry exists (mirrors c2's hardcoded behavior).
    previewWeek = readPreviewWeek(slug);
    // §16.2.7 c1: hydrate showDiff per-station. Default OFF. Slug
    // change ALSO clears the savedTrace cache (D2 — different
    // station means a different saved-state baseline). Bump the
    // generation so any in-flight _refreshSavedTraceIfNeeded from
    // the previous slug discards its result instead of assigning
    // it to the new station's cache (review R1).
    showDiff = readShowDiff(slug);
    savedTrace = null;
    savedTraceError = null;
    savedTraceGeneration += 1;
    // §16.2.7.t4: hydrate compareNextN + clear the multi-week cache.
    // Bump multiWeekGeneration so any in-flight _refreshMultiWeekDiff
    // from the previous slug discards its result.
    compareNextN = readCompareNextN(slug);
    weekDiffSummary = new Map();
    multiWeekFetching = false;
    multiWeekGeneration += 1;
    _lastHydratedSlug = slug;
  }

  // §16.2.6 c2 — per-station localStorage key. The toggle's intent
  // ("I'm exploring this station, show me previews") is most useful
  // when scoped to the station; switching to a different station
  // resets to the default OFF. Cross-station shared toggle was
  // considered but deferred — operators typically edit one station
  // at a time + the per-station shape mirrors viewMode persistence.
  function _livePreviewKey(s: string): string {
    return `livePreview:${s}`;
  }

  function readLivePreview(s: string): boolean {
    if (typeof window === 'undefined') return false;
    try {
      return window.localStorage.getItem(_livePreviewKey(s)) === 'on';
    } catch {
      return false;
    }
  }

  function writeLivePreview(s: string, on: boolean): void {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(_livePreviewKey(s), on ? 'on' : 'off');
    } catch {
      // localStorage quota / privacy-mode block — non-fatal, toggle
      // state just doesn't persist across page reloads in those
      // sessions.
    }
  }

  // §16.2.4.t1 — per-station macro-palette-open localStorage. Mirrors
  // the livePreview key shape so an operator's per-station UI mode
  // persists across reloads. Failure-tolerant for the same reasons as
  // readLivePreview / writeLivePreview.
  function _macroPaletteKey(s: string): string {
    return `macroPalette:${s}`;
  }

  function readMacroPalette(s: string): boolean {
    if (typeof window === 'undefined') return false;
    try {
      return window.localStorage.getItem(_macroPaletteKey(s)) === 'on';
    } catch {
      return false;
    }
  }

  function writeMacroPalette(s: string, on: boolean): void {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(_macroPaletteKey(s), on ? 'on' : 'off');
    } catch {
      // localStorage quota / privacy-mode block — non-fatal.
    }
  }

  // §16.2.6.t8 — per-station preview week localStorage. Same shape +
  // failure handling as livePreview. Read defaults to 1 on absent /
  // unparseable / out-of-range stored value (D2: client-side fallback;
  // backend's strict-typed PreviewRequest.week is the authoritative
  // bound). NaN / negative / non-integer stored values reset to 1.
  function _previewWeekKey(s: string): string {
    return `previewWeek:${s}`;
  }

  function readPreviewWeek(s: string): number {
    if (typeof window === 'undefined') return 1;
    try {
      const raw = window.localStorage.getItem(_previewWeekKey(s));
      if (raw === null) return 1;
      const n = Number(raw);
      if (!Number.isFinite(n) || n < 1) return 1;
      return Math.floor(n);
    } catch {
      return 1;
    }
  }

  function writePreviewWeek(s: string, week: number): void {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(_previewWeekKey(s), String(week));
    } catch {
      // localStorage quota / privacy-mode block — non-fatal, week
      // just doesn't persist across page reloads in those sessions.
    }
  }

  // §16.2.7 c1 — `showDiff` per-station persistence. Same shape as
  // _previewWeekKey / _livePreviewKey above (mirrors §16.2.6.t8 + c2
  // localStorage conventions). Stored as "on"/"off" string; any other
  // value reads as false (defensive against stale shapes).
  function _showDiffKey(s: string): string {
    return `showDiff:${s}`;
  }

  function readShowDiff(s: string): boolean {
    if (typeof window === 'undefined') return false;
    try {
      return window.localStorage.getItem(_showDiffKey(s)) === 'on';
    } catch {
      return false;
    }
  }

  function writeShowDiff(s: string, on: boolean): void {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(_showDiffKey(s), on ? 'on' : 'off');
    } catch {
      // Same quota/privacy fallback as the helpers above.
    }
  }

  // §16.2.7.t4 — `compareNextN` per-station persistence. Stored as
  // the decimal string of the number. Defensive parse (Number.isFinite
  // + clamp 0-COMPARE_NEXT_N_MAX) so a stale shape or hand-edited
  // value can't crash the page. Mirrors readPreviewWeek + readShowDiff.
  const COMPARE_NEXT_N_MAX = 5;
  function _compareNextNKey(s: string): string {
    return `compareNextN:${s}`;
  }
  function readCompareNextN(s: string): number {
    if (typeof window === 'undefined') return 0;
    try {
      const raw = window.localStorage.getItem(_compareNextNKey(s));
      if (raw === null) return 0;
      const n = Number(raw);
      if (!Number.isFinite(n) || n < 0) return 0;
      return Math.min(COMPARE_NEXT_N_MAX, Math.floor(n));
    } catch {
      return 0;
    }
  }
  function writeCompareNextN(s: string, n: number): void {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(_compareNextNKey(s), String(n));
    } catch {
      // Same quota/privacy fallback.
    }
  }

  // §16.2.6.t1 — staged-edit persistence + recovery. localStorage
  // key + helpers. Design summary (DESIGN §16.2.6.t1 design pass):
  //
  //   - **Storage**: localStorage per-station — `stagedEdits:<slug>`.
  //     Single-operator hobbyist scale; the realistic opLog upper
  //     bound (~50 ops × few hundred bytes) fits well under
  //     localStorage's ~5MB quota. IndexedDB's async API overkill
  //     for the scope.
  //   - **Shape**: `{ opLog, updatedAtSnapshot }` — the snapshot is
  //     the `updated_at` token at stage-time. Reused on recovery to
  //     detect "the station changed under me" drift.
  //   - **Multi-tab**: cross-tab live sync SHIPPED in the
  //     §16.2.6.t1-follow-up (`45894b8`). A `storage` event listener
  //     (registered in onMount, removed in onDestroy) mirrors
  //     `stagedEdits:<slug>` writes between tabs; last-write-wins,
  //     but the loser SEES the change rather than shadowing it
  //     with stale state until reload. §16.2.7.t3 extended the
  //     same listener to also sync the `showDiff:<slug>` toggle.
  //   - **Recovery UX**: silent if `updatedAtSnapshot === updatedAt`
  //     (the staged edits apply against the same base they were
  //     authored against). Otherwise window.confirm: cancel to
  //     recover anyway, confirm to discard. Mirrors the toggle-off
  //     prompt's interaction shape.
  /** §16.2.6.t1 review L3 — bump on any breaking change to the
   *  persisted Edit shape (added required fields on an existing
   *  kind, etc.). Entries with a mismatched version drop silently
   *  in `_readStagedEdits`, so an evolved frontend doesn't try to
   *  replay an op whose semantics have shifted under it. */
  const STAGED_EDITS_SCHEMA_VERSION = 1;

  /** §16.2.6.t1 review L2 — known Edit kinds for recovery
   *  filtering. If the persisted opLog has an entry whose `kind`
   *  isn't in this set (a future build dropped the kind, the
   *  persisted entry was hand-edited, etc.), it gets dropped
   *  silently to avoid crashing `replayOpLog`'s exhaustiveness pin.
   *  Keep in lockstep with the `Edit` union in `$lib/edit-ops`. */
  const KNOWN_EDIT_KINDS: ReadonlySet<string> = new Set([
    'patchBlock',
    'patchMacro',
    'renameSection',
    'sectionCreate',
    'sectionDelete',
    'reorderSections',
    'moveBlock',
    'retypeBlock',
    'structuralBlockEdit',
  ]);

  interface PersistedStagedEdits {
    schemaVersion: number;
    opLog: Edit[];
    updatedAtSnapshot: string;
  }
  function _stagedEditsKey(s: string): string {
    return `stagedEdits:${s}`;
  }
  function _readStagedEdits(s: string): PersistedStagedEdits | null {
    if (typeof window === 'undefined') return null;
    try {
      const raw = window.localStorage.getItem(_stagedEditsKey(s));
      if (raw === null) return null;
      const parsed = JSON.parse(raw) as PersistedStagedEdits;
      // Defensive: minimum shape sanity. A localStorage entry from a
      // pre-t1 build, a schema-version mismatch, or a corrupted entry
      // is dropped silently.
      if (
        !parsed ||
        typeof parsed !== 'object' ||
        parsed.schemaVersion !== STAGED_EDITS_SCHEMA_VERSION ||
        !Array.isArray(parsed.opLog) ||
        typeof parsed.updatedAtSnapshot !== 'string'
      ) {
        return null;
      }
      // L2 filter: drop entries whose `kind` isn't in the current
      // known set. Today the Edit union only grows, but a future
      // schema-version bump that removes a kind would crash
      // replayOpLog without this guard.
      const filtered = parsed.opLog.filter(
        (op) =>
          op !== null &&
          typeof op === 'object' &&
          KNOWN_EDIT_KINDS.has((op as { kind?: unknown }).kind as string),
      );
      return { ...parsed, opLog: filtered };
    } catch {
      return null;
    }
  }
  function _writeStagedEdits(s: string, payload: PersistedStagedEdits | null): void {
    if (typeof window === 'undefined') return;
    try {
      const key = _stagedEditsKey(s);
      if (payload === null || payload.opLog.length === 0) {
        window.localStorage.removeItem(key);
      } else {
        window.localStorage.setItem(key, JSON.stringify(payload));
      }
    } catch {
      // localStorage quota / privacy-mode block — non-fatal. Worst
      // case: staged edits stay in-memory only for this session.
    }
  }

  /**
   * §16.2.6.t1 — write the current opLog + updatedAt token to
   * localStorage. Called at every opLog mutation site so persistence
   * stays in lockstep with in-memory state.
   *
   * L6: keep in lockstep with the 4 opLog mutation sites:
   *   1. `_interceptToOpLog` — append on a staging-mode handler.
   *   2. `handleStagedSave` — drain on replay (success = empty,
   *      failure = `result.remaining` keeps the failed head).
   *   3. `handleDrop` — pop opLog[0] after the Retry/Drop modal's
   *      Drop action.
   *   4. `_resetStagedState` — clear on Discard / toggle-off-with-
   *      confirm. Also routes through here on the loadSidecar 404
   *      orphan-cleanup path (L1).
   * A 5th mutation site should add a `_persistOpLog()` call OR
   * route through one of the above helpers.
   */
  function _persistOpLog(): void {
    _writeStagedEdits(
      slug,
      opLog.length > 0 && updatedAt !== null
        ? { schemaVersion: STAGED_EDITS_SCHEMA_VERSION, opLog, updatedAtSnapshot: updatedAt }
        : null,
    );
  }

  // §16.2.6 c2 review #1 fix #8: operator-facing error sanitiser.
  // Backend's clean 400 messages ("invalid sidecar (grammar):
  // missing required keys [layout_order]") are operator-readable +
  // pass through. Internal exception strings (Python tracebacks,
  // module-qualified class names, KeyError quotes) should NOT be
  // exposed in the trace pane banner — they're confusing + leak
  // internals. Heuristic: cap length at 200 chars + strip the
  // common Python-y prefixes that escape from job_runner's error
  // handler.
  function _friendlyPreviewError(raw: string | undefined): string {
    if (!raw) return 'preview failed';
    let msg = raw;
    // Strip module-qualified class prefix: "engine.editor_metadata.EditorMetadataError: foo" → "foo"
    msg = msg.replace(/^[a-zA-Z_][\w.]*Error:\s*/, '');
    // Strip leading "Traceback (most recent call last):" + the rest.
    if (msg.startsWith('Traceback')) return 'preview failed (internal error)';
    // Cap length. Long traceback fragments shouldn't dominate the
    // banner; 200 chars holds the typical backend 400 message + room.
    if (msg.length > 200) msg = msg.slice(0, 197) + '…';
    return msg;
  }

  // §16.2.6.t3 — click-trace-row → jump-to-chip handler.
  // TracePane dispatches `select { slotIndex }` on row click / Enter / Space.
  // This handler resolves the row to a chip ref via findChipRef, locates
  // the chip in the DOM via its data-ref-key, scrolls it into view, and
  // flashes the chip's outline. D5: does NOT auto-open DetailPanel —
  // a trace-click is "where is this slot?", not "edit this slot." The
  // operator clicks the chip themselves to edit.
  async function handleTraceSelect(e: CustomEvent<{ slotIndex: number }>) {
    if (!previewTrace || !stagedTemplate || !stagedSidecar) return;
    // The TracePane dispatches whichever slot index appears in the
    // row; for row_template stations this is the globally-flattened
    // number, not slot.row + slot.block. We need the full TraceSlot
    // to feed findChipRef the layout-form-aware mapping.
    const traceSlot = previewTrace.slots.find(
      (s) => s.slot === e.detail.slotIndex,
    );
    if (!traceSlot) {
      // Trace state changed under us (Discard / new preview) between
      // the click handler firing + this handler running. Defensive
      // no-op; not worth a warn since it's expected on rapid edits.
      return;
    }
    // §16.2.7 c2 review R1 — look up the chip in STAGED state, not
    // saved. Pre-fix used `template, sidecar` (saved), which missed
    // staged-only additions (structural-add chips that exist in the
    // canvas DOM rendered from stagedSidecar but not in saved).
    // Diff-mode ADDED rows are the most visible surface for this;
    // pre-existing latent for any staged edit. The canvas renders
    // stagedSidecar; findChipRef must use the same state pair.
    const ref = findChipRef(stagedTemplate, stagedSidecar, traceSlot);
    if (ref === null) {
      // D6 — out-of-canvas / drift / orphan-macro case. Silent no-op
      // for the operator; warn so a real drift bug surfaces in dev.
      // eslint-disable-next-line no-console
      console.warn(
        `§16.2.6.t3 jump: no chip matches trace slot ${e.detail.slotIndex} ` +
          `(row=${traceSlot.row} block=${traceSlot.block}).`,
      );
      return;
    }

    const key = refKey(ref);
    let chipEl = document.querySelector<HTMLElement>(
      `[data-ref-key="${CSS.escape(key)}"]`,
    );

    if (chipEl === null) {
      // Chip not in the DOM. The expected cause is that its
      // containing section is COLLAPSED (§16.2.2 large-station
      // auto-collapse). Find the section + synthetic-click its
      // toggle (the documented user affordance for expand/collapse),
      // await tick() so Svelte renders the expanded section, then
      // re-query.
      //
      // §16.2.6.t3 slice-review R3: but "chip not in DOM" can ALSO
      // mean "section is already expanded + chip is hidden for an
      // unrelated reason" (a future view-mode filter, a hot-reload
      // race, etc.). Synthetic-clicking the toggle in that case
      // would COLLAPSE the section the chip lives in — making the
      // chip even more inaccessible + leaving the operator with a
      // closed section they had open. Guard by checking whether
      // the section's body element is already rendered: if it is,
      // the section IS expanded (toggle would collapse it) → bail
      // instead of toggling.
      // R1 follow-through: same state pair for the section lookup.
      // The chip element rendered in the DOM is in the staged sidecar.
      const sectionId = findSectionIdForRef(stagedSidecar, ref);
      if (sectionId !== null) {
        const sectionEl = document.querySelector<HTMLElement>(
          `[data-section-id="${CSS.escape(sectionId)}"]`,
        );
        if (sectionEl !== null) {
          const alreadyExpanded = sectionEl.querySelector(
            '[data-testid="section-body"]',
          ) !== null;
          if (!alreadyExpanded) {
            const toggleEl = sectionEl.querySelector<HTMLButtonElement>(
              '[data-testid="section-toggle"]',
            );
            if (toggleEl !== null) {
              toggleEl.click();
              await tick();
              chipEl = document.querySelector<HTMLElement>(
                `[data-ref-key="${CSS.escape(key)}"]`,
              );
            }
          }
          // alreadyExpanded === true → don't toggle. chipEl stays
          // null → falls through to the bail below.
        }
      }
    }

    if (chipEl === null) return; // expansion didn't surface the chip; bail.

    chipEl.scrollIntoView({
      behavior: 'smooth',
      block: 'nearest',
      inline: 'nearest',
    });

    // D4 race semantics: a second trace-row click during the flash
    // window strips the class from the prior chip + moves it to the
    // new one, then restarts the timer. The cleared timer + el track
    // also matter for onDestroy hygiene (no dangling timer firing
    // after the route navigates away).
    if (_jumpFlashEl !== null && _jumpFlashEl !== chipEl) {
      _jumpFlashEl.classList.remove('chip--jump-flash');
    }
    if (_jumpFlashTimer !== null) {
      clearTimeout(_jumpFlashTimer);
    }
    chipEl.classList.add('chip--jump-flash');
    _jumpFlashEl = chipEl;
    _jumpFlashTimer = setTimeout(() => {
      if (_jumpFlashEl !== null) {
        _jumpFlashEl.classList.remove('chip--jump-flash');
        _jumpFlashEl = null;
      }
      _jumpFlashTimer = null;
    }, JUMP_FLASH_MS);
  }

  // §16.2.4.t1 — toggle the macro palette drawer + persist.
  function toggleMacroPalette() {
    paletteOpen = !paletteOpen;
    writeMacroPalette(slug, paletteOpen);
  }

  // §16.2.4.t1 — handle MacroPalette's `select { macroId }` event.
  // Find the FIRST occurrence of that macro on the canvas
  // (pick_in_macro=1 — guaranteed unique within the sidecar by
  // validate_sidecar's drift rules), then run the same scroll +
  // expand-section + flash pipeline as handleTraceSelect. Edit-from-
  // sidebar is deliberately NOT wired (locked at design pass) — the
  // operator clicks the chip on the canvas to edit, same as the
  // existing macro-edit flow. A future slice can add an explicit
  // 'edit' affordance here when operators report the extra hop.
  async function handleMacroPaletteSelect(
    e: CustomEvent<{ macroId: string }>,
  ) {
    const macroId = e.detail.macroId;
    // The macro chip's data-ref-key is `macro:<id>:pick=1` for the
    // first repeat instance. refKey owns the serialization contract;
    // mirror it here rather than importing a one-off helper.
    const key = `macro:${macroId}:pick=1`;
    let chipEl = document.querySelector<HTMLElement>(
      `[data-ref-key="${CSS.escape(key)}"]`,
    );

    if (chipEl === null) {
      // Section containing the macro may be collapsed. Find it by
      // scanning the sidecar for the first section that references
      // the macro, then synthetic-click its toggle (same pattern as
      // handleTraceSelect's collapsed-section recovery). Use staged
      // sidecar when present so the chip lookup matches what's
      // actually rendered.
      const sc = stagedSidecar ?? sidecar;
      if (sc === null) return;
      const containingSection = sc.sections.find((s) =>
        s.blocks.some((b) => b.kind === 'macro' && b.ref === macroId),
      );
      if (containingSection === undefined) return;
      const sectionEl = document.querySelector<HTMLElement>(
        `[data-section-id="${CSS.escape(containingSection.id)}"]`,
      );
      if (sectionEl !== null) {
        const alreadyExpanded = sectionEl.querySelector(
          '[data-testid="section-body"]',
        ) !== null;
        if (!alreadyExpanded) {
          const toggleEl = sectionEl.querySelector<HTMLButtonElement>(
            '[data-testid="section-toggle"]',
          );
          if (toggleEl !== null) {
            toggleEl.click();
            await tick();
            chipEl = document.querySelector<HTMLElement>(
              `[data-ref-key="${CSS.escape(key)}"]`,
            );
          }
        }
      }
    }

    if (chipEl === null) return;

    chipEl.scrollIntoView({
      behavior: 'smooth',
      block: 'nearest',
      inline: 'nearest',
    });
    // Reuse the shared flash-class lifecycle (same race semantics as
    // handleTraceSelect — second click moves the flash; onDestroy
    // tears down the timer).
    if (_jumpFlashEl !== null && _jumpFlashEl !== chipEl) {
      _jumpFlashEl.classList.remove('chip--jump-flash');
    }
    if (_jumpFlashTimer !== null) {
      clearTimeout(_jumpFlashTimer);
    }
    chipEl.classList.add('chip--jump-flash');
    _jumpFlashEl = chipEl;
    _jumpFlashTimer = setTimeout(() => {
      if (_jumpFlashEl !== null) {
        _jumpFlashEl.classList.remove('chip--jump-flash');
        _jumpFlashEl = null;
      }
      _jumpFlashTimer = null;
    }, JUMP_FLASH_MS);
  }

  // §16.2.6.t8 — handle TracePane's `weekChange` event. Update the
  // reactive `previewWeek` (which `_schedulePreview` reads), persist
  // to per-station localStorage so reloads + next-mount hydrate the
  // same value, and reschedule a preview against the new week. The
  // reschedule path naturally cancels any in-flight preview from
  // the old week.
  async function handleWeekChange(e: CustomEvent<{ week: number }>) {
    const next = e.detail.week;
    if (next === previewWeek) return;
    previewWeek = next;
    writePreviewWeek(slug, next);
    // §16.2.7 c1 (D2 invalidation): the savedTrace cache was for the
    // OLD week's baseline; new week needs a fresh fetch. Bump the
    // generation so any in-flight refresh discards (R1). Then AWAIT
    // the new refresh BEFORE scheduling the staged preview (R2 —
    // otherwise POLICY_CANCEL_EXISTING supersedes the saved-trace
    // job). When showDiff is OFF the refresh returns immediately, so
    // the await is free.
    savedTrace = null;
    savedTraceError = null;
    savedTraceGeneration += 1;
    // §16.2.7.t4: ALSO invalidate the multi-week summary — its weeks
    // were [oldWeek..oldWeek+N]; new previewWeek shifts the range.
    weekDiffSummary = new Map();
    multiWeekFetching = false;
    multiWeekGeneration += 1;
    await _refreshSavedTraceIfNeeded();
    // Multi-week refresh runs after the single-week saved-trace
    // refresh so the operator's primary view (single-week diff) is
    // ready first. _refreshMultiWeekDiff is also async + fires its
    // own previews serially, so we void it (no need to block
    // _schedulePreview on the full range).
    void _refreshMultiWeekDiff();
    _schedulePreview();
  }

  // §16.2.7 c1 — diff toggle handler. Per D1, this is dispatched from
  // TracePane in c2; the c1 wiring + persistence lives here so the c2
  // surface can land independently. Cache lifecycle (D2):
  //   - ON → fire saved-trace preview to populate cache.
  //   - OFF → clear cache + error; no more saved-trace work pending.
  // §16.2.7.t3 review R1 — shared local-state + cache-lifecycle
  // helper. Owns the showDiff state transition + cache invalidation
  // semantics. Both `handleShowDiffChange` (toggle click) and the
  // storage event listener (cross-tab) call this to keep their
  // behaviors in lockstep — pre-extraction the two paths each held
  // their own copy of the OFF/ON cache logic, drifting was inevitable.
  // Does NOT write to localStorage — the caller decides whether to
  // persist (local toggle does; storage handler doesn't since the
  // other tab already wrote).
  function _applyShowDiffChange(next: boolean): void {
    if (next === showDiff) return;
    showDiff = next;
    if (!next) {
      // Bump the generation (c1 review R1) so any in-flight refresh
      // from a previous toggle ON discards instead of populating the
      // cache after we've cleared it. §16.2.7.t4: ALSO clear the
      // multi-week summary + bump its generation — diff OFF tears
      // down the whole compare-against-saved feature.
      savedTrace = null;
      savedTraceError = null;
      savedTraceGeneration += 1;
      weekDiffSummary = new Map();
      multiWeekFetching = false;
      multiWeekGeneration += 1;
      return;
    }
    // Toggle ON: fire a fresh refresh. _refreshSavedTraceIfNeeded
    // captures the current generation at start; no need to bump.
    // §16.2.7.t4: also fire the multi-week aggregator if compareNextN
    // is non-zero (operator's prior setting persists across toggle
    // OFF→ON cycles).
    void _refreshSavedTraceIfNeeded();
    void _refreshMultiWeekDiff();
  }

  function handleShowDiffChange(e: CustomEvent<{ on: boolean }>) {
    const next = e.detail.on;
    if (next === showDiff) return;
    writeShowDiff(slug, next);
    _applyShowDiffChange(next);
  }

  // §16.2.7.t6 — per-row Revert. Receives a slot index from
  // TracePane; calls attributeOpsToSlot to find the matching opLog
  // entries, splices them (highest index first to avoid shifting),
  // persists, and reschedules the staged preview. Saved-trace cache
  // is NOT invalidated — the saved baseline is unchanged (DB state);
  // only the staged side regressed. Empty attribution = defensive
  // no-op + console.warn (TracePane should have disabled the
  // button, but state drift is possible).
  function handleRevertSlot(e: CustomEvent<{ slotIndex: number }>): void {
    const slotIndex = e.detail.slotIndex;
    const indices = attributeOpsToSlot(opLog, slotIndex);
    if (indices.length === 0) {
      // eslint-disable-next-line no-console
      console.warn(
        `§16.2.7.t6 revertSlot: no direct attribution for slot ${slotIndex} ` +
          '(opLog has no patchBlock/retypeBlock/moveBlock/structuralBlockEdit ' +
          'targeting it). The button should have been disabled — this is a ' +
          'defensive no-op against state drift.',
      );
      return;
    }
    // Splice highest first so earlier-index removals don't shift
    // the later-index entries.
    const next = opLog.slice();
    for (let i = indices.length - 1; i >= 0; i--) {
      next.splice(indices[i], 1);
    }
    opLog = next;
    _persistOpLog();
    _schedulePreview();
  }

  // §16.2.7.t4 — compareNextN change handler dispatched from
  // TracePane's "+next N weeks" input (c2). Clamps to [0, MAX],
  // persists per-station, invalidates the multi-week cache, refires
  // the aggregator. Mirrors the showDiff helper-split pattern so
  // c2's cross-tab storage handler can call `_applyCompareNextNChange`
  // directly without the localStorage write.
  function _applyCompareNextNChange(next: number): void {
    const clamped = Math.max(
      0, Math.min(COMPARE_NEXT_N_MAX, Math.floor(Number(next) || 0)),
    );
    if (clamped === compareNextN) return;
    compareNextN = clamped;
    // Invalidate + reset the cache. multiWeekFetching cleared so the
    // UI doesn't render a stale "Computing…" placeholder if the
    // previous fetch was mid-flight.
    weekDiffSummary = new Map();
    multiWeekFetching = false;
    multiWeekGeneration += 1;
    if (clamped > 0) {
      void _refreshMultiWeekDiff();
    }
  }

  function handleCompareNextNChange(e: CustomEvent<{ n: number }>) {
    const next = e.detail.n;
    if (next === compareNextN) return;
    writeCompareNextN(slug, next);
    _applyCompareNextNChange(next);
  }

  // §16.2.7 c1 — fire a one-shot preview against the CURRENT saved
  // state (template + sidecar), cache the result in `savedTrace`. No
  // debounce (operator just opted in; freshness matters more than
  // amortizing the cost). Errors land in `savedTraceError` (D6 — UI
  // surfaces "Diff unavailable: ..." in c2). Concurrent refreshes
  // are SAFE — the generation gate ensures only the latest
  // generation's result lands, and the server's POLICY_CANCEL_EXISTING
  // cancels superseded jobs so the wasted-work window is microseconds
  // (§16.2.7.t1 review F1).
  //
  // Review R1 — generation gate: every assignment to savedTrace /
  // savedTraceError checks `myGen` against the current
  // `savedTraceGeneration`. If a context-change invalidation (slug /
  // week / Save / toggle-off) bumped the generation since this call
  // started, we discard the late result instead of polluting the
  // cleared cache.
  //
  // Review R2 — orchestration with the staged-trace preview: the
  // server's POLICY_CANCEL_EXISTING means starting a second
  // KIND_PREVIEW supersedes the first. Callers that ALSO start a
  // staged-trace preview (handleStagedSave, handleWeekChange) AWAIT
  // this function before calling _schedulePreview — that way the
  // saved-trace job runs to completion first; the staged-trace
  // preview only starts after the cache is filled.
  async function _refreshSavedTraceIfNeeded(): Promise<void> {
    if (!showDiff) return;
    if (savedTrace !== null) return;
    if (!template || !sidecar) return;
    // §16.2.7.t1 review F1 fix — no pending guard. The generation gate
    // (myGen vs savedTraceGeneration) handles correctness for late
    // results; the server's POLICY_CANCEL_EXISTING cancels superseded
    // jobs so the wasted-work window between concurrent refreshes is
    // microseconds. Pre-fix the pending guard short-circuited new
    // refreshes triggered by Save / week-change while an OLD
    // invalidated refresh was in flight — cache stuck empty forever.
    const myGen = savedTraceGeneration;
    savedTraceError = null;
    try {
      const start = await startPreviewJob(
        slug,
        template as unknown as Record<string, unknown>,
        sidecar as unknown as Record<string, unknown>,
        previewWeek,
      );
      for await (const snap of watchJob(start.job_id)) {
        if (myGen !== savedTraceGeneration) return;
        if (snap.status === 'done') {
          const r = snap.result;
          if (isPreviewResult(r)) {
            savedTrace = r.trace;
          }
          return;
        }
        if (snap.status === 'error') {
          savedTraceError = _friendlyPreviewError(snap.error?.message);
          return;
        }
        if (snap.status === 'cancelled') {
          // Superseded server-side. The caller's await-then-schedule
          // pattern (R2) prevents this in the normal flow; a manual
          // cancel via cancelJob could still trip it. Leave savedTrace
          // null so the next opportunity refires.
          return;
        }
      }
    } catch (err) {
      if (myGen !== savedTraceGeneration) return;
      savedTraceError = _friendlyPreviewError(
        err instanceof Error ? err.message : String(err),
      );
    }
    // §16.2.7.t1 review F2 fix — `finally` block removed; nothing
    // to clean up now that savedTracePending is gone. The generation
    // gate handles late-result discard at the assignment sites
    // above; no lock-like state needs releasing.
  }

  // §16.2.7.t4 — multi-week diff aggregator. Fires saved+staged
  // preview pairs serially for weeks [previewWeek, previewWeek+1,
  // ..., previewWeek+compareNextN]. Populates `weekDiffSummary`
  // per week. Uses `multiWeekGeneration` (separate from
  // `savedTraceGeneration` so the two refresh paths don't cross-
  // invalidate each other) to gate against context changes mid-
  // fetch. Per-week failures land in the summary as `{error: ...}`
  // and don't abort the rest of the range (D5).
  //
  // Sequencing (D4): each week runs saved first, then staged, in
  // strict order. Concurrent firing would race against
  // POLICY_CANCEL_EXISTING server-side. The whole walk awaits its
  // own progress so the operator's next state change (toggle off,
  // week change, Save) can bump the generation + cause the next
  // gate-check to discard the in-flight result cleanly.
  async function _runOneWeekPreview(
    week: number,
    snapshot: { template: Record<string, unknown>; sidecar: Record<string, unknown> },
  ): Promise<Trace | null> {
    const start = await startPreviewJob(slug, snapshot.template, snapshot.sidecar, week);
    for await (const snap of watchJob(start.job_id)) {
      if (snap.status === 'done') {
        const r = snap.result;
        return isPreviewResult(r) ? r.trace : null;
      }
      if (snap.status === 'error') {
        throw new Error(snap.error?.message ?? 'preview failed');
      }
      if (snap.status === 'cancelled') {
        // §16.2.7.t4 review R1 — throw instead of returning null.
        // Pre-fix the null return caused classifySlots(null, staged)
        // to classify every staged slot as ADDED (the classifier's
        // null-defensive contract treats null as zero-length). That
        // produced wildly wrong summary counts when the server's
        // POLICY_CANCEL_EXISTING superseded our preview (e.g. when
        // _schedulePreview's debounced staged preview fires mid-
        // aggregator). Throw → outer try/catch records {error} for
        // that week → UI shows ⚠ instead of misleading counts.
        throw new Error('preview superseded (cancelled server-side)');
      }
    }
    return null;
  }

  async function _refreshMultiWeekDiff(): Promise<void> {
    if (!showDiff) return;
    if (compareNextN <= 0) return;
    if (!template || !sidecar || !stagedTemplate || !stagedSidecar) return;
    const myGen = multiWeekGeneration;
    multiWeekFetching = true;
    const next: Map<number, WeekSummary> = new Map();
    try {
      const savedSnap = {
        template: template as unknown as Record<string, unknown>,
        sidecar: sidecar as unknown as Record<string, unknown>,
      };
      const stagedSnap = {
        template: stagedTemplate as unknown as Record<string, unknown>,
        sidecar: stagedSidecar as unknown as Record<string, unknown>,
      };
      for (let i = 0; i <= compareNextN; i++) {
        if (myGen !== multiWeekGeneration) return;
        const wk = previewWeek + i;
        try {
          const savedT = await _runOneWeekPreview(wk, savedSnap);
          if (myGen !== multiWeekGeneration) return;
          const stagedT = await _runOneWeekPreview(wk, stagedSnap);
          if (myGen !== multiWeekGeneration) return;
          const cls = classifySlots(savedT, stagedT);
          next.set(wk, summarizeClassifications(cls));
          // Commit incrementally so the UI sees progress without
          // waiting for the whole range. Each iteration writes the
          // entire `next` Map so Svelte's reactivity fires.
          weekDiffSummary = new Map(next);
        } catch (err) {
          if (myGen !== multiWeekGeneration) return;
          next.set(wk, {
            error: err instanceof Error ? err.message : String(err),
          });
          weekDiffSummary = new Map(next);
        }
      }
    } finally {
      if (myGen === multiWeekGeneration) {
        multiWeekFetching = false;
      }
    }
  }

  // Cancel any pending or in-flight preview job. Called from
  // onDestroy + on toggle-off + before scheduling a new preview.
  // The previewGeneration bump invalidates any watchJob loop that
  // captured the old generation.
  function _cancelPreview() {
    previewGeneration += 1;
    if (previewDebounceTimer !== null) {
      clearTimeout(previewDebounceTimer);
      previewDebounceTimer = null;
    }
    if (currentPreviewJobId !== null) {
      const jobId = currentPreviewJobId;
      currentPreviewJobId = null;
      void cancelJob(jobId).catch(() => {
        // cancelJob is idempotent (204 / 404 both succeed); any
        // network error here is non-fatal — POLICY_CANCEL_EXISTING
        // on the server-side supersede covers the same need.
      });
    }
    previewing = false;
  }

  /** Schedule a debounced preview job. Called from withSaveLifecycle
   *  after a successful PATCH when livePreviewOn. Resets the debounce
   *  timer + cancels the in-flight job (latest-wins behavior matching
   *  §16.2.0 D4). */
  function _schedulePreview() {
    if (!livePreviewOn) return;
    if (!template || !sidecar) return;
    // _cancelPreview bumped previewGeneration; the resulting value
    // IS this schedule's generation token. Review #1 finding #6:
    // pre-fix added a second `+= 1` here on the (mis-?)reading that
    // both invalidation steps needed their own gen — the double-
    // bump was harmless but read as suspicious.
    _cancelPreview();
    const myGeneration = previewGeneration;
    previewDebounceTimer = setTimeout(async () => {
      previewDebounceTimer = null;
      if (myGeneration !== previewGeneration) return;
      if (!template || !sidecar) return;
      previewing = true;
      previewError = null;
      try {
        // §16.2.6 c3: preview against the STAGED view, not the
        // loaded view. With toggle ON + opLog empty, stagedTemplate
        // === template so the body is identical to c2's behavior.
        // With opLog non-empty, the operator's queued edits show
        // up in the trace before they Save.
        const start = await startPreviewJob(
          slug,
          stagedTemplate as unknown as Record<string, unknown>,
          stagedSidecar as unknown as Record<string, unknown>,
          previewWeek,
        );
        // Review #1 fix #1: a supersede or onDestroy that fired
        // DURING the POST already bumped previewGeneration, but
        // could only no-op-cancel because currentPreviewJobId was
        // still null at the time. The just-spawned job is now
        // orphaned server-side — explicitly cancel it before
        // returning. Without this, the server runs the unwanted
        // preview to completion (POLICY_CANCEL_EXISTING only
        // supersedes when a NEW preview starts).
        if (myGeneration !== previewGeneration) {
          void cancelJob(start.job_id).catch(() => {
            // Idempotent — 204 / 404 / network blip all fine.
          });
          return;
        }
        currentPreviewJobId = start.job_id;
        for await (const snap of watchJob(start.job_id)) {
          if (myGeneration !== previewGeneration) return;
          if (snap.status === 'done') {
            // Discriminate via the hoisted type guard (§16.2.6 c3
            // fold-in of c2 review #1 finding #12) — single
            // source of truth for the PreviewResult / GenerateResponse
            // discriminator.
            const r = snap.result;
            if (isPreviewResult(r)) {
              previewTrace = r.trace;
            } else {
              // Review #1 fix #13: defensive narrow used to silently
              // keep the previous trace + clear `previewing` — the
              // operator saw stale data with no error indication. Now
              // we surface the unexpected shape as a preview error so
              // the banner names it; the (possibly stale) trace stays
              // visible per the design's "frame of reference" rule.
              previewError = 'preview returned an unexpected shape';
            }
            previewing = false;
            currentPreviewJobId = null;
            return;
          }
          if (snap.status === 'error') {
            previewError = _friendlyPreviewError(snap.error?.message);
            previewing = false;
            currentPreviewJobId = null;
            return;
          }
          if (snap.status === 'cancelled') {
            // Superseded by a newer preview OR explicitly cancelled.
            // Don't update previewTrace; let the supersede's eventual
            // result land or leave the last-good trace visible.
            previewing = false;
            currentPreviewJobId = null;
            return;
          }
        }
      } catch (err) {
        if (myGeneration !== previewGeneration) return;
        previewError = _friendlyPreviewError(
          err instanceof Error ? err.message : String(err),
        );
        previewing = false;
        currentPreviewJobId = null;
      }
    }, PREVIEW_DEBOUNCE_MS);
  }

  async function toggleLivePreview() {
    // §16.2.6 c3e — guard the OFF transition when staged edits
    // exist. confirm() returning false aborts the toggle and leaves
    // the operator in staging mode; true runs _resetStagedState()
    // (shared with handleDiscard) so the OFF transition matches the
    // explicit Discard button semantically — clears opLog, clears
    // any stale saveError/conflict banner, refetches the sidecar
    // (G1 review fix: pre-extract the path skipped saveError +
    // conflict, leaving stale error banners visible past the OFF
    // transition). The "save then turn off" path is c3e c2 work
    // (needs a 3-button modal: Save, Discard, Cancel — not a binary
    // confirm).
    if (livePreviewOn && hasStagedEdits) {
      const ok = window.confirm(
        `You have ${opLog.length} staged edit${opLog.length === 1 ? '' : 's'} ` +
        'that will be discarded if you turn live preview off. Continue?',
      );
      if (!ok) return;
      await _resetStagedState();
    }
    livePreviewOn = !livePreviewOn;
    writeLivePreview(slug, livePreviewOn);
    if (!livePreviewOn) {
      _cancelPreview();
      previewTrace = null;
      previewError = null;
    } else {
      // Toggle-on: schedule an immediate preview against the current
      // template/sidecar so the operator sees a trace without having
      // to edit first.
      _schedulePreview();
    }
  }

  // §16.2.6 c3e — beforeunload listener. Browsers display a generic
  // "leave site?" prompt when `preventDefault()` runs inside a
  // beforeunload handler. We only want the prompt when staged edits
  // would be lost — clean-state navigation (no edits, or just-saved)
  // should never warn. The listener is registered in onMount + torn
  // down in onDestroy so it doesn't leak across page navigations
  // within the SPA.
  let _beforeUnloadHandler: ((e: BeforeUnloadEvent) => void) | null = null;

  // §16.2.6.t1-follow-up — cross-tab `storage` event listener.
  // Browsers dispatch the `storage` event on OTHER tabs (never the
  // tab that called setItem). When the operator has two tabs open
  // on the same station: Tab A stages an edit → its `_persistOpLog`
  // writes to localStorage → Tab B's storage handler fires, re-reads
  // the persisted entry, and updates its in-memory opLog to match.
  // Last-write-wins, but the loser SEES the change rather than
  // shadowing it with stale state until the next reload.
  let _storageHandler: ((e: StorageEvent) => void) | null = null;

  onDestroy(() => {
    _cancelPreview();
    if (_beforeUnloadHandler) {
      window.removeEventListener('beforeunload', _beforeUnloadHandler);
      _beforeUnloadHandler = null;
    }
    if (_storageHandler) {
      window.removeEventListener('storage', _storageHandler);
      _storageHandler = null;
    }
    // §16.2.6.t3 — clear any in-flight chip-jump flash timer so it
    // doesn't fire after the route navigates away (the chipEl
    // reference would still be valid but stripping the class on a
    // detached element is harmless; the wasted setTimeout callback
    // is what we're preventing).
    if (_jumpFlashTimer !== null) {
      clearTimeout(_jumpFlashTimer);
      _jumpFlashTimer = null;
    }
    if (_jumpFlashEl !== null) {
      _jumpFlashEl.classList.remove('chip--jump-flash');
      _jumpFlashEl = null;
    }
  });

  // §16.2.5.t11 — single point of truth for "we have a fresh
  // server-validated sidecar+template, mirror it into local state".
  // Both `loadSidecar()` (GET path) and `withSaveLifecycle`'s
  // inline-apply branch (§16.2.5.t5 PATCH path that ships a typed
  // SidecarMutationResponse) route through here so a future contributor
  // adding a 5th canvas field (e.g. macro_warnings) only updates ONE
  // assignment site instead of two.
  function _applySidecarState(s: {
    template: StationTemplate;
    sidecar: Sidecar;
    updatedAt: string | null;
    sidecarError: string | null;
  }): void {
    template = s.template;
    sidecar = s.sidecar;
    updatedAt = s.updatedAt;
    sidecarError = s.sidecarError;
  }

  async function loadSidecar() {
    const r = await getStationSidecar(slug);
    _applySidecarState({
      template: r.template,
      sidecar: r.sidecar,
      updatedAt: r.updated_at,
      sidecarError: r.sidecar_error,
    });
  }

  onMount(async () => {
    if (!slug) {
      loadError = 'missing slug';
      loading = false;
      return;
    }
    // §16.2.6 c3e — register the beforeunload listener at mount.
    // Captures `hasStagedEdits` reactively at fire-time (the
    // closure reads the current values of the reactive bindings).
    _beforeUnloadHandler = (e: BeforeUnloadEvent) => {
      if (hasStagedEdits) {
        // Per spec, modern browsers ignore the returned string
        // and show a generic prompt; setting returnValue is the
        // historical signal that we want to block, and
        // preventDefault is the modern one. Set both for
        // cross-browser coverage.
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', _beforeUnloadHandler);
    // §16.2.6.t1-follow-up — register the cross-tab `storage`
    // listener. Filters by `storageArea` (sessionStorage events
    // also trigger this listener per spec), then by key so other-
    // station entries don't perturb this station's opLog; ignores
    // no-op events (`oldValue === newValue`) that some browsers
    // emit.
    _storageHandler = (e: StorageEvent) => {
      // M3 follow-up review: defensive `storageArea` guard. Per
      // WHATWG spec, sessionStorage writes also fire on this
      // listener; we only care about localStorage.
      if (e.storageArea !== window.localStorage) return;
      if (e.newValue === e.oldValue) return;

      // §16.2.7.t3 — cross-tab showDiff toggle sync. If Tab A
      // toggles, Tab B receives the storage event + mirrors the
      // change via the shared `_applyShowDiffChange` helper (review
      // R1 — same cache-lifecycle semantics as handleShowDiffChange;
      // any divergence between local + cross-tab paths would be a
      // correctness bug). No localStorage write — the other tab
      // already wrote (the writing tab doesn't receive its own
      // storage event).
      if (e.key === _showDiffKey(slug)) {
        _applyShowDiffChange(e.newValue === 'on');
        return;
      }

      // §16.2.7.t4 — cross-tab compareNextN sync. Mirrors the
      // showDiff path: shared `_applyCompareNextNChange` helper
      // owns the local-state + cache lifecycle so local + cross-tab
      // paths can't drift. The newValue parse mirrors readCompareNextN
      // (defensive against stale shapes — null/missing → 0).
      if (e.key === _compareNextNKey(slug)) {
        const raw = e.newValue;
        const parsed = raw === null ? 0 : Number(raw);
        const next = Number.isFinite(parsed) ? parsed : 0;
        _applyCompareNextNChange(next);
        return;
      }

      if (e.key !== _stagedEditsKey(slug)) return;
      if (e.newValue === null) {
        // Other tab cleared the entry (Save, Discard, or
        // navigate-away). Drop our in-memory opLog to match.
        opLog = [];
        return;
      }
      // Other tab mutated the entry. Re-read with the same shape /
      // schemaVersion / known-kind defensive filters used at mount.
      const persisted = _readStagedEdits(slug);
      if (persisted === null) return;
      // M4 follow-up review: defensive copy of the parsed array so
      // a future contributor's in-place push on `opLog` doesn't
      // mutate the parsed payload.
      opLog = persisted.opLog.slice();
      // M1 follow-up review: cross-tab payload was authored against
      // a different base (Tab A saved, server stamped a new
      // updated_at, Tab A then staged new edits against ts-2 while
      // we're still on ts-1). The staged preview would apply Tab A's
      // ops against our stale base — refresh so the preview's
      // index space matches Tab A's. Conflict path on subsequent
      // Save remains unchanged (PATCH still fires with the freshest
      // updated_at the refetch hands us).
      if (persisted.updatedAtSnapshot !== updatedAt) {
        loadSidecar().catch((err) => {
          saveError = err instanceof Error ? err.message : String(err);
        });
      }
    };
    window.addEventListener('storage', _storageHandler);
    // Hydrate the live-preview toggle from localStorage BEFORE the
    // sidecar loads — so a returning operator's preferred mode is
    // already active when the canvas renders. No preview job fires
    // here; the operator's first edit triggers _schedulePreview if
    // the toggle is ON.
    livePreviewOn = readLivePreview(slug);
    // §16.2.4.t1 — hydrate the macro palette open-state too. The
    // palette is browse-only (no scheduler / side effects) so this
    // is purely a UI hydration.
    paletteOpen = readMacroPalette(slug);
    try {
      // §16.2.2.t1 — getStationSidecar validates the response shape
      // before return. Both ApiError (network/4xx) and
      // EditorMetadataError (shape) carry `.message`.
      await loadSidecar();
      // §16.2.6.t1 — recover staged edits from localStorage.
      // Gated on livePreviewOn: if the operator's saved preference
      // is OFF, recovering staged edits silently would surprise
      // them (the staged-save controls only render when toggle ON,
      // so recovered ops would persist invisibly). When OFF, drop
      // any stored opLog instead.
      if (livePreviewOn) {
        _recoverStagedEdits();
      } else {
        _writeStagedEdits(slug, null);
      }
      // If the operator's saved preference is "live preview ON",
      // fire one preview right after the sidecar lands so the trace
      // pane shows actual content instead of the empty-state nudge.
      // With recovered staged edits, the preview reflects the
      // applied state via the `stagedView` reactive derivation.
      if (livePreviewOn) _schedulePreview();
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
      // L1: 404 on loadSidecar means the station was deleted
      // elsewhere (DB delete, manual SQL, external workflow).
      // Clear any orphan localStorage entry so it doesn't
      // accumulate forever for a slug that no longer exists.
      if (e instanceof ApiError && e.status === 404) {
        _writeStagedEdits(slug, null);
      }
    } finally {
      loading = false;
    }
  });

  // §16.2.6.t1 — recover persisted opLog. Called from onMount AFTER
  // loadSidecar succeeds so `updatedAt` is populated for the
  // freshness check. Silently skipped when no entry exists; prompts
  // (window.confirm) when the staged edits were authored against a
  // different `updated_at` than the current sidecar. Confirm =
  // discard; cancel = recover anyway.
  function _recoverStagedEdits(): void {
    const persisted = _readStagedEdits(slug);
    if (persisted === null) return;
    if (persisted.opLog.length === 0) {
      _writeStagedEdits(slug, null);
      return;
    }
    const fresh = persisted.updatedAtSnapshot === updatedAt;
    if (fresh) {
      // Silent recovery — the staged edits apply against the same
      // base they were authored against.
      opLog = persisted.opLog;
      return;
    }
    // Stale — prompt. Default to discard (confirm = discard).
    const ok = window.confirm(
      `Found ${persisted.opLog.length} staged edit${persisted.opLog.length === 1 ? '' : 's'} ` +
      `from a previous session, but this station has changed since then ` +
      `(the saved version may have been edited elsewhere). Discard the ` +
      `staged edits? (Cancel to recover them anyway.)`,
    );
    if (ok) {
      _writeStagedEdits(slug, null);
    } else {
      opLog = persisted.opLog;
      // Re-persist with the CURRENT updatedAt as the new snapshot —
      // the operator chose to apply the staged edits against the
      // current base, so the recovery prompt shouldn't fire again
      // on the next load.
      _persistOpLog();
    }
  }

  // Map a chip's ExpandedRef to the storage BlockAddress. row_idx is
  // dropped — a row_template edit targets the shared block_idx
  // (Decision B).
  function refToAddress(ref: ExpandedRef): BlockAddress | null {
    if (ref.kind !== 'atomic-ref') return null;
    if (ref.slot_index !== undefined) return { slot_index: ref.slot_index };
    if (ref.block_idx !== undefined) return { block_idx: ref.block_idx };
    return null;
  }

  // c2 review #5: extract the PATCH-then-refetch lifecycle that
  // every write-path handler shares. Resets the three banner
  // variables, runs the supplied PATCH (catching 409 via the
  // shared BlockPatchConflict contract — Mid-slice review 🟡-1: do
  // NOT adopt the server's token; force the operator to Reload
  // before retrying so concurrent changes aren't silently
  // overwritten), then refetches the canvas. Refetch failures
  // surface with a distinct `refetchVerbPrefix` so the operator
  // can tell "save itself failed" from "save landed but the view
  // didn't refresh."
  /** §16.2.5.t5 type guard: did the PATCH return a self-contained
   *  SidecarMutationResponse (the 6 §16.2.5 endpoints)? If yes,
   *  apply it directly and skip the GET refetch. Falls back to
   *  GET for §16.2.3 patchBlock + §16.2.4 retypeBlock + patchMacro,
   *  which return different shapes (no top-level `sidecar` field).
   *
   *  c1 review #3 finding #5: re-tighten the structural narrow.
   *  The c1-rescue #6 "presence is sufficient" simplification was
   *  wrong — a future async-202 or backend regression returning
   *  `{sidecar: null}` would pass `'sidecar' in r` and assign null
   *  to the local `sidecar` binding; the next EditorCanvas render
   *  would crash on `sidecar.sections.map(...)` with no saveError,
   *  no banner, white screen. The threat model includes "the server
   *  returns the shape but with a null/wrong-type sidecar value,"
   *  not just "the server doesn't return a sidecar field at all."
   *  Cost of the deeper check: 3 lines vs 1. */
  // §16.2.5.t11 — TS discriminator on the `SaveResponse` union.
  // SidecarMutationResponse is the only variant with `sidecar`, so the
  // `'sidecar' in r` check narrows at compile time. Runtime checks
  // (typeof, null-guard, array-guard on the field value) stay as
  // belt-and-suspenders defense against a backend drift that smuggles
  // a malformed payload past the typed contract.
  function _isSidecarMutationResponse(
    r: SaveResponse,
  ): r is SidecarMutationResponse {
    if (r === null || typeof r !== 'object') return false;
    if (!('sidecar' in r)) return false;
    const s = (r as { sidecar: unknown }).sidecar;
    return s !== null && typeof s === 'object' && !Array.isArray(s);
  }

  async function withSaveLifecycle(
    doPatch: () => Promise<SaveResponse>,
    refetchVerbPrefix: string,
  ): Promise<void> {
    saveError = null;
    conflict = false;
    exportMsg = null;
    let result: SaveResponse;
    try {
      result = await doPatch();
    } catch (err) {
      if (err instanceof BlockPatchConflict) {
        conflict = true;
      } else {
        saveError = err instanceof Error ? err.message : String(err);
      }
      return;
    }
    // PATCH landed.
    //
    // §16.2.5.t5: if the response carries the new sidecar (and
    // template), apply it directly — no GET refetch needed. The
    // 6 §16.2.5 endpoints (section CRUD, reorder, move, structural)
    // return SidecarMutationResponse with BOTH sidecar + template
    // (c1 review #3 finding #6 — eliminates the two-tab drift
    // hazard from sending template=None for sidecar-only edits).
    // §16.2.3 patchBlock + §16.2.4 retypeBlock + patchMacro do NOT
    // return this shape — they fall through to the GET refetch
    // below. Net: section drag / move / structural edits drop a
    // per-mutation 2× round-trip + a redundant server-side
    // validate_sidecar pass.
    //
    // c1 review #3 finding #7: route the response through
    // `parseEditorSidecarResponse` (the same validator GET uses).
    // The prior `as unknown as StationTemplate` cast was a pure
    // TypeScript assertion with zero runtime work — backend drift
    // (a partial-template response from a refactor bug) would
    // crash deep in EditorCanvas with no signal pointing at the
    // PATCH that drifted. Wire the validator + surface failures
    // as a saveError naming the offending field.
    if (_isSidecarMutationResponse(result)) {
      try {
        const parsed = parseEditorSidecarResponse({
          template: result.template,
          sidecar: result.sidecar,
          sidecar_error: null,
          updated_at: result.updated_at,
        });
        // §16.2.5.t11 — route through `_applySidecarState` so this
        // PATCH branch and `loadSidecar()` (GET path) share the same
        // field-assignment site. Slice-review c1-rescue #1: a
        // successful PATCH means the server-validated sidecar is back
        // in sync — clear any stale warning a prior GET set
        // (`loadSidecar()` would have done the same).
        _applySidecarState({
          template: parsed.template,
          sidecar: parsed.sidecar,
          updatedAt: parsed.updated_at,
          sidecarError: null,
        });
      } catch (err) {
        // Validation failed → backend drift. Fall through to the
        // GET refetch so the operator at least sees the canvas
        // against the next-fetched (validated) sidecar.
        saveError =
          `${refetchVerbPrefix}, but the response shape failed validation: ` +
          (err instanceof Error ? err.message : String(err));
        try {
          await loadSidecar();
        } catch {
          // Compound failure — keep the validation error visible.
        }
        return;
      }
      // §16.2.6 c2: schedule a debounced preview against the new
      // in-memory state. _schedulePreview is a no-op when
      // livePreviewOn is false, so toggle OFF behavior is unchanged.
      _schedulePreview();
      return;
    }
    // Fallback: refetch via GET. Distinct error prefix (mid-slice
    // review 🟢-3) so a refetch blip after a successful save
    // doesn't masquerade as a save failure.
    try {
      await loadSidecar();
      _schedulePreview();
    } catch (err) {
      saveError =
        `${refetchVerbPrefix}, but failed to refresh the view: ` +
        (err instanceof Error ? err.message : String(err));
    }
  }

  // §16.2.6 c3c — staging intercept helper. Each
  // livePreviewOn-gated handler queues its op via this function so
  // the queue-append + reset-state + preview-schedule shape stays
  // in one place. Extracted in c3c per c3b review #1 finding #8
  // when the handler count grew from 3 (c3 + c3b) to 6 (c3c).
  // c3d adds 3 more handlers (moveBlock / retype / structural)
  // that ride on the same helper for free.
  function _interceptToOpLog(op: Edit): void {
    opLog = [...opLog, op];
    _persistOpLog();
    saveError = null;
    conflict = false;
    exportMsg = null;
    _schedulePreview();
  }

  // §16.2.3 c2 — DetailPanel save → PATCH → refetch. On 409 (someone
  // else changed the station) show a reload prompt; on other errors
  // show the message inline.
  async function handleSave(
    e: CustomEvent<{ ref: ExpandedRef; fields: Record<string, string | number | string[] | null> }>,
  ) {
    const address = refToAddress(e.detail.ref);
    if (address === null) {
      saveError = 'cannot edit this block (no storage address)';
      conflict = false;
      exportMsg = null;
      return;
    }
    // §16.2.6 c3: when live-preview is ON, intercept the chip edit
    // into the staging buffer instead of immediately PATCHing. The
    // operator's edit shows up in the previewed trace; nothing
    // commits until they click Save.
    //
    // Toggle OFF (or any non-patchBlock edit kind in c3 MVP) falls
    // through to the existing withSaveLifecycle path — c3b extends
    // the interception to the other 8 op kinds.
    if (livePreviewOn) {
      // c3 review #1 fix #9 defensive skip — empty-fields save
      // (DetailPanel quirk) gets silently dropped before queueing.
      const fieldKeys = Object.keys(e.detail.fields);
      if (fieldKeys.length === 0) return;
      // c3 review #1 fix #3 shallow-copy — fields + address into
      // the queued op so a future DetailPanel change that mutates
      // the emitted dict doesn't drift the staged op.
      _interceptToOpLog({
        kind: 'patchBlock',
        address: { ...address },
        fields: { ...e.detail.fields },
      });
      return;
    }
    await withSaveLifecycle(
      () => patchBlock(slug, address, e.detail.fields, updatedAt),
      'saved',
    );
  }

  // §16.2.6.t2 c2 — Save flow rewritten to use the transactional
  // `applyBatch` endpoint (replaces the c3-era per-op PATCH loop
  // via replayOpLog). All edits commit atomically; mid-batch failure
  // rolls back the WHOLE batch server-side, so `opLog` stays intact
  // for Retry / Drop. The c3e c3 modal still surfaces — semantics
  // shift slightly:
  //   - Retry: re-fire the whole opLog (no fix-and-retry mid-batch
  //     to manage; server's all-or-nothing means either all land
  //     or none did).
  //   - Drop: pop opLog[failedAtIdx - 1] (the SERVER-identified
  //     failed edit, 1-based) + re-fire. Sentinel `failedAtIdx === 0`
  //     (empty edits, _finalize, internal) means no specific edit;
  //     UI hides the Drop button in that case.
  //   - Cancel: leave opLog + saveError; operator examines the
  //     failed edit, fixes it inline, re-clicks Save.
  let failedAtIdx = 0;  // 1-based when modal visible; 0 = sentinel.
  let failedKind = '';

  async function handleStagedSave() {
    if (opLog.length === 0) return;
    saving = true;
    saveError = null;
    conflict = false;
    exportMsg = null;
    failedAtIdx = 0;
    failedKind = '';
    try {
      let result;
      try {
        result = await applyBatch(slug, opLog, updatedAt);
      } catch (err) {
        if (err instanceof BlockPatchConflict) {
          conflict = true;
          return;
        }
        if (err instanceof SidecarBatchFailure) {
          // Server-identified failure. failedAtIdx 1-based when a
          // specific edit caused it; 0 sentinel for composition
          // failures (kind === '_finalize') + empty-edits.
          failedAtIdx = err.failedAtIdx;
          failedKind = err.kind;
          if (err.failedAtIdx > 0) {
            saveError =
              `Save failed at staged edit ${err.failedAtIdx} of ${opLog.length}: ` +
              err.message;
          } else if (err.kind === '_finalize') {
            saveError = `Save failed: ${err.message}`;
          } else {
            saveError = err.message;
          }
          showRetryModal = true;
          return;
        }
        // Network / generic ApiError.
        saveError = err instanceof Error ? err.message : String(err);
        return;
      }
      // Success — apply returned sidecar + template + clear opLog.
      try {
        const parsed = parseEditorSidecarResponse({
          template: result.template,
          sidecar: result.sidecar,
          sidecar_error: null,
          updated_at: result.updated_at,
        });
        template = parsed.template;
        sidecar = parsed.sidecar;
        updatedAt = parsed.updated_at;
      } catch (err) {
        // Backend response shape drift — fall through to a GET refetch.
        saveError =
          'Save landed but the response shape failed validation: ' +
          (err instanceof Error ? err.message : String(err));
        try { await loadSidecar(); } catch { /* preserve validation error */ }
      }
      opLog = [];
      _persistOpLog();
      sidecarError = null;
      // §16.2.7 c1 (D2 invalidation): the saved state JUST changed
      // (this Save just landed). Bump the generation (R1) + AWAIT
      // the new refresh BEFORE scheduling the staged preview (R2 —
      // server's POLICY_CANCEL_EXISTING would otherwise supersede
      // our saved-trace job when _schedulePreview's debounce fires).
      // When showDiff is OFF the await is free.
      savedTrace = null;
      savedTraceError = null;
      savedTraceGeneration += 1;
      // §16.2.7.t4: invalidate the multi-week summary. Saved-state
      // baseline just shifted; every week's diff needs to recompute.
      weekDiffSummary = new Map();
      multiWeekFetching = false;
      multiWeekGeneration += 1;
      await _refreshSavedTraceIfNeeded();
      void _refreshMultiWeekDiff();
      _schedulePreview();
    } finally {
      saving = false;
    }
  }

  // §16.2.6 c3e c3 — Retry: close modal + re-fire whole batch.
  async function handleRetry() {
    showRetryModal = false;
    await handleStagedSave();
  }

  // §16.2.6 c3e c3 — Drop & continue: pop the SERVER-identified
  // failed edit (failedAtIdx 1-based) + re-fire on the rest.
  // Sentinel `failedAtIdx === 0` means no specific edit (composition
  // failure or empty); the modal hides Drop in that case, so this
  // function won't be called with the sentinel.
  async function handleDrop() {
    // §16.2.6.t2 slice-review T8 (folded into t13): defend against
    // a server-returned out-of-bounds failedAtIdx (race window —
    // operator dropped an edit BETWEEN the request being prepared
    // and the response arriving). Silently dropping opLog[0] in
    // that case would remove the WRONG edit. Surface a banner +
    // leave opLog intact instead so the operator can re-evaluate.
    if (failedAtIdx > opLog.length) {
      showRetryModal = false;
      const plural = opLog.length === 1 ? '' : 's';
      saveError = `Internal error: server reported failed edit ${failedAtIdx} but the queue only has ${opLog.length} edit${plural}. Staged edits preserved; please Save again or Discard.`;
      failedAtIdx = 0;
      failedKind = '';
      return;
    }
    showRetryModal = false;
    if (failedAtIdx > 0) {
      opLog = [
        ...opLog.slice(0, failedAtIdx - 1),
        ...opLog.slice(failedAtIdx),
      ];
    } else {
      // Defensive fallback (shouldn't fire — UI hides Drop on sentinel).
      opLog = opLog.slice(1);
    }
    _persistOpLog();
    saveError = null;
    failedAtIdx = 0;
    failedKind = '';
    if (opLog.length === 0) return;
    await handleStagedSave();
  }

  // §16.2.6 c3e c3 — Cancel: close modal but leave saveError +
  // opLog intact so the operator can examine the failed op + fix
  // it inline + click Save again (fix-and-retry-later flow).
  function handleCancelRetry() {
    showRetryModal = false;
  }

  // §16.2.6 c3e c3 review J2: autofocus action — focus the bound
  // element when it mounts. Lets the modal's primary action (Retry)
  // grab focus on open so keyboard operators don't have to tab in.
  function autofocus(node: HTMLElement) {
    node.focus();
  }

  // §16.2.6 c3e c3 review J1: ESC-anywhere-on-page closes the modal
  // (matches modern modal a11y expectations). Wired via
  // <svelte:window on:keydown> below; guarded on `showRetryModal`
  // so non-modal pages aren't burdened.
  function handleModalKeydown(e: KeyboardEvent) {
    if (showRetryModal && e.key === 'Escape') {
      handleCancelRetry();
    }
  }

  // §16.2.6 c3e c1 review G1 — drop all staged edits + clear stale
  // error/conflict banners + refetch the sidecar. Shared between the
  // Discard button (handleDiscard) and the toggle-off-with-confirm
  // path (toggleLivePreview), so both surfaces present the same
  // "clean slate" semantic. Returns true iff the reload succeeded;
  // the caller decides whether to schedule a fresh preview after
  // (Discard does, toggle-off doesn't — preview is turning OFF).
  async function _resetStagedState(): Promise<boolean> {
    opLog = [];
    _persistOpLog();
    saveError = null;
    conflict = false;
    try {
      await loadSidecar();
      return true;
    } catch (err) {
      saveError = err instanceof Error ? err.message : String(err);
      return false;
    }
  }

  // §16.2.6 c3 — drop all staged edits + restore the displayed
  // state to the last-saved sidecar. Refetches via GET so the
  // canvas re-renders against fresh server state (covers the case
  // where another session changed the station while these edits
  // were staged — the operator sees the conflict winner's content
  // rather than their stale base).
  async function handleDiscard() {
    if (opLog.length === 0) return;
    if (await _resetStagedState()) _schedulePreview();
  }

  async function handleExport() {
    saveError = null;
    exportMsg = null;
    try {
      const r = await exportStationTemplate(slug);
      exportMsg = `Exported to ${r.path} (${r.bytes} bytes).`;
    } catch (err) {
      saveError = err instanceof Error ? err.message : String(err);
    }
  }

  // §16.2.4 c2 — macro fan-out edit (PATCH /macros/{macro_id}).
  async function handleMacroSave(
    e: CustomEvent<{ macroId: string; fields: Record<string, string | number | string[] | null> }>,
  ) {
    // §16.2.6 c3b: intercept macro edits into the staging buffer
    // when live-preview is ON. Same shape as handleSave (empty-
    // fields skip + shallow-copy fields).
    if (livePreviewOn) {
      const fieldKeys = Object.keys(e.detail.fields);
      if (fieldKeys.length === 0) return;
      _interceptToOpLog({
        kind: 'patchMacro',
        macroId: e.detail.macroId,
        fields: { ...e.detail.fields },
      });
      return;
    }
    await withSaveLifecycle(
      () => patchMacro(slug, e.detail.macroId, e.detail.fields, updatedAt),
      'saved',
    );
  }

  // §16.2.4 c2 — atomic block retype (PATCH /blocks/retype).
  async function handleRetype(
    e: CustomEvent<{
      ref: ExpandedRef;
      newStrategy: string | null;
      newSource: string | null;
      newFields: Record<string, string | number | string[] | null>;
    }>,
  ) {
    const address = refToAddress(e.detail.ref);
    if (address === null) {
      saveError = 'cannot retype this block (no storage address)';
      conflict = false;
      exportMsg = null;
      return;
    }
    // §16.2.6 c3d: intercept retype when live-preview is ON.
    if (livePreviewOn) {
      _interceptToOpLog({
        kind: 'retypeBlock',
        address: { ...address },
        newStrategy: e.detail.newStrategy,
        newSource: e.detail.newSource,
        newFields: { ...e.detail.newFields },
      });
      return;
    }
    await withSaveLifecycle(
      () => retypeBlock(
        slug,
        address,
        e.detail.newStrategy,
        e.detail.newSource,
        e.detail.newFields,
        updatedAt,
      ),
      'retype landed',
    );
  }

  // §16.2.5 c2 — section CRUD + structural-edit + move handlers.
  // Each is a thin wrapper over `withSaveLifecycle` (extracted in
  // §16.2.4 c2 review #5) for uniform 409 / refetch / banner flow.

  async function handleSectionCreate(
    e: CustomEvent<{ id: string; name: string | null }>,
  ) {
    // §16.2.6 c3c: intercept section creates when live-preview is
    // ON. EditorCanvas's Add Section input already validates the
    // id format before emitting; the page coerces empty-string
    // names to null per c3c review #1 fix #5 (matches the backend
    // contract — name absent vs name='' are semantically the same
    // "operator skipped the name field").
    if (livePreviewOn) {
      const name =
        e.detail.name && e.detail.name.trim() ? e.detail.name : null;
      _interceptToOpLog({
        kind: 'sectionCreate',
        sectionId: e.detail.id,
        name,
      });
      return;
    }
    await withSaveLifecycle(
      () => createSection(slug, e.detail.id, e.detail.name, updatedAt),
      'created',
    );
  }

  async function handleSectionRename(
    e: CustomEvent<{ sectionId: string; name: string }>,
  ) {
    // §16.2.6 c3b: intercept section renames into the staging
    // buffer when live-preview is ON. SectionHeader's rename input
    // only emits on non-empty + dirty commits so the page-level
    // intercept trusts the payload (no empty-name guard needed).
    if (livePreviewOn) {
      _interceptToOpLog({
        kind: 'renameSection',
        sectionId: e.detail.sectionId,
        name: e.detail.name,
      });
      return;
    }
    await withSaveLifecycle(
      () => renameSection(slug, e.detail.sectionId, e.detail.name, updatedAt),
      'renamed',
    );
  }

  async function handleSectionDelete(
    e: CustomEvent<{ sectionId: string }>,
  ) {
    // §16.2.6 c3c: intercept section deletes. applyEdit mirrors the
    // §16.2.5 D2 auto-_unsorted-bin behavior locally (non-empty
    // section's blocks re-home to _unsorted) so the preview
    // reflects the actual post-delete state.
    if (livePreviewOn) {
      _interceptToOpLog({
        kind: 'sectionDelete',
        sectionId: e.detail.sectionId,
      });
      return;
    }
    await withSaveLifecycle(
      () => deleteSection(slug, e.detail.sectionId, updatedAt),
      'deleted',
    );
  }

  async function handleSectionsReorder(
    e: CustomEvent<{ layoutOrder: string[] }>,
  ) {
    // §16.2.6 c3c: intercept sections reorder. EditorCanvas's drag
    // handler already validates the new order has the same id set
    // before emitting, so the page trusts the payload (defensive
    // mismatch check still lives in applyEdit).
    if (livePreviewOn) {
      _interceptToOpLog({
        kind: 'reorderSections',
        layoutOrder: [...e.detail.layoutOrder],
      });
      return;
    }
    await withSaveLifecycle(
      () => reorderSections(slug, e.detail.layoutOrder, updatedAt),
      'reordered',
    );
  }

  async function handleBlockMove(
    e: CustomEvent<{
      ref: ExpandedRef;
      targetSectionId: string;
      targetIndex: number | null;
    }>,
  ) {
    const address = refToAddress(e.detail.ref);
    if (address === null) {
      saveError = 'cannot move this block (no storage address)';
      conflict = false;
      exportMsg = null;
      return;
    }
    // §16.2.6 c3d: intercept block move when live-preview is ON.
    if (livePreviewOn) {
      _interceptToOpLog({
        kind: 'moveBlock',
        address: { ...address },
        targetSectionId: e.detail.targetSectionId,
        targetIndex: e.detail.targetIndex,
      });
      return;
    }
    await withSaveLifecycle(
      () => moveBlock(
        slug, address, e.detail.targetSectionId,
        e.detail.targetIndex, updatedAt,
      ),
      'moved',
    );
  }

  async function handleStructuralEdit(
    e: CustomEvent<{ edit: StructuralBlockEdit }>,
  ) {
    // §16.2.6 c3d-follow-up: intercept structural edit when
    // live-preview is ON. Shallow-copy the edit payload so the
    // op log doesn't hold a reference the caller could mutate.
    if (livePreviewOn) {
      _interceptToOpLog({
        kind: 'structuralBlockEdit',
        edit: { ...e.detail.edit },
      });
      return;
    }
    // c2 review #10: name the op in the refetch-failure prefix so a
    // refresh blip after a successful structural edit tells the
    // operator WHICH mutation landed ('resized slot 3' vs
    // 'removed slot 5' vs 'added slot 2'). The generic prior
    // 'applied' was indistinct + the recovery path depends on op.
    const verb = ({
      size: `resized slot ${e.detail.edit.slot_index}`,
      add: `added slot ${e.detail.edit.slot_index}`,
      remove: `removed slot ${e.detail.edit.slot_index}`,
    } as Record<string, string>)[e.detail.edit.op] ?? 'applied structural edit';
    await withSaveLifecycle(
      () => structuralBlockEdit(slug, e.detail.edit, updatedAt),
      verb,
    );
  }

  async function reloadAfterConflict() {
    conflict = false;
    try {
      await loadSidecar();
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    }
  }
</script>

<svelte:head>
  <title>Visualise — {template?.name ?? slug} — Station Studio</title>
</svelte:head>

<!-- §16.2.6 c3e c3 review J1: ESC anywhere on the page dismisses
     the Retry/Drop modal. The handler self-guards on
     `showRetryModal` so this is a no-op when no modal is open. -->
<svelte:window on:keydown={handleModalKeydown} />

<main id="main-content" tabindex="-1">
  <!--
    §16.20 c2 — narrow-viewport gate. The visual block editor
    (canvas + chip drag-drop + DetailPanel + TracePane) is
    fundamentally a desktop affordance: drag targets are too
    small for touch, chip flows wrap unpredictably below 40rem,
    and the TracePane needs the side-by-side layout. Below
    40rem the editor's <div class="visualise-desktop"> is
    hidden via CSS and the operator sees this notice with a
    back-link to the station detail page. Above 40rem the
    notice is hidden and the editor renders normally.

    Cost acknowledged in §16.20 c1: the editor's onMount still
    runs below 40rem (mount cost paid for invisible content);
    if a phone-user profile shows it matters, an
    early-return guard at the top of onMount is the fix.
  -->
  <NarrowScreenNotice
    backHref="/stations/{encodeURIComponent(slug)}"
    backLabel="← Back to {slug}"
    heading="Visual editor needs a wider screen"
    body="The drag-and-drop block editor is built for desktop layouts and doesn't translate to phone-width viewports. Open this page on a screen 640px wide or larger to edit; the station detail page works at any size."
    testId="visualise-narrow-notice"
  />

  <div class="visualise-desktop">
  <p class="crumbs">
    <a href="/stations/{encodeURIComponent(slug)}">← {slug}</a>
  </p>

  <h1>Visualise template</h1>

  {#if loading}
    <p>Loading…</p>
  {:else if loadError}
    <p class="error" data-testid="visualise-error">{loadError}</p>
  {:else if template && sidecar}
    <p class="slug">{template.station} — {template.name}</p>

    {#if sidecarError}
      <!--
        Decision E warning banner. The committed sidecar failed
        validation; server fell back to the synthesized trivial one
        + populated sidecar_error. Operator can keep working while
        figuring out what broke.
      -->
      <div class="sidecar-warning" data-testid="sidecar-warning">
        <strong>Warning:</strong> the committed
        <code>stations/{slug}.editor.json</code> is malformed —
        falling back to the trivial view.
        <pre>{sidecarError}</pre>
        Fix the file or regenerate it with
        <code>make derive-sidecar STATION={slug}</code>.
      </div>
    {/if}

    <!-- §16.2.3 c2 — edit affordances: export-to-disk + edit status. -->
    <div class="editor-actions">
      <button
        type="button"
        class="export"
        data-testid="export-template"
        on:click={handleExport}
      >
        Export to disk
      </button>
      <!-- §16.2.6 c2 — live-preview toggle. Default OFF preserves
           §16.2.5 mental model; ON enables debounced trace pane on
           the right of the canvas. Persists per-station to
           localStorage. -->
      <label class="live-preview-toggle" data-testid="live-preview-toggle">
        <input
          type="checkbox"
          checked={livePreviewOn}
          on:change={toggleLivePreview}
          data-testid="live-preview-checkbox"
        />
        Live preview
      </label>
      <!-- §16.2.4.t1 — macro palette toggle. Opens the browseable
           macro list as a drawer above the trace pane area. Per-
           station persistence (mirrors Live preview's localStorage
           pattern). -->
      <button
        type="button"
        class="palette-toggle"
        data-testid="macro-palette-toggle"
        aria-pressed={paletteOpen}
        on:click={toggleMacroPalette}
      >
        {paletteOpen ? 'Hide macros' : 'Show macros'}
      </button>
      <!-- §16.2.6 c3 — Save / Discard controls for staged edits.
           Visible only when toggle ON + opLog has at least one
           edit (no Save target otherwise). The button labels
           include the count so the operator sees what they're
           committing. -->
      {#if livePreviewOn && opLog.length > 0}
        <button
          type="button"
          class="staged-save"
          data-testid="staged-save"
          disabled={saving}
          on:click={handleStagedSave}
        >
          {saving ? 'Saving…' : `Save ${opLog.length} edit${opLog.length === 1 ? '' : 's'}`}
        </button>
        <button
          type="button"
          class="staged-discard"
          data-testid="staged-discard"
          disabled={saving}
          on:click={handleDiscard}
        >
          Discard
        </button>
      {/if}
      {#if exportMsg}
        <span class="export-msg" data-testid="export-msg">{exportMsg}</span>
      {/if}
    </div>

    {#if conflict}
      <div class="conflict" data-testid="conflict-banner">
        This station changed elsewhere (another tab or the edit route).
        <button type="button" data-testid="conflict-reload" on:click={reloadAfterConflict}>
          Reload latest
        </button>
      </div>
    {/if}
    {#if saveError}
      <p class="error" data-testid="save-error">{saveError}</p>
    {/if}

    <!-- §16.2.6 c3e c3 — Retry/Drop partial-failure modal. Shown
         when handleStagedSave stops mid-replay on a non-conflict
         error. Three actions matching the three reasonable
         operator responses; the saveError banner above stays
         visible underneath so the operator sees what failed. -->
    {#if showRetryModal}
      <!-- §16.2.6 c3e c3 review J6: clicking the backdrop is
           equivalent to Cancel — a common operator convention.
           stopPropagation on the dialog so clicks INSIDE it
           don't bubble out and re-trigger Cancel. -->
      <div
        class="retry-modal-backdrop"
        data-testid="retry-modal-backdrop"
        on:click={handleCancelRetry}
        on:keydown|self={() => { /* surface area only */ }}
        role="presentation"
      >
        <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="retry-modal-title"
          class="retry-modal"
          data-testid="retry-modal"
          on:click|stopPropagation
          on:keydown|stopPropagation
        >
          <h2 id="retry-modal-title">Save failed mid-replay</h2>
          <p>{saveError}</p>
          <p class="retry-modal__hint">
            {opLog.length} staged edit{opLog.length === 1 ? '' : 's'} remain in the queue.
            Choose how to proceed:
          </p>
          <div class="retry-modal__actions">
            <button
              type="button"
              class="primary"
              data-testid="retry-modal-retry"
              disabled={saving}
              on:click={handleRetry}
              use:autofocus
            >Retry the failed edit</button>
            <!-- §16.2.6.t2 c2: hide Drop on sentinel failedAtIdx === 0.
                 Composition failures (kind === '_finalize') + empty
                 batches don't point at a specific opLog entry, so
                 there's no meaningful "drop the failed edit" action.
                 Operator picks Retry or Cancel (and fixes the
                 composition issue inline). -->
            {#if failedAtIdx > 0}
              <button
                type="button"
                data-testid="retry-modal-drop"
                disabled={saving}
                on:click={handleDrop}
              >Drop &amp; continue</button>
            {/if}
            <button
              type="button"
              data-testid="retry-modal-cancel"
              disabled={saving}
              on:click={handleCancelRetry}
            >Cancel</button>
          </div>
        </div>
      </div>
    {/if}

    <!--
      §16.2.3 — editable visual block canvas. The DetailPanel's `save`
      event bubbles up here (via EditorCanvas) to the PATCH call.

      §16.2.6 c2: when livePreviewOn, wrap the canvas + <TracePane>
      in a flex row so the trace appears to the right of the canvas
      (D2 side panel). Toggle OFF returns to the centered max-width
      layout untouched.
    -->
    <div
      class="editor-region"
      class:has-trace={livePreviewOn || paletteOpen}
      data-testid="editor-region"
    >
      <div class="canvas-col">
        <EditorCanvas
          {template}
          {sidecar}
          on:save={handleSave}
          on:macroSave={handleMacroSave}
          on:retype={handleRetype}
          on:sectionCreate={handleSectionCreate}
          on:sectionRename={handleSectionRename}
          on:sectionDelete={handleSectionDelete}
          on:sectionsReorder={handleSectionsReorder}
          on:blockMove={handleBlockMove}
          on:structuralEdit={handleStructuralEdit}
          on:detailOpen={(e) => (detailPanelOpen = e.detail.open)}
        />
      </div>
      <!-- §16.2.6 c3e c2 — D2(b) DetailPanel ↔ TracePane mutual-
           exclusivity. TracePane hides while DetailPanel is open so
           it doesn't compete for the right-side real estate. The
           trace data + ongoing preview job keep running in the
           background (no _cancelPreview on DetailPanel open) so the
           operator gets a fresh trace as soon as they close.
           c3e c2 review H4: use CSS hide instead of `{#if}` unmount
           — TracePane reactively re-renders as `previewTrace`
           updates even while hidden, so closing DetailPanel shows
           the latest trace with no remount cost. -->
      {#if livePreviewOn || paletteOpen}
        <div
          class="trace-col"
          class:trace-col--hidden={detailPanelOpen}
          data-testid="trace-col"
        >
          <!-- §16.2.4.t1 — macro palette sits ABOVE the trace pane in
               the right column. Independent of livePreviewOn — the
               column auto-renders when either feature is active. -->
          {#if paletteOpen}
            <MacroPalette
              sidecar={stagedSidecar ?? sidecar}
              on:select={handleMacroPaletteSelect}
            />
          {/if}
          {#if livePreviewOn}
            <TracePane
              trace={previewTrace}
              previewing={previewing}
              errorMessage={previewError}
              weekValue={previewWeek}
              showDiff={showDiff}
              savedTrace={savedTrace}
              savedTraceError={savedTraceError}
              compareNextN={compareNextN}
              weekDiffSummary={weekDiffSummary}
              multiWeekFetching={multiWeekFetching}
              compareNextNMax={COMPARE_NEXT_N_MAX}
              template={template}
              savedSidecar={sidecar}
              stagedSidecar={stagedSidecar}
              revertableSlots={revertableSlots}
              opLog={opLog}
              on:select={handleTraceSelect}
              on:weekChange={handleWeekChange}
              on:showDiffChange={handleShowDiffChange}
              on:compareNextNChange={handleCompareNextNChange}
              on:revertSlot={handleRevertSlot}
            />
          {/if}
        </div>
      {/if}
    </div>
  {/if}
  </div><!-- /.visualise-desktop -->
</main>

<style>
  /* §16.20 c2 — hide the desktop editor below the mobile
     breakpoint (NarrowScreenNotice handles that range). The
     CSS-only gate avoids any JS branching in this 2500-line
     file; the editor still mounts on phones (acknowledged
     mount-cost in c1) but it's invisible until viewport
     grows past 40rem. Above 40rem the notice is hidden by
     its own internal rule and the editor renders. */
  .visualise-desktop {
    display: block;
  }
  @media (max-width: 40rem) {
    .visualise-desktop {
      display: none;
    }
  }

  main {
    font-family: system-ui, sans-serif;
    max-width: 900px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  /* §16.2.6 c2: when live-preview is on, the main relaxes its
     max-width so the trace pane has room on the right. */
  main:has(.editor-region.has-trace) {
    max-width: 1400px;
  }
  .editor-region {
    /* Toggle OFF: canvas-col is the only child; flexbox is a no-op
       (single block-level child). Toggle ON: canvas-col + trace-col
       sit side-by-side. */
    display: flex;
    gap: 1rem;
    align-items: stretch;
  }
  .canvas-col {
    /* Canvas gets the rest of the available space when trace is
       visible; full width when trace is hidden. */
    flex: 1;
    min-width: 0;
  }
  .trace-col {
    /* D2: ~30% viewport on a 1400px max main. The pane internally
       handles its own scrolling. Max-height matches what the canvas
       would visually occupy on a typical edit session. */
    width: 30%;
    min-width: 280px;
    max-height: 80vh;
    border-radius: 4px;
    overflow: hidden;
    border: 1px solid var(--border, #ddd);
    /* §16.2.4.t1 — column now hosts BOTH the macro palette (when
       open) AND the trace pane. Flex column with a small gap so the
       two visually separate; single-child case (palette OR trace
       alone) is unaffected — flex falls back to natural block flow. */
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .trace-col--hidden {
    /* §16.2.6 c3e c2 review H4: hide via display:none instead of
       unmounting via {#if}. The component stays in the DOM, reacts
       to previewTrace updates while DetailPanel is open, and shows
       the latest trace immediately when DetailPanel closes — no
       remount cost. display:none also drops the element from the
       a11y tree, matching the visibility-hidden semantic. */
    display: none;
  }
  /* §16.2.6 c3e c3 — Retry/Drop modal styles. Centered overlay on
     a semi-transparent backdrop; modal has a small drop shadow to
     separate from the page. Action buttons live in a row with the
     primary (Retry) emphasized. */
  .retry-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }
  .retry-modal {
    background: var(--surface, #fff);
    border-radius: 6px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.18);
    padding: 1.25rem 1.5rem;
    max-width: 480px;
    width: calc(100% - 2rem);
  }
  .retry-modal h2 {
    margin: 0 0 0.5rem 0;
    font-size: 1.05rem;
  }
  .retry-modal p {
    margin: 0.25rem 0;
    font-size: 0.9rem;
  }
  .retry-modal__hint {
    color: var(--text-muted, #555);
    margin-top: 0.75rem;
  }
  .retry-modal__actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 1rem;
    justify-content: flex-end;
  }
  .retry-modal__actions button {
    cursor: pointer;
  }
  .retry-modal__actions button.primary {
    background: var(--accent, #3a80c0);
    color: #fff;
    border: 1px solid var(--accent, #3a80c0);
  }
  .retry-modal__actions button[disabled] {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .live-preview-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    color: var(--text-muted, #555);
    cursor: pointer;
    user-select: none;
  }
  .live-preview-toggle input[type='checkbox'] {
    margin: 0;
    cursor: pointer;
  }
  /* §16.2.4.t1 — macro palette toggle button. Mirrors the
     `.live-preview-toggle` font + size so the two toolbar
     affordances visually balance. aria-pressed handles the
     active-state semantics; CSS just dims the inactive state. */
  .palette-toggle {
    padding: 0.4rem 0.8rem;
    background: transparent;
    border: 1px solid var(--color-panel-border, #ccc);
    border-radius: 4px;
    cursor: pointer;
    font: inherit;
    font-size: 0.85rem;
    color: var(--text, #222);
  }
  .palette-toggle[aria-pressed='true'] {
    background: var(--color-row-hover, #eef);
    border-color: var(--color-focus, #3b82f6);
  }
  /* §16.2.6 c3 — staged-edit Save / Discard controls. */
  .staged-save {
    padding: 0.4rem 0.9rem;
    background: var(--accent, #2c7a7b);
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .staged-save:hover:not([disabled]) {
    background: color-mix(in srgb, var(--accent, #2c7a7b) 85%, black);
  }
  .staged-save[disabled] {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .staged-discard {
    padding: 0.4rem 0.9rem;
    background: transparent;
    color: var(--text-muted, #555);
    border: 1px solid var(--border, #ddd);
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .staged-discard:hover:not([disabled]) {
    background: var(--bg-hover, #f0f0f0);
  }
  .staged-discard[disabled] {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .editor-actions {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin: 0.75rem 0;
  }
  .export {
    padding: 0.4rem 0.9rem;
    background: #2c3e50;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .export:hover {
    background: #1a252f;
  }
  .export-msg {
    color: #2e7d32;
    font-size: 0.85rem;
  }
  .conflict {
    background: #fff8e1;
    border: 1px solid #f0c36d;
    border-radius: 4px;
    padding: 0.6rem 0.9rem;
    margin: 0.5rem 0;
    font-size: 0.9rem;
  }
  .conflict button {
    margin-left: 0.5rem;
    padding: 0.2rem 0.6rem;
    cursor: pointer;
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
  .sidecar-warning {
    background: #fff8e1;
    border: 1px solid #f0c36d;
    border-radius: 4px;
    padding: 0.75rem 1rem;
    margin: 1rem 0;
    font-size: 0.9rem;
  }
  .sidecar-warning pre {
    background: #fffaf0;
    padding: 0.4rem 0.6rem;
    border-radius: 3px;
    overflow-x: auto;
    font-size: 0.85rem;
    margin: 0.5rem 0;
  }
  .error {
    color: crimson;
  }
  code {
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
  }

  /* §16.2.6.t3 — chip jump-flash. The class is added by
     handleTraceSelect on the target chip element + auto-strips
     after JUMP_FLASH_MS (800ms). `:global` because the class is
     added to a <Chip>-rendered element from outside the chip's
     scoped style context (the page's querySelector reaches into
     the canvas's chip subtree). The outline pulses using the
     chip's existing --chip-color CSS variable so the flash
     matches the chip's identity. */
  :global(.chip.chip--jump-flash) {
    animation: chip-jump-flash 800ms ease-out;
    /* Layer the outline above neighbours so the pulse isn't
       clipped by adjacent chip outlines. */
    position: relative;
    z-index: 1;
  }
  @keyframes chip-jump-flash {
    0% {
      outline: 3px solid var(--chip-color, #2c7a7b);
      outline-offset: 2px;
    }
    100% {
      outline: 3px solid transparent;
      outline-offset: 2px;
    }
  }
  /* Reduced-motion variant: hold a static outline for the same
     duration instead of animating. Motion-sensitive operators still
     see the cue without the keyframe pulse. */
  @media (prefers-reduced-motion: reduce) {
    :global(.chip.chip--jump-flash) {
      animation: none;
      outline: 3px solid var(--chip-color, #2c7a7b);
      outline-offset: 2px;
    }
  }
</style>
