// Typed fetch helpers for the FastAPI backend.
//
// Relative paths so Vite's dev proxy (vite.config.js) forwards /api/* to
// localhost:8001 in dev; in a deployed build the SvelteKit adapter should
// serve under the same origin as the API.
//
// Types intentionally mirror the Pydantic schemas in api/schemas.py. Keep
// them in sync — if you add a field there, add it here.

// §16.2.2.t1 — api.ts now depends on editor.ts for the sidecar
// validator + parsed-shape type. A one-way dep (editor.ts doesn't
// import api.ts), explicitly chosen: editor.ts owns the canonical
// shapes for the visual editor; replicating them here would mean
// two sources of truth. If a future caller wants api.ts without
// editor.ts (e.g. a non-editor surface that just needs auth
// helpers), the validator can be split into a third "shapes" module
// — but that's overengineering today.
import {
  parseEditorSidecarResponse,
  type ParsedEditorSidecar,
} from '$lib/editor';

export interface Station {
  id: number;
  slug: string;
  name: string;
  template: Record<string, unknown>;
}

/**
 * List-row shape from GET /api/stations (§16.12). Mirrors the
 * backend `StationSummary` — lighter than `Station` (no template
 * blob) + per-station aggregates the landing page sorts/filters on.
 * The DETAIL endpoint (getStation) still returns the full `Station`.
 */
export interface StationSummary {
  id: number;
  slug: string;
  name: string;
  cadence: string;
  playlist_count: number;
  last_generated_week: number | null;
  last_generated_at: string | null; // ISO 8601
  updated_at: string | null; // ISO 8601
}

export interface PlaylistSummary {
  id: number;
  week: number;
  trace_id: string;
  generated_at: string | null;
  entry_count: number;
}

export interface Track {
  id: number;
  name: string;
  artist: string | null;
  album: string | null;
}

export interface PlaylistEntry {
  position: number;
  source_name: string | null;
  /**
   * Strategy tag (window/pick/sequential/jingle/mix/weighted/prev_ref),
   * denormalised from the trace's slot.strategy (§14.31). Null for
   * entries created before §14.31 that haven't been backfilled via
   * `scripts/backfill_slot_types.py`.
   */
  slot_type: string | null;
  track: Track;
}

export interface Playlist {
  id: number;
  station: string;
  week: number;
  trace_id: string;
  entries: PlaylistEntry[];
}

/**
 * A track the strategy considered but did not pick, with rationale
 * (DESIGN.md §14.23). Populated by future weighted/dedup-aware strategies;
 * empty for the current deterministic ones (window/pick/sequential).
 */
export interface ExcludedCandidate {
  pool_index: number;
  track_id: number | null;
  track_name: string;
  track_artist: string | null;
  reason: string;
}

export interface TraceSlot {
  slot: number;
  unit: string;
  row: number;
  block: number;
  source: string;
  strategy: string;
  pool_index: number;
  /**
   * Mirrors `engine.trace.TraceSlot.track_id: int | None`. Null for
   * dry-run / synthetic generations where the track wasn't ID'd in
   * the DB; not currently produced by any live code path but the
   * engine reserves None. §14.33 fix: was `number`, drifted from
   * the engine's `int | None` declaration.
   */
  track_id: number | null;
  track_name: string;
  track_artist: string | null;
  reason: string;
  /**
   * Optional — added in §14.23. Traces persisted before that release lack
   * this field; the viewer treats missing/undefined as an empty list.
   */
  excluded?: ExcludedCandidate[];
}

export interface Trace {
  trace_id: string;
  station: string;
  week: number;
  rng: string;
  seed: number | null;
  generated_at: string;
  slots: TraceSlot[];
}

// ---- internals ----------------------------------------------------------

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, `${res.status} ${res.statusText}: ${body}`);
  }
  return (await res.json()) as T;
}

/**
 * Like `json`, but for endpoints that return 204 No Content (DELETE). Skips
 * the JSON parse — calling `.json()` on an empty body throws. Errors still
 * map through ApiError so the caller can surface them inline.
 */
async function noContent(res: Response): Promise<void> {
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, `${res.status} ${res.statusText}: ${body}`);
  }
}

// ---- public API ---------------------------------------------------------

export const getStations = (fetch_ = fetch): Promise<StationSummary[]> =>
  fetch_('/api/stations').then((r) => json<StationSummary[]>(r));

export const getStation = (slug: string, fetch_ = fetch): Promise<Station> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}`).then((r) => json<Station>(r));

export const getStationPlaylists = (
  slug: string,
  fetch_ = fetch,
): Promise<PlaylistSummary[]> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}/playlists`).then((r) =>
    json<PlaylistSummary[]>(r),
  );

export const getPlaylist = (id: number, fetch_ = fetch): Promise<Playlist> =>
  fetch_(`/api/playlists/${id}`).then((r) => json<Playlist>(r));

export const getTrace = (id: number, fetch_ = fetch): Promise<Trace> =>
  fetch_(`/api/playlists/${id}/trace`).then((r) => json<Trace>(r));

export interface StationCreatePayload {
  slug: string;
  name: string;
  template: Record<string, unknown>;
}

export const createStation = (
  payload: StationCreatePayload,
  fetch_ = fetch,
): Promise<Station> =>
  fetch_('/api/stations', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((r) => json<Station>(r));

/**
 * Update an existing station's display name + template (DESIGN.md §16.7).
 * The slug is part of the URL and must not be in the body — the backend
 * rejects slug changes with 400 (slug is immutable; iTunes Persistent IDs
 * embed it). The shape mirrors `createStation` because both wrap the same
 * server-side validator (`engine.template.parse_template`).
 */
export const updateStation = (
  slug: string,
  payload: StationCreatePayload,
  fetch_ = fetch,
): Promise<Station> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((r) => json<Station>(r));

export interface GenerateResponse {
  playlist_id: number;
  trace_id: string;
  station: string;
  week: number;
  slot_count: number;
}

/**
 * Generate (or regenerate) a playlist for `(slug, week)`.
 *
 * If a playlist already exists for that pair the backend silently overwrites
 * — the calling UI is expected to confirm with the user first (see DESIGN.md
 * §16.1 D2). `seed` is only honoured for stations whose template is
 * `rng: "on"`; otherwise it's ignored by the engine.
 */
export const generatePlaylist = (
  slug: string,
  week: number,
  seed?: number,
  fetch_ = fetch,
): Promise<GenerateResponse> => {
  const body: { week: number; seed?: number } = { week };
  if (seed !== undefined) body.seed = seed;
  return fetch_(`/api/stations/${encodeURIComponent(slug)}/generate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }).then((r) => json<GenerateResponse>(r));
};

/**
 * Delete a single playlist + its entries + its trace (DESIGN.md §16.3 D1).
 * Hard delete — no undo. Caller must confirm with the user first; the
 * backend silently destroys on 204.
 */
export const deletePlaylist = (id: number, fetch_ = fetch): Promise<void> =>
  fetch_(`/api/playlists/${id}`, { method: 'DELETE' }).then((r) => noContent(r));

/**
 * Delete a station and cascade through every playlist + entry + trace +
 * cursor state (DESIGN.md §16.3 D1). Same destructive semantics as
 * `deletePlaylist`; caller must confirm and ideally surface counts in the
 * dialog (D2).
 */
export const deleteStation = (slug: string, fetch_ = fetch): Promise<void> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}`, { method: 'DELETE' }).then(
    (r) => noContent(r),
  );

export interface DiskExportResponse {
  /** Absolute path the iTunes XML was written to. */
  path: string;
  /** Size of the file on disk, in bytes. */
  bytes: number;
}

/**
 * Write the playlist's iTunes XML to the configured server-side output
 * directory (DESIGN.md §16.6, originally §14.2). Non-destructive — overwrites
 * only the file at `{playlist_output_dir}/{slug}/{week:03d}.xml`. The browser-
 * download counterpart is `/api/playlists/{id}/export` (a plain `<a href>`
 * per §14.5).
 */
export const exportPlaylistToDisk = (
  id: number,
  fetch_ = fetch,
): Promise<DiskExportResponse> =>
  fetch_(`/api/playlists/${id}/export-to-disk`, { method: 'POST' }).then((r) =>
    json<DiskExportResponse>(r),
  );

/**
 * Canonical option lists for template-shaped UI dropdowns. Sourced from
 * `engine/template.py` via GET /api/meta (DESIGN.md §14.19) so the
 * <StationForm> and <LayoutFieldset> dropdowns can't drift from what
 * the validator accepts. Each list is sorted server-side.
 */
export interface Meta {
  strategies: string[];
  rng: string[];
  roles: string[];
  cadences: string[];
}

/**
 * Fetch the canonical option lists. Treat the response as cacheable
 * static data — see `web/src/lib/meta.ts` for the module-level cache
 * the page components share.
 */
export const getMeta = (fetch_ = fetch): Promise<Meta> =>
  fetch_('/api/meta').then((r) => json<Meta>(r));


// ---- §14.29 + §14.30 per-station rule CRUD ----------------------------

/**
 * One rule's effective state for a station. Mirrors the backend's
 * RuleOut Pydantic schema. `value` is what the engine sees; if
 * `is_override` is false, value equals default_value.
 */
export interface Rule {
  key: string;
  value: number;
  default_value: number;
  description: string;
  max_value: number | null;
  is_override: boolean;
}

export interface RuleListResponse {
  rules: Rule[];
}

/** List every known rule with its effective value + override flag. */
export const getStationRules = (
  slug: string,
  fetch_ = fetch,
): Promise<RuleListResponse> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}/rules`).then((r) =>
    json<RuleListResponse>(r),
  );

/**
 * Upsert a per-station override. Backend validates `value`: must be
 * non-negative; capped at the rule's `max_value` if present (4xx on
 * violation, surfaced via ApiError per the rest of the api.ts shape).
 */
export const setStationRule = (
  slug: string,
  key: string,
  value: number,
  fetch_ = fetch,
): Promise<Rule> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}/rules/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ value }),
  }).then((r) => json<Rule>(r));

/**
 * Clear a per-station override (revert to default). Idempotent on
 * the backend — clearing a non-existent override returns 204.
 */
export const clearStationRule = (
  slug: string,
  key: string,
  fetch_ = fetch,
): Promise<void> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}/rules/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  }).then((r) => noContent(r));

// ---- §16.11 c4 — async job runner ------------------------------------
//
// Job pattern (DESIGN.md §16.11 Decision A1):
//   1. POST /api/stations/{slug}/jobs/generate  → 201 {job_id, status}
//      OR 409 {detail: {message, existing_job_id}} if a job is already
//      running for this slug.
//   2. Poll GET /api/jobs/{job_id} every ~500ms until status !== "running".
//      404 mid-poll = server restarted or job GC'd; treat as terminal.
//   3. DELETE /api/jobs/{job_id} to cancel (idempotent, 204).
//
// Error-response shapes for the job endpoints (api/routers/jobs.py
// header comment is the contract):
//   * 404 — {"detail": <string>}                       → ApiError
//   * 400 — {"detail": <string>}                       → ApiError
//   * 422 — {"detail": [...]}                          → ApiError
//   * 409 — {"detail": {"message", "existing_job_id"}} → JobStartConflict

export type JobStatus = 'running' | 'done' | 'error' | 'cancelled';

export interface JobErrorBody {
  message: string;
  /** Python exception class name — e.g. "RuntimeError", "ValueError",
   *  "AssertionError" for engine-bug paths. */
  kind: string;
}

/**
 * Snapshot returned by GET /api/jobs/{job_id}. Mirrors the backend's
 * `JobStatusResponse` (api/schemas.py). The `result` shape for a
 * generate job matches `GenerateResponse` exactly — the wrapper-vs-
 * job parity Decision E1 promised.
 */
export interface JobSnapshot {
  job_id: string;
  slug: string;
  kind: string;
  status: JobStatus;
  /** Mid-stream phase from the engine — "queued" before the worker
   *  pulls its first event, then "start", then "picks". Frontends
   *  should treat phase ∈ {"queued", "start"} as "indeterminate". */
  phase: string;
  current: number;
  total: number;
  label: string | null;
  /** Populated when status === "done". For generate jobs this is the
   *  same shape as GenerateResponse. */
  result: GenerateResponse | null;
  /** Populated when status === "error". */
  error: JobErrorBody | null;
  /** ISO-8601 UTC (Z-suffix), never null. */
  started_at: string;
  /** ISO-8601 UTC (Z-suffix). Null while status === "running". */
  finished_at: string | null;
}

/**
 * Thrown by `startGenerateJob` when the backend responds 409 with the
 * conflict-detail shape. Carries `existing_job_id` so the caller can
 * choose to poll the in-flight job rather than failing the user's
 * click. Extends ApiError so generic catch-blocks still work.
 */
export class JobStartConflict extends ApiError {
  existing_job_id: string;
  constructor(message: string, existing_job_id: string) {
    super(409, message);
    this.name = 'JobStartConflict';
    this.existing_job_id = existing_job_id;
  }
}

/**
 * Shape returned by POST /api/stations/{slug}/jobs/generate on success.
 * `status` is always "running" at this point — the worker thread might
 * not have entered the engine yet; immediately follow with `pollJob` or
 * `watchJob`.
 */
export interface JobStartResponse {
  job_id: string;
  status: 'running';
}

/**
 * Start an asynchronous generation job. Returns the new job_id on 201;
 * throws `JobStartConflict` on 409 (concurrent job exists); throws a
 * generic `ApiError` for other non-2xx.
 *
 * Decision F: per-(slug) REJECT means a second click while one runs
 * gets 409. The mid-slice review fix (🔴-1) means a click after a
 * previous job finished — even within the 60s grace — does NOT 409;
 * it starts a fresh job.
 */
export const startGenerateJob = async (
  slug: string,
  week: number,
  seed?: number,
  fetch_ = fetch,
): Promise<JobStartResponse> => {
  const body: { week: number; seed?: number } = { week };
  if (seed !== undefined) body.seed = seed;
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/jobs/generate`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (res.status === 409) {
    // 409 carries a structured detail; parse + throw JobStartConflict
    // so callers can distinguish "click harder" from "deeper problem".
    let detail: { message?: string; existing_job_id?: string } | string = '';
    try {
      const parsed = await res.json();
      detail = parsed?.detail ?? parsed;
    } catch {
      // Non-JSON body — shouldn't happen but handle defensively.
      detail = await res.text();
    }
    if (
      detail &&
      typeof detail === 'object' &&
      typeof detail.existing_job_id === 'string'
    ) {
      throw new JobStartConflict(
        detail.message ?? 'a job for this station is already running',
        detail.existing_job_id,
      );
    }
    // Fall through to ApiError if the shape is unexpected.
    throw new ApiError(409, `409 Conflict: ${JSON.stringify(detail)}`);
  }
  return json<JobStartResponse>(res);
};

/**
 * Poll a job's current state. Returns null on 404 — the calling
 * `watchJob` translates that into a clean terminal stop (server
 * restarted; job GC'd past grace; typo'd id). Other non-2xx still
 * throw ApiError.
 */
export const pollJob = async (
  jobId: string,
  fetch_ = fetch,
): Promise<JobSnapshot | null> => {
  const res = await fetch_(`/api/jobs/${encodeURIComponent(jobId)}`);
  if (res.status === 404) return null;
  return json<JobSnapshot>(res);
};

/**
 * Cancel a job. Idempotent at the HTTP level: 204 on success,
 * 404 if the job is unknown. We absorb the 404 because cancel-after-
 * GC has the same end state the caller wanted ("dismiss this job")
 * — surfacing it as an error would be hostile to a UI racing cancel
 * against a fast finish.
 */
export const cancelJob = async (
  jobId: string,
  fetch_ = fetch,
): Promise<void> => {
  const res = await fetch_(`/api/jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  });
  if (res.status === 404) return; // already gone — caller's intent met.
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(res.status, `${res.status} ${res.statusText}: ${body}`);
  }
};

/**
 * Async-iterable wrapper around `pollJob`. Yields one snapshot per
 * poll until either `status !== "running"` or the job 404s mid-poll
 * (yields nothing in the 404 case — `for await` cleanly exits).
 *
 * Usage:
 *   for await (const state of watchJob(jobId)) {
 *     updateProgress(state);
 *     if (state.status === 'done') navigateToResult(state.result);
 *     if (state.status === 'error') showToast(state.error);
 *   }
 *
 * The 250ms median polling tax (Decision A1 punt) is the price of
 * the polling transport. If/when long-poll lands (§16.21+), this
 * helper's signature can stay; only its internal sleep goes away.
 *
 * `sleepFn` is injectable for tests so they don't actually sleep.
 */
export async function* watchJob(
  jobId: string,
  intervalMs = 500,
  fetch_ = fetch,
  sleepFn: (ms: number) => Promise<void> = (ms) =>
    new Promise((r) => setTimeout(r, ms)),
): AsyncIterable<JobSnapshot> {
  // Loop until the job reaches a terminal status OR 404s. We poll
  // BEFORE sleeping on the first iteration so the caller sees fresh
  // state immediately rather than waiting `intervalMs` for the first
  // snapshot.
  // Hard cap on iterations as a defensive safety net — at 500ms/poll
  // 7200 iterations = 1h, far beyond any reasonable generation. If
  // we ever blow past this it's a bug (engine stuck, never reaches
  // a terminal state).
  let safety = 7200;
  while (safety-- > 0) {
    const snap = await pollJob(jobId, fetch_);
    if (snap === null) {
      // Job vanished (404). Yielding nothing here means consumer's
      // for-await loop exits cleanly; they can detect via "we got no
      // terminal status" + the next pollJob returning null. UI side
      // typically shows a "job lost" toast.
      return;
    }
    yield snap;
    if (snap.status !== 'running') return;
    await sleepFn(intervalMs);
  }
  throw new Error(
    `watchJob(${jobId}) iteration limit exceeded — engine may be stuck`,
  );
}


// ---- §16.2.2 c2 — visual block editor sidecar fetch ------------------


/**
 * Fetch the editor sidecar bundle for a station. The endpoint
 * returns 404 only when the station itself doesn't exist —
 * absent/broken sidecars resolve to the trivial fallback with
 * `sidecar_error` populated, never an HTTP error.
 *
 * §16.2.2.t1 (post-c5 sweep): the response is now PARSED by
 * `parseEditorSidecarResponse` before return — replaces the
 * earlier opaque-`Record` + `as unknown as` cast pattern. Callers
 * get strongly-typed `template: StationTemplate` + `sidecar:
 * Sidecar` and an early `EditorMetadataError` throw if the server
 * ever drifts.
 */
export const getStationSidecar = async (
  slug: string,
  fetch_ = fetch,
): Promise<ParsedEditorSidecar> => {
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/sidecar`,
  );
  const raw = await json<unknown>(res);
  return parseEditorSidecarResponse(raw);
};


export { ApiError };
