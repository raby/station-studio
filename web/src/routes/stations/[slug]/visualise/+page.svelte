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
  import { onDestroy, onMount } from 'svelte';
  import { page } from '$app/stores';
  import {
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
    startPreviewJob,
    structuralBlockEdit,
    watchJob,
    type BlockAddress,
    type StructuralBlockEdit,
  } from '$lib/api';
  import {
    parseEditorSidecarResponse,
    type ExpandedRef, type Sidecar, type StationTemplate,
  } from '$lib/editor';
  import { applyEdits, type Edit } from '$lib/edit-ops';
  import EditorCanvas from '$lib/components/editor/EditorCanvas.svelte';
  import TracePane from '$lib/components/editor/TracePane.svelte';

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
  let previewTrace: Trace | null = null;
  let previewing = false;
  let previewError: string | null = null;
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
  //
  // **Still falls through to immediate-PATCH when toggle ON** (c3c
  // extends both applyEdit + the handler interception):
  //   - sectionCreate / sectionDelete / reorderSections
  //   - moveBlock / retypeBlock / structuralBlockEdit
  //
  // The opLog data structure carries all kinds; only the dispatch
  // wiring needs adding per kind.
  //
  // saveError surfacing during Save replay reuses the existing
  // `saveError` banner — the Retry/Drop modal lands in c3c. On any
  // replay error the loop stops + the operator sees which edit
  // failed; remaining edits stay in opLog so a fix-and-retry
  // sequence works.
  let opLog: Edit[] = [];
  let saving = false;

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
          1,
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

  function toggleLivePreview() {
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

  onDestroy(() => {
    _cancelPreview();
  });

  async function loadSidecar() {
    const r = await getStationSidecar(slug);
    template = r.template;
    sidecar = r.sidecar;
    sidecarError = r.sidecar_error;
    updatedAt = r.updated_at;
  }

  onMount(async () => {
    if (!slug) {
      loadError = 'missing slug';
      loading = false;
      return;
    }
    // Hydrate the live-preview toggle from localStorage BEFORE the
    // sidecar loads — so a returning operator's preferred mode is
    // already active when the canvas renders. No preview job fires
    // here; the operator's first edit triggers _schedulePreview if
    // the toggle is ON.
    livePreviewOn = readLivePreview(slug);
    try {
      // §16.2.2.t1 — getStationSidecar validates the response shape
      // before return. Both ApiError (network/4xx) and
      // EditorMetadataError (shape) carry `.message`.
      await loadSidecar();
      // If the operator's saved preference is "live preview ON",
      // fire one preview right after the sidecar lands so the trace
      // pane shows actual content instead of the empty-state nudge.
      if (livePreviewOn) _schedulePreview();
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

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
  function _isSidecarMutationResponse(
    r: unknown,
  ): r is { sidecar: Sidecar; template: Record<string, unknown>; updated_at: string | null } {
    if (r === null || typeof r !== 'object') return false;
    if (!('sidecar' in r)) return false;
    const s = (r as { sidecar: unknown }).sidecar;
    return s !== null && typeof s === 'object' && !Array.isArray(s);
  }

  async function withSaveLifecycle(
    doPatch: () => Promise<unknown>,
    refetchVerbPrefix: string,
  ): Promise<void> {
    saveError = null;
    conflict = false;
    exportMsg = null;
    let result: unknown;
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
        template = parsed.template;
        sidecar = parsed.sidecar;
        updatedAt = parsed.updated_at;
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
      // Slice-review c1-rescue #1: a successful PATCH means the
      // server-validated sidecar is back in sync. Clear any stale
      // warning from a prior GET that landed with sidecar_error
      // populated — `loadSidecar()` would have done the same.
      sidecarError = null;
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
      // Review #1 fix #9: defensive skip when there are no actual
      // field changes. DetailPanel COULD emit a save with empty
      // fields (operator opens panel + clicks Save without
      // editing); pre-fix the empty edit was silently queued + the
      // button label showed "Save 1 edit" for nothing.
      const fieldKeys = Object.keys(e.detail.fields);
      if (fieldKeys.length === 0) return;
      // Review #1 fix #3: shallow-copy the fields dict + the
      // address into the opLog entry. The DetailPanel emits + then
      // doesn't retain a reference today, but if a future change
      // adds a 'reset form' or 'remove this field' affordance that
      // mutates the same object, the staged op would silently
      // drift. Cheap insurance.
      opLog = [
        ...opLog,
        {
          kind: 'patchBlock',
          address: { ...address },
          fields: { ...e.detail.fields },
        },
      ];
      saveError = null;
      conflict = false;
      exportMsg = null;
      // Schedule a debounced preview against the new staged state.
      _schedulePreview();
      return;
    }
    await withSaveLifecycle(
      () => patchBlock(slug, address, e.detail.fields, updatedAt),
      'saved',
    );
  }

  // §16.2.6 c3 — replay the opLog as sequential PATCHes. On the
  // first failure, stop + surface the error inline. Remaining edits
  // stay in opLog so a fix-and-retry sequence works. The Retry/Drop
  // modal (with explicit "drop the un-attempted edits" semantics)
  // lands in c3b.
  async function handleStagedSave() {
    if (opLog.length === 0) return;
    saving = true;
    saveError = null;
    conflict = false;
    exportMsg = null;
    // Review #1 fix #1: capture the original count + track which
    // edit (1-indexed) we're currently replaying so the error
    // banner can say "edit 3 of 5" instead of the pre-fix
    // nonsense `${opLog.length}/${opLog.length + opLog.length}`
    // (which expanded to `K/2K` — visually a fraction but
    // semantically wrong).
    const totalEdits = opLog.length;
    let currentEditIdx = 0;
    try {
      while (opLog.length > 0) {
        const op = opLog[0];
        currentEditIdx += 1;
        try {
          // §16.2.6 c3b: dispatch on op.kind for the three currently-
          // intercepted edits. The other 6 kinds (sectionCreate /
          // Delete / reorderSections / moveBlock / retypeBlock /
          // structuralEdit) still hit immediate-PATCH when toggle
          // is ON — c3c extends this dispatch + the matching
          // handler intercepts.
          switch (op.kind) {
            case 'patchBlock':
              await patchBlock(slug, op.address, op.fields, updatedAt);
              break;
            case 'patchMacro':
              await patchMacro(slug, op.macroId, op.fields, updatedAt);
              break;
            case 'renameSection':
              await renameSection(slug, op.sectionId, op.name, updatedAt);
              break;
          }
        } catch (err) {
          if (err instanceof BlockPatchConflict) {
            conflict = true;
            // c3 MVP: stop on conflict, surface the existing
            // conflict banner, leave the rest in opLog. The
            // Retry/Drop modal lands in c3c.
            return;
          }
          saveError =
            `Save failed at staged edit ${currentEditIdx} of ${totalEdits}: ` +
            (err instanceof Error ? err.message : String(err));
          return;
        }
        // Refresh updated_at from the latest fetch so the next
        // PATCH in the replay carries the freshest token. We
        // refetch + drop the just-replayed op together so a
        // failure mid-replay leaves consistent state.
        await loadSidecar();
        opLog = opLog.slice(1);
      }
      // All edits applied. Trigger a preview refresh.
      _schedulePreview();
    } finally {
      saving = false;
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
    opLog = [];
    saveError = null;
    conflict = false;
    try {
      await loadSidecar();
      _schedulePreview();
    } catch (err) {
      saveError = err instanceof Error ? err.message : String(err);
    }
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
    // when live-preview is ON. Same shape as the chip-edit
    // intercept in handleSave: empty-fields skip + shallow-copy +
    // schedule a debounced preview against the new staged state.
    if (livePreviewOn) {
      const fieldKeys = Object.keys(e.detail.fields);
      if (fieldKeys.length === 0) return;
      opLog = [
        ...opLog,
        {
          kind: 'patchMacro',
          macroId: e.detail.macroId,
          fields: { ...e.detail.fields },
        },
      ];
      saveError = null;
      conflict = false;
      exportMsg = null;
      _schedulePreview();
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
    await withSaveLifecycle(
      () => createSection(slug, e.detail.id, e.detail.name, updatedAt),
      'created',
    );
  }

  async function handleSectionRename(
    e: CustomEvent<{ sectionId: string; name: string }>,
  ) {
    // §16.2.6 c3b: intercept section renames into the staging
    // buffer when live-preview is ON. Same shape as the chip-edit
    // + macro-edit intercepts (skip if name is empty / unchanged
    // would be ideal but the SectionHeader rename input only emits
    // on non-empty + dirty commits, so the page-level intercept
    // can trust the payload).
    if (livePreviewOn) {
      opLog = [
        ...opLog,
        {
          kind: 'renameSection',
          sectionId: e.detail.sectionId,
          name: e.detail.name,
        },
      ];
      saveError = null;
      conflict = false;
      exportMsg = null;
      _schedulePreview();
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
    await withSaveLifecycle(
      () => deleteSection(slug, e.detail.sectionId, updatedAt),
      'deleted',
    );
  }

  async function handleSectionsReorder(
    e: CustomEvent<{ layoutOrder: string[] }>,
  ) {
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

<main>
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
      class:has-trace={livePreviewOn}
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
        />
      </div>
      {#if livePreviewOn}
        <div class="trace-col">
          <TracePane
            trace={previewTrace}
            previewing={previewing}
            errorMessage={previewError}
          />
        </div>
      {/if}
    </div>
  {/if}
</main>

<style>
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
</style>
