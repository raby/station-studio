// Typed fetch helpers for the FastAPI backend.
//
// Relative paths so Vite's dev proxy (vite.config.js) forwards /api/* to
// localhost:8001 in dev; in a deployed build the SvelteKit adapter should
// serve under the same origin as the API.
//
// Types intentionally mirror the Pydantic schemas in api/schemas.py. Keep
// them in sync — if you add a field there, add it here.

export interface Station {
  id: number;
  slug: string;
  name: string;
  template: Record<string, unknown>;
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
  track_id: number;
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

export const getStations = (fetch_ = fetch): Promise<Station[]> =>
  fetch_('/api/stations').then((r) => json<Station[]>(r));

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

export { ApiError };
