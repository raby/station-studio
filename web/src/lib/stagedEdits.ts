/**
 * Staged edits: what the station editor and the week page's song list
 * share. Edits are queued (the op log) and previewed before anything is
 * saved; the queue survives a reload through localStorage.
 *
 *   - persistence: read/write the queued edits for a station
 *   - runPreview: start a preview job and watch it to the end
 *   - friendlyPreviewError: a preview failure in words for the page
 *
 * Moved out of stations/[slug]/visualise/+page.svelte unchanged in
 * behaviour; that page and the session store (stagedEditSession.ts)
 * both use these.
 */
import {
  cancelJob,
  isPreviewResult,
  startPreviewJob,
  watchJob,
  type Trace,
} from '$lib/api';
import type { Edit } from '$lib/edit-ops';

// ---- persistence -------------------------------------------------------

/** Bump on any breaking change to the persisted Edit shape. Entries with
 *  another version are dropped on read, so a newer page never replays
 *  an edit whose meaning has changed under it. */
export const STAGED_EDITS_SCHEMA_VERSION = 1;

/** Edit kinds a persisted queue may hold; anything else is dropped on
 *  read. Keep in step with the `Edit` union in `$lib/edit-ops`. */
export const KNOWN_EDIT_KINDS: ReadonlySet<string> = new Set([
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

export interface PersistedStagedEdits {
  schemaVersion: number;
  opLog: Edit[];
  /** The station's `updated_at` when the edits were queued. A different
   *  value on recovery means the station changed since. */
  updatedAtSnapshot: string;
}

export const stagedEditsKey = (slug: string): string => `stagedEdits:${slug}`;

/** The queued edits stored for a station, or null when there are none
 *  or the entry is unreadable, from another schema version or the
 *  wrong shape. Unknown edit kinds are filtered out. */
export function readStagedEdits(slug: string): PersistedStagedEdits | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(stagedEditsKey(slug));
    if (raw === null) return null;
    const parsed = JSON.parse(raw) as PersistedStagedEdits;
    if (
      !parsed ||
      typeof parsed !== 'object' ||
      parsed.schemaVersion !== STAGED_EDITS_SCHEMA_VERSION ||
      !Array.isArray(parsed.opLog) ||
      typeof parsed.updatedAtSnapshot !== 'string'
    ) {
      return null;
    }
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

/** Store the queued edits, or remove the entry when `payload` is null or
 *  has no edits. A failed write (quota, privacy mode) is ignored: the
 *  edits stay in memory for this session only. */
export function writeStagedEdits(slug: string, payload: PersistedStagedEdits | null): void {
  if (typeof window === 'undefined') return;
  try {
    const key = stagedEditsKey(slug);
    if (payload === null || payload.opLog.length === 0) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, JSON.stringify(payload));
    }
  } catch {
    // Non-fatal.
  }
}

/** What to persist for a queue: the entry, or null (remove it) when the
 *  queue is empty or there's no `updated_at` to stamp it with. */
export function stagedEditsPayload(
  opLog: Edit[],
  updatedAt: string | null,
): PersistedStagedEdits | null {
  return opLog.length > 0 && updatedAt !== null
    ? { schemaVersion: STAGED_EDITS_SCHEMA_VERSION, opLog, updatedAtSnapshot: updatedAt }
    : null;
}

// ---- preview errors ----------------------------------------------------

/** A preview failure as the page shows it. The server's own 400
 *  messages pass through; Python exception prefixes are stripped,
 *  tracebacks become "internal error", and long text is cut to 200. */
export function friendlyPreviewError(raw: string | undefined): string {
  if (!raw) return 'preview failed';
  let msg = raw;
  // "engine.editor_metadata.EditorMetadataError: foo" → "foo"
  msg = msg.replace(/^[a-zA-Z_][\w.]*Error:\s*/, '');
  if (msg.startsWith('Traceback')) return 'preview failed (internal error)';
  if (msg.length > 200) msg = msg.slice(0, 197) + '…';
  return msg;
}

// ---- running a preview -------------------------------------------------

/** How a preview job ended, from the caller's point of view. */
export type PreviewOutcome =
  /** Finished. `trace` is null when the result wasn't a preview result. */
  | { status: 'done'; trace: Trace | null }
  /** The job failed; `message` is the server's raw text. */
  | { status: 'error'; message: string | undefined }
  /** Cancelled server-side (a newer preview superseded it). */
  | { status: 'cancelled' }
  /** The caller's `isCurrent()` turned false: its context moved on. */
  | { status: 'stale' }
  /** The job's updates stopped without a final status. */
  | { status: 'ended' };

export interface RunPreviewOptions {
  /** Checked after the job starts (when `cancelIfStale`) and before each
   *  update is acted on; false ends the run with `stale`. */
  isCurrent?: () => boolean;
  /** Cancel the just-started job when the context moved on while it was
   *  starting, so the server doesn't run a preview nobody will read. */
  cancelIfStale?: boolean;
  /** Told the job id as soon as the job has started. */
  onStart?: (jobId: string) => void;
}

/**
 * Start a preview of `template` + `sidecar` for `week` and watch it to
 * the end. Network errors from starting or watching are thrown, as
 * before the move; job failures come back as `error`.
 */
export async function runPreview(
  slug: string,
  template: unknown,
  sidecar: unknown,
  week: number,
  opts: RunPreviewOptions = {},
): Promise<PreviewOutcome> {
  const isCurrent = opts.isCurrent ?? (() => true);
  const start = await startPreviewJob(
    slug,
    template as Record<string, unknown>,
    sidecar as Record<string, unknown>,
    week,
  );
  if (opts.cancelIfStale && !isCurrent()) {
    void cancelJob(start.job_id).catch(() => {
      // Idempotent (204 / 404 both fine); a network blip is harmless.
    });
    return { status: 'stale' };
  }
  opts.onStart?.(start.job_id);
  for await (const snap of watchJob(start.job_id)) {
    if (!isCurrent()) return { status: 'stale' };
    if (snap.status === 'done') {
      const r = snap.result;
      return { status: 'done', trace: isPreviewResult(r) ? r.trace : null };
    }
    if (snap.status === 'error') return { status: 'error', message: snap.error?.message };
    if (snap.status === 'cancelled') return { status: 'cancelled' };
  }
  return { status: 'ended' };
}
