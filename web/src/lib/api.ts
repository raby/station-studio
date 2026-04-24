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

export { ApiError };
