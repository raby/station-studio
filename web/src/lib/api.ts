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
 *
 * §16.14.t10: broadened to cover both int + list rules. The `kind`
 * discriminator drives per-component dispatch; the RulesPanel
 * renders int rules with its existing edit + save + reset UX and
 * list rules via the <ListRuleRow> chip editor shipped in
 * §16.14.t11 (add via Enter or autocomplete, per-chip × remove,
 * Clear-override DELETEs the Rule row).
 */
export interface Rule {
  key: string;
  kind: 'int' | 'list';
  value: number | string[];
  default_value: number | string[];
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
 * Upsert a per-station override. Backend validates `value`: int
 * rules must be non-negative and within `max_value`; list rules
 * must contain non-empty comma-free string entries. 4xx on
 * violation, surfaced via ApiError.
 *
 * §16.14.t10: `value` accepts number OR string[]. The backend
 * dispatches by the URL `{key}`'s registry membership; the caller
 * is expected to pass the shape matching the rule's `kind`.
 */
export const setStationRule = (
  slug: string,
  key: string,
  value: number | string[],
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
 * §16.2.6 c2 — result shape for /jobs/preview's worker on_result.
 * Distinct from GenerateResponse — preview returns the trace dict
 * (no playlist_id / station / week / slot_count summary — those
 * are persistence-side concerns; preview's slot_count is included
 * for parity with the job-snapshot label rendering).
 */
export interface PreviewResult {
  trace: Trace;
  slot_count: number;
}


/**
 * §16.2.6 c3 — type guards for the `JobSnapshot.result` union.
 *
 * The widening from `GenerateResponse` to `GenerateResponse |
 * PreviewResult | null` makes every consumer narrow before reading
 * kind-specific fields. The c2 narrows used inline `'playlist_id'
 * in r` / `'trace' in r` checks; c2 review #1 finding #12 flagged
 * this as a maintainability gap — a future contributor writing a
 * third job kind would have to reverse-engineer the convention.
 *
 * Hoisted as named guards next to the interfaces so the
 * discriminator (presence of `playlist_id` vs `trace`) is a single
 * source of truth + IDE refactors can find every callsite.
 */
export function isGenerateResult(
  r: GenerateResponse | PreviewResult | null | undefined,
): r is GenerateResponse {
  return r !== null && r !== undefined && 'playlist_id' in r;
}

export function isPreviewResult(
  r: GenerateResponse | PreviewResult | null | undefined,
): r is PreviewResult {
  return r !== null && r !== undefined && 'trace' in r;
}

/**
 * Snapshot returned by GET /api/jobs/{job_id}. Mirrors the backend's
 * `JobStatusResponse` (api/schemas.py). The `result` shape varies by
 * job kind: `GenerateResponse` for kind="generate" (Decision E1
 * wrapper-vs-job parity); `PreviewResult` for kind="preview".
 * Callers discriminate on `kind` before narrowing.
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
  /** Populated when status === "done". Shape depends on `kind`:
   *  GenerateResponse for generate jobs, PreviewResult for preview. */
  result: GenerateResponse | PreviewResult | null;
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


// ---- §16.19 — station overview (rich detail bundle) -------------------


export interface TemplateSummary {
  slot_count: number;
  layout_form: 'flat_slots' | 'row_template';
  strategy_counts: Record<string, number>;
  cursor_persist: boolean;
  source_count: number;
}

export interface PoolStatus {
  name: string;
  role: string;
  imported: boolean;
  track_count: number | null;
  imported_at: string | null;
}

export interface CursorPosition {
  name: string;
  position: number;
}

export interface GenerationsSummary {
  count: number;
  last_generated_week: number | null;
  last_generated_at: string | null;
  /** Playlists generated before the last template edit (§16.2.3 hint). */
  stale_count: number;
}

export interface StationOverview {
  slug: string;
  name: string;
  cadence: string;
  rng: string;
  updated_at: string | null;
  template_summary: TemplateSummary;
  pools: PoolStatus[];
  cursors: CursorPosition[];
  generations: GenerationsSummary;
}

/**
 * Fetch the rich station-detail bundle (§16.19) — identity + template
 * summary + pool status + cursor positions + generation rollup, in one
 * request. 404 if the station doesn't exist.
 */
export const getStationOverview = (
  slug: string,
  fetch_ = fetch,
): Promise<StationOverview> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}/overview`).then((r) =>
    json<StationOverview>(r),
  );


// ---- §16.2.3 c2 — atomic block edit (PATCH) + disk export ------------


/** Storage address of a block — XOR slot_index / block_idx. Mirrors
 *  the backend BlockAddress. row_idx is intentionally absent: a
 *  row_template edit targets the shared block_idx. */
export interface BlockAddress {
  slot_index?: number;
  block_idx?: number;
}

export interface BlockPatchResponse {
  block: Record<string, unknown>;
  updated_at: string | null;
}

/**
 * Thrown when a block PATCH 409s — the station changed since the
 * editor loaded it (Decision D2). Carries the server's current
 * `updated_at` so the caller can prompt a reload. Extends ApiError
 * so generic catches still work.
 */
export class BlockPatchConflict extends ApiError {
  current_updated_at: string | null;
  constructor(message: string, current_updated_at: string | null) {
    super(409, message);
    this.name = 'BlockPatchConflict';
    this.current_updated_at = current_updated_at;
  }
}

/**
 * Edit one block's parameter fields. `fields` values: a value sets
 * the field; `null` clears an optional field. `expectedUpdatedAt` is
 * the optimistic-concurrency token from `getStationSidecar`.
 *
 * Throws `BlockPatchConflict` on 409 (stale token); generic
 * `ApiError` on other non-2xx (400 invalid edit / parameter-only
 * violation, 404 station/out-of-range).
 */
export const patchBlock = async (
  slug: string,
  address: BlockAddress,
  fields: Record<string, string | number | string[] | null>,
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<BlockPatchResponse> => {
  const res = await fetch_(`/api/stations/${encodeURIComponent(slug)}/blocks`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      address,
      fields,
      expected_updated_at: expectedUpdatedAt,
    }),
  });
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<BlockPatchResponse>(res);
};

// ---- §16.2.4 c2 — block retype + macro edit (PATCH) ------------------


export interface BlockRetypeResponse {
  block: Record<string, unknown>;
  updated_at: string | null;
}

/**
 * Change a block's `strategy` and/or `source` (§16.2.4 D2).
 *
 * Distinct from `patchBlock`'s parameter-only contract. Pass `null`
 * for `newStrategy` / `newSource` to leave them unchanged.
 * `newFields` carries any new-required-field values (e.g.
 * `{ position: 1 }` when retyping to `archive_ref`); structural
 * keys (strategy / source / size) are REJECTED by the server (c1
 * review #5) — use newStrategy / newSource for those.
 *
 * Same 409-on-stale-token contract as `patchBlock`: throws
 * `BlockPatchConflict` so the same `conflict` banner reuses.
 */
export const retypeBlock = async (
  slug: string,
  address: BlockAddress,
  newStrategy: string | null,
  newSource: string | null,
  newFields: Record<string, string | number | string[] | null>,
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<BlockRetypeResponse> => {
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/blocks/retype`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        address,
        new_strategy: newStrategy,
        new_source: newSource,
        new_fields: newFields,
        expected_updated_at: expectedUpdatedAt,
      }),
    },
  );
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<BlockRetypeResponse>(res);
};


export interface MacroPatchResponse {
  blocks_updated: number;
  updated_at: string | null;
}

/**
 * Edit-once-applies-to-all: PATCH a macro by id (§16.2.4 D1). The
 * server resolves macro_id → expanded block addresses (via the
 * sidecar's macros-by-id mapping + section anchoring) and applies
 * `fields` to each in one transaction.
 *
 * Parameter-only guard fires PER expanded block — if any rejects,
 * the whole transaction aborts. Same 409 contract as patchBlock.
 */
export const patchMacro = async (
  slug: string,
  macroId: string,
  fields: Record<string, string | number | string[] | null>,
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<MacroPatchResponse> => {
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/macros/${encodeURIComponent(macroId)}`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        fields,
        expected_updated_at: expectedUpdatedAt,
      }),
    },
  );
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<MacroPatchResponse>(res);
};


// ---- §16.2.5 c2 — section CRUD + structural block edits ----------------
//
// Six new endpoints all return the same shape (the full new sidecar +
// updated_at), so frontend reuses one response handler. Optimistic-
// concurrency contract matches PATCH /blocks: 409 → BlockPatchConflict.


export interface SidecarMutationResponse {
  sidecar: import('./editor').Sidecar;
  /** §16.2.5.t5 + c1 review #3 finding #6: ALWAYS the live
   *  post-mutation template. Pre-fix this was optional ('only when
   *  the route changed it') — sending it every time eliminates the
   *  two-tab drift hazard where sidecar-only mutations left the
   *  frontend's local template stale against a parallel writer's
   *  structural edit. */
  template: Record<string, unknown>;
  updated_at: string | null;
}


/** Shared 409-handler for every PATCH endpoint with optimistic
 *  concurrency. Slice-review sweep folded the §16.2.3 `patchBlock`,
 *  §16.2.4 `retypeBlock` + `patchMacro` inline copies into this
 *  helper too — renamed from `_handleConcurrencyConflict` since it now
 *  serves both template-mutation + sidecar-mutation paths. */
async function _handleConcurrencyConflict(res: Response): Promise<never> {
  let detail: { message?: string; current_updated_at?: string | null } | string = '';
  try {
    const parsed = await res.json();
    detail = parsed?.detail ?? parsed;
  } catch {
    detail = await res.text();
  }
  if (detail && typeof detail === 'object') {
    throw new BlockPatchConflict(
      detail.message ?? 'station changed since you loaded it',
      detail.current_updated_at ?? null,
    );
  }
  throw new ApiError(409, `409 Conflict: ${JSON.stringify(detail)}`);
}


/** POST /api/stations/{slug}/sections — create an empty section
 *  (§16.2.5 D4). Server appends to layout_order. */
export const createSection = async (
  slug: string,
  id: string,
  name: string | null,
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<SidecarMutationResponse> => {
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/sections`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        id, name, expected_updated_at: expectedUpdatedAt,
      }),
    },
  );
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<SidecarMutationResponse>(res);
};


/** PATCH /api/stations/{slug}/sections/{section_id} — rename a section.
 *  Id stays immutable; only `name` changes. */
export const renameSection = async (
  slug: string,
  sectionId: string,
  name: string,
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<SidecarMutationResponse> => {
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/sections/${encodeURIComponent(sectionId)}`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name, expected_updated_at: expectedUpdatedAt }),
    },
  );
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<SidecarMutationResponse>(res);
};


/** DELETE /api/stations/{slug}/sections/{section_id} — delete a section.
 *  D2: non-empty sections re-home their blocks into the `_unsorted`
 *  bin server-side. The optimistic-token rides as a query string
 *  because DELETE bodies aren't universally supported. */
export const deleteSection = async (
  slug: string,
  sectionId: string,
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<SidecarMutationResponse> => {
  const qs = expectedUpdatedAt !== null
    ? `?expected_updated_at=${encodeURIComponent(expectedUpdatedAt)}`
    : '';
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/sections/${encodeURIComponent(sectionId)}${qs}`,
    { method: 'DELETE' },
  );
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<SidecarMutationResponse>(res);
};


/** PUT /api/stations/{slug}/sections/order — full layout_order
 *  replacement; must be a permutation of the existing section ids. */
export const reorderSections = async (
  slug: string,
  layoutOrder: string[],
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<SidecarMutationResponse> => {
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/sections/order`,
    {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        layout_order: layoutOrder,
        expected_updated_at: expectedUpdatedAt,
      }),
    },
  );
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<SidecarMutationResponse>(res);
};


/** PATCH /api/stations/{slug}/blocks/move — move an ATOMIC block
 *  to another section. Macros aren't movable in §16.2.5 (the sidecar
 *  position-math can't sustain it without grammar changes); the
 *  server 404s with an operator-readable hint. */
export const moveBlock = async (
  slug: string,
  address: BlockAddress,
  targetSectionId: string,
  targetIndex: number | null,
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<SidecarMutationResponse> => {
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/blocks/move`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        address,
        target_section_id: targetSectionId,
        target_index: targetIndex,
        expected_updated_at: expectedUpdatedAt,
      }),
    },
  );
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<SidecarMutationResponse>(res);
};


/** PATCH /api/stations/{slug}/blocks/structure — one of three
 *  structural edits (size / add / remove). Add inserts at
 *  slot_index + auto-files in `_unsorted`; remove cascades the
 *  sidecar atomic ref drop. */
export type StructuralOp = 'size' | 'add' | 'remove';
export interface StructuralBlockEdit {
  op: StructuralOp;
  slot_index: number;
  /** Required when op === 'size'. */
  new_size?: number;
  /** Required when op === 'add'. */
  new_block?: Record<string, unknown>;
}
export const structuralBlockEdit = async (
  slug: string,
  edit: StructuralBlockEdit,
  expectedUpdatedAt: string | null,
  fetch_ = fetch,
): Promise<SidecarMutationResponse> => {
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/blocks/structure`,
    {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        edit, expected_updated_at: expectedUpdatedAt,
      }),
    },
  );
  if (res.status === 409) await _handleConcurrencyConflict(res);
  return json<SidecarMutationResponse>(res);
};


export interface TemplateExportResponse {
  path: string;
  bytes: number;
}

/**
 * Write the station's current DB template to `stations/{slug}.json`
 * (§16.2.3 Decision A3 — the on-demand git-committable snapshot).
 */
export const exportStationTemplate = (
  slug: string,
  fetch_ = fetch,
): Promise<TemplateExportResponse> =>
  fetch_(`/api/stations/${encodeURIComponent(slug)}/export`, {
    method: 'POST',
  }).then((r) => json<TemplateExportResponse>(r));


// ---- §16.25 — source-pool management (list / upload / import / delete) ----


/**
 * One imported source pool. Mirrors the backend `SourceOut`
 * (`GET /api/sources`); used as the row type for the §16.25
 * `/sources` page and the §16.19 pool-status widget.
 */
export interface Source {
  id: number;
  name: string;
  /** §14.35 — `content` (engine samples) vs `reference` (cross-ref only). */
  role: 'content' | 'reference';
  track_count: number;
}

/**
 * Server-side `import_source(...)` summary, returned by both
 * `POST /api/sources/upload` and `POST /api/sources/import`. The
 * library-level keys (`name_persistent_id`, etc) are present but
 * we don't surface them yet; documented loosely so a future field
 * doesn't 422 the typed parse.
 */
export interface ImportSummary {
  name: string;
  role: 'content' | 'reference';
  tracks_imported: number;
  entries_imported: number;
  [extra: string]: unknown;
}

/** `GET /api/sources` — every imported pool with its current track count.
 *
 * §16.14.t11 c1 review F10: `SourcePlaylist` is GLOBAL (no
 * `station_id` FK by design). The hobbyist model is "one music
 * library on your machine, multiple stations as different
 * configurations on top of it." Stations reference pools by
 * NAME in their template's `sources[]` block; the cross-checks
 * at `parse_template()` + `load_pools()` enforce role
 * consistency, but pool names live in one shared namespace.
 *
 * Callers (e.g. <ListRuleRow>'s autocomplete) that want
 * "station-relevant" pools either filter client-side OR rely
 * on the engine's runtime cross-check at generation time. The
 * autocomplete intentionally shows all reference pools — not
 * just those declared in the current station's template —
 * because the `cross_pool_dedup_sources` rule legitimately
 * accepts pools the station doesn't formally declare (its
 * whole purpose is "exclude tracks ALSO in this OTHER pool I
 * don't sample from").
 */
export const listSources = (fetch_ = fetch): Promise<Source[]> =>
  fetch_('/api/sources').then((r) => json<Source[]>(r));

/**
 * §16.25 c1 — the browser-friendly multipart upload path. Used as the
 * PRIMARY route on `/sources` (D1) because it works regardless of
 * where the server runs (pipx-local OR docker — the user's XML never
 * has to be on the server's disk).
 *
 * The first non-JSON helper in this module: a `FormData` body lets
 * the browser stream the file without buffering it in memory.
 * `content-type` is intentionally NOT set — the browser fills it
 * with the right `multipart/form-data; boundary=...` automatically.
 */
export const uploadSource = (
  name: string,
  role: 'content' | 'reference',
  file: File,
  fetch_ = fetch,
  /**
   * §16.25.t2 — optional AbortSignal. When the linked AbortController
   * fires `.abort()`, fetch rejects with an `AbortError`, the panel's
   * inner try/catch flips the row to `cancelled`, and (in batch mode)
   * the surviving for-loop breaks out of the pending tail.
   */
  signal?: AbortSignal,
): Promise<ImportSummary> => {
  const body = new FormData();
  body.append('name', name);
  body.append('role', role);
  body.append('file', file);
  return fetch_('/api/sources/upload', {
    method: 'POST',
    body,
    signal,
  }).then((r) => json<ImportSummary>(r));
};

/**
 * §16.25 D1 — the "advanced / local file" path (a typed server-side
 * filesystem path, NOT a file picker — browsers can't return a real
 * path from `<input type="file">`). For `pipx`-local users with a
 * 100 MB library already on disk who'd rather skip the multipart
 * round-trip.
 */
export const importSourceByPath = (
  name: string,
  role: 'content' | 'reference',
  path: string,
  fetch_ = fetch,
  /** §16.25.t2 — see `uploadSource` for AbortSignal semantics. */
  signal?: AbortSignal,
): Promise<ImportSummary> =>
  fetch_('/api/sources/import', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name, role, path }),
    signal,
  }).then((r) => json<ImportSummary>(r));

/**
 * `DELETE /api/sources/{id}`. Cascade-deletes the pool + its
 * `SourcePlaylistEntry` rows. Generated playlists are unaffected —
 * `PlaylistEntry` references tracks directly, not pools. Caller
 * confirms (the §16.25 c4 UI wraps this with a confirm dialog).
 */
export const deleteSource = (id: number, fetch_ = fetch): Promise<void> =>
  fetch_(`/api/sources/${id}`, { method: 'DELETE' }).then((r) => noContent(r));


// ----- §16.2.6 c1 live-preview transport ---------------------------------
//
// `POST /api/stations/{slug}/jobs/preview` shares the §16.11 job-id
// transport with /jobs/generate — JobStartResponse + watchJob + pollJob +
// cancelJob all reused. The only thing unique to preview at the client
// surface is the request body shape: the operator's IN-MEMORY editor
// state (template + sidecar) NOT the stored station's. The backend
// recomputes excluded_mix_ids / previous_playlist / alias_map fresh
// from DB per request — sending them from the frontend would defeat
// the recompute-per-request guarantee. See DESIGN §16.2.6 "API surface."
//
// Concurrent policy on the server is POLICY_CANCEL_EXISTING — calling
// startPreviewJob again for the same slug supersedes the in-flight
// preview without the frontend needing an explicit cancelJob. The
// cancelJob entry-point remains supported for Discard mid-PREVIEWING
// (when c3 ships).

export const startPreviewJob = async (
  slug: string,
  template: Record<string, unknown>,
  sidecar: Record<string, unknown>,
  week: number,
  seed?: number,
  fetch_ = fetch,
): Promise<JobStartResponse> => {
  const body: {
    template: Record<string, unknown>;
    sidecar: Record<string, unknown>;
    week: number;
    seed?: number;
  } = { template, sidecar, week };
  if (seed !== undefined) body.seed = seed;
  const res = await fetch_(
    `/api/stations/${encodeURIComponent(slug)}/jobs/preview`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  return json<JobStartResponse>(res);
};


export { ApiError };
