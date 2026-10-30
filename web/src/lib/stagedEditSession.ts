/**
 * A staged-edit session for one station: load its template, queue edits,
 * preview one week as saved and as edited, then save the queue in one
 * batch or discard it.
 *
 * Written for the week page's song-list editor (and later the editor's
 * Songs tab). It's a Svelte store (`subscribe`), so runes components
 * read it as `$session` and so can older-syntax ones. The station editor
 * page (stations/[slug]/visualise) keeps its own wiring for now, built
 * on the same pieces in $lib/stagedEdits.
 *
 * Previews: the station allows one preview job at a time (a new one
 * cancels the running one server-side), so a refresh runs the saved
 * week first, then the edited week, never both at once. With nothing
 * queued, the edited week IS the saved week and no second job runs.
 */
import { writable, type Readable } from 'svelte/store';
import {
  applyBatch,
  BlockPatchConflict,
  cancelJob,
  getStationSidecar,
  SidecarBatchFailure,
  type Trace,
} from '$lib/api';
import { applyEdits, type Edit } from '$lib/edit-ops';
import { parseEditorSidecarResponse, type Sidecar, type StationTemplate } from '$lib/editor';
import {
  friendlyPreviewError,
  readStagedEdits,
  runPreview,
  stagedEditsPayload,
  writeStagedEdits,
} from '$lib/stagedEdits';

export interface StagedEditState {
  slug: string;
  loading: boolean;
  /** Set when the station couldn't be loaded. */
  loadError: string | null;
  template: StationTemplate | null;
  sidecar: Sidecar | null;
  /** The station's version token; saves send it so a change made
   *  elsewhere in the meantime is a conflict, not an overwrite. */
  updatedAt: string | null;
  sidecarError: string | null;

  /** Queued edits, oldest first. Nothing is saved until `save()`. */
  opLog: Edit[];
  /** The template and sidecar with the queue applied (the loaded ones
   *  when the queue is empty). */
  staged: { template: StationTemplate | null; sidecar: Sidecar | null };
  /** Edits from an earlier visit, queued against an older version of the
   *  station, waiting for acceptRecovered() or discardRecovered(). Edits
   *  queued against the current version come back without asking. */
  recovered: { count: number } | null;

  saving: boolean;
  saveError: string | null;
  /** The station changed elsewhere since it was loaded: reload first. */
  conflict: boolean;
  /** 1-based index of the queued edit a failed save stopped at (0 when
   *  the failure wasn't one edit's), for dropFailed(). */
  failedAtIdx: number;

  week: number;
  /** The week as saved. */
  savedTrace: Trace | null;
  /** The week with the queued edits (the saved trace when none). */
  stagedTrace: Trace | null;
  previewing: boolean;
  previewError: string | null;
}

export interface StagedEditSession extends Readable<StagedEditState> {
  /** Load the station, recover queued edits, preview. Reloading (after a
   *  conflict, say) keeps the queue and re-stamps it with the new version. */
  load(): Promise<void>;
  /** Queue an edit and refresh the edited preview. */
  stage(op: Edit): void;
  /** Remove one queued edit (0-based). */
  unstage(index: number): void;
  /** Save the whole queue in one batch. True when it landed. */
  save(): Promise<boolean>;
  /** After a failed save: drop the edit it stopped at, then save the rest. */
  dropFailed(): Promise<boolean>;
  /** Drop every queued edit and reload the saved station. */
  discard(): Promise<void>;
  acceptRecovered(): void;
  discardRecovered(): void;
  setWeek(week: number): void;
  /** Stop any preview job and timers; call when the page goes away. */
  destroy(): void;
}

export interface SessionOptions {
  week?: number;
  /** Wait this long after the last change before previewing. */
  debounceMs?: number;
  /** Preview only while edits are queued (the week page, which already
   *  shows the generated week). Otherwise a preview runs on load too. */
  previewOnlyWhenEdited?: boolean;
}

const errorText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function createStagedEditSession(slug: string, opts: SessionOptions = {}): StagedEditSession {
  const debounceMs = opts.debounceMs ?? 500;
  const onlyWhenEdited = opts.previewOnlyWhenEdited ?? false;
  let s: StagedEditState = {
    slug,
    loading: true,
    loadError: null,
    template: null,
    sidecar: null,
    updatedAt: null,
    sidecarError: null,
    opLog: [],
    staged: { template: null, sidecar: null },
    recovered: null,
    saving: false,
    saveError: null,
    conflict: false,
    failedAtIdx: 0,
    week: Math.max(1, Math.floor(opts.week ?? 1)),
    savedTrace: null,
    stagedTrace: null,
    previewing: false,
    previewError: null,
  };
  const store = writable<StagedEditState>(s);
  let pendingRecovery: Edit[] = [];
  let generation = 0; // bumped whenever a running refresh's result would be out of date
  let timer: ReturnType<typeof setTimeout> | null = null;
  let jobId: string | null = null;
  let destroyed = false;

  function update(patch: Partial<StagedEditState>): void {
    s = { ...s, ...patch };
    if ('opLog' in patch || 'template' in patch || 'sidecar' in patch) {
      s.staged =
        s.template && s.sidecar && s.opLog.length > 0
          ? (applyEdits(s.template, s.sidecar, s.opLog) as { template: StationTemplate; sidecar: Sidecar })
          : { template: s.template, sidecar: s.sidecar };
    }
    store.set(s);
  }

  function persist(): void {
    writeStagedEdits(slug, stagedEditsPayload(s.opLog, s.updatedAt));
  }

  function setQueue(opLog: Edit[]): void {
    update({ opLog });
    persist();
  }

  // ---- previews --------------------------------------------------------

  function cancelRunning(): void {
    generation += 1;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    if (jobId !== null) {
      const id = jobId;
      jobId = null;
      void cancelJob(id).catch(() => {
        // Idempotent; a network blip here is harmless.
      });
    }
  }

  /** Preview again after `debounceMs`. `savedChanged` also re-runs the
   *  saved week (after a load, save, discard or week change). */
  function schedule(savedChanged: boolean): void {
    if (destroyed) return;
    cancelRunning();
    if (savedChanged) update({ savedTrace: null });
    if (onlyWhenEdited && s.opLog.length === 0) {
      // Nothing to compare: the page shows the week as generated. The
      // saved-week preview is kept for the next edit unless the saved
      // station or the week changed (savedChanged cleared it above).
      update({ stagedTrace: null, previewing: false, previewError: null });
      return;
    }
    const gen = generation;
    timer = setTimeout(() => {
      timer = null;
      void refresh(gen);
    }, debounceMs);
  }

  async function previewOf(template: unknown, sidecar: unknown, gen: number): Promise<Trace | 'stale'> {
    const out = await runPreview(slug, template, sidecar, s.week, {
      isCurrent: () => gen === generation,
      cancelIfStale: true,
      onStart: (id) => {
        jobId = id;
      },
    });
    jobId = null;
    if (out.status === 'stale') return 'stale';
    if (out.status === 'done') {
      if (out.trace === null) throw new Error('preview returned an unexpected shape');
      return out.trace;
    }
    if (out.status === 'error') throw new Error(friendlyPreviewError(out.message));
    // Cancelled server-side or ended without a result: a newer preview
    // took over; its result is the one that counts.
    return 'stale';
  }

  async function refresh(gen: number): Promise<void> {
    if (!s.template || !s.sidecar) return;
    update({ previewing: true, previewError: null });
    try {
      let saved = s.savedTrace;
      if (saved === null) {
        const t = await previewOf(s.template, s.sidecar, gen);
        if (t === 'stale' || gen !== generation) return;
        saved = t;
        update({ savedTrace: t });
      }
      if (s.opLog.length === 0) {
        update({ stagedTrace: saved });
      } else {
        const t = await previewOf(s.staged.template, s.staged.sidecar, gen);
        if (t === 'stale' || gen !== generation) return;
        update({ stagedTrace: t });
      }
      update({ previewing: false });
    } catch (e) {
      if (gen !== generation) return;
      update({ previewing: false, previewError: friendlyPreviewError(errorText(e)) });
    }
  }

  // ---- loading ---------------------------------------------------------

  async function fetchStation(): Promise<void> {
    const r = await getStationSidecar(slug);
    update({
      template: r.template,
      sidecar: r.sidecar,
      updatedAt: r.updated_at,
      sidecarError: r.sidecar_error,
    });
  }

  function recover(): void {
    const persisted = readStagedEdits(slug);
    if (persisted === null) return;
    if (persisted.opLog.length === 0) {
      writeStagedEdits(slug, null);
      return;
    }
    if (persisted.updatedAtSnapshot === s.updatedAt) {
      update({ opLog: persisted.opLog.slice() });
      return;
    }
    pendingRecovery = persisted.opLog.slice();
    update({ recovered: { count: pendingRecovery.length } });
  }

  async function load(): Promise<void> {
    update({ loading: true, loadError: null });
    try {
      await fetchStation();
      if (s.opLog.length > 0) {
        // A reload: the queue is this session's, not an earlier visit's.
        // Keep it against the version just loaded.
        persist();
      } else {
        recover();
      }
      schedule(true);
    } catch (e) {
      update({ loadError: errorText(e) });
    } finally {
      update({ loading: false });
    }
  }

  // ---- edits -----------------------------------------------------------

  function stage(op: Edit): void {
    // Before the station has loaded there's no version to stamp the
    // queue with, and persisting would clear what an earlier visit left.
    if (s.template === null || s.sidecar === null) return;
    update({ saveError: null, conflict: false });
    setQueue([...s.opLog, op]);
    schedule(false);
  }

  function unstage(index: number): void {
    if (index < 0 || index >= s.opLog.length) return;
    setQueue(s.opLog.filter((_, i) => i !== index));
    schedule(false);
  }

  async function save(): Promise<boolean> {
    if (s.opLog.length === 0 || s.saving) return false;
    update({ saving: true, saveError: null, conflict: false, failedAtIdx: 0 });
    // What this save sends. Edits queued while it's on its way stay
    // queued: they were made on top of these, which are now saved.
    const sending = s.opLog;
    try {
      let result;
      try {
        result = await applyBatch(slug, sending, s.updatedAt);
      } catch (e) {
        if (e instanceof BlockPatchConflict) {
          update({ conflict: true });
        } else if (e instanceof SidecarBatchFailure) {
          // The server saves all or nothing, so the queue is intact.
          const at = e.failedAtIdx;
          update({
            failedAtIdx: at,
            saveError:
              at > 0
                ? `Save failed at edit ${at} of ${sending.length}: ${e.message}`
                : `Save failed: ${e.message}`,
          });
        } else {
          update({ saveError: errorText(e) });
        }
        return false;
      }
      try {
        const parsed = parseEditorSidecarResponse({
          template: result.template,
          sidecar: result.sidecar,
          sidecar_error: null,
          updated_at: result.updated_at,
        });
        update({
          template: parsed.template,
          sidecar: parsed.sidecar,
          updatedAt: parsed.updated_at,
          sidecarError: null,
        });
      } catch (e) {
        // Saved, but the reply didn't look right: show the saved station
        // as the server has it.
        update({ saveError: `Saved, but the reply didn't look right: ${errorText(e)}` });
        try {
          await fetchStation();
        } catch {
          // Keep the message above.
        }
      }
      setQueue(s.opLog.slice(sending.length));
      schedule(true);
      return true;
    } finally {
      update({ saving: false });
    }
  }

  async function dropFailed(): Promise<boolean> {
    const at = s.failedAtIdx;
    if (at < 1 || at > s.opLog.length) return false;
    update({ failedAtIdx: 0, saveError: null });
    setQueue(s.opLog.filter((_, i) => i !== at - 1));
    if (s.opLog.length === 0) {
      schedule(false);
      return true;
    }
    return save();
  }

  async function discard(): Promise<void> {
    cancelRunning(); // a preview of the discarded edits mustn't land later
    setQueue([]);
    update({ saveError: null, conflict: false, failedAtIdx: 0, recovered: null });
    pendingRecovery = [];
    try {
      await fetchStation();
    } catch (e) {
      update({ saveError: errorText(e) });
      return;
    }
    schedule(true);
  }

  function acceptRecovered(): void {
    if (s.recovered === null) return;
    update({ recovered: null });
    // Queued against an older version; keep them against this one, so
    // the question isn't asked again next time.
    setQueue(pendingRecovery);
    pendingRecovery = [];
    schedule(false);
  }

  function discardRecovered(): void {
    if (s.recovered === null) return;
    update({ recovered: null });
    pendingRecovery = [];
    writeStagedEdits(slug, null);
  }

  function setWeek(week: number): void {
    const w = Math.max(1, Math.floor(week));
    if (!Number.isFinite(w) || w === s.week) return;
    update({ week: w, savedTrace: null, stagedTrace: null });
    schedule(true);
  }

  function destroy(): void {
    destroyed = true;
    cancelRunning();
  }

  return {
    subscribe: store.subscribe,
    load,
    stage,
    unstage,
    save,
    dropFailed,
    discard,
    acceptRecovered,
    discardRecovered,
    setWeek,
    destroy,
  };
}
