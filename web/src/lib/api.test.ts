import { describe, it, expect, vi } from 'vitest';
import {
  ApiError,
  BlockPatchConflict,
  cancelJob,
  deleteSource,
  exportStationTemplate,
  getStations,
  getStation,
  getStationPlaylists,
  getPlaylist,
  getStationOverview,
  getStationSidecar,
  importSourceByPath,
  listSources,
  uploadSource,
  getTrace,
  createStation,
  generatePlaylist,
  deletePlaylist,
  deleteStation,
  exportPlaylistToDisk,
  getMeta,
  getStationRules,
  JobStartConflict,
  createSection,
  deleteSection,
  moveBlock,
  patchBlock,
  patchMacro,
  pollJob,
  renameSection,
  reorderSections,
  retypeBlock,
  structuralBlockEdit,
  setStationRule,
  clearStationRule,
  startGenerateJob,
  isGenerateResult,
  isPreviewResult,
  startPreviewJob,
  watchJob,
  type JobSnapshot,
} from './api';

/**
 * Unit tests for $lib/api.ts. Each helper is verified along two axes:
 *   - URL + method + body construction (via a spy fetch)
 *   - response parsing (happy path returns the JSON; non-2xx raises ApiError)
 *
 * We pass our spy fetch as the `fetch_` parameter every helper accepts,
 * avoiding any global monkey-patching.
 */

type SpyCall = {
  input: RequestInfo | URL;
  init?: RequestInit;
};

function makeSpy(response: {
  ok?: boolean;
  status?: number;
  statusText?: string;
  body?: unknown;
  text?: string;
}) {
  const calls: SpyCall[] = [];
  const fetch_ = async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ input, init });
    return {
      ok: response.ok ?? true,
      status: response.status ?? 200,
      statusText: response.statusText ?? 'OK',
      json: async () => response.body,
      text: async () => response.text ?? '',
    } as unknown as Response;
  };
  return { fetch_, calls };
}

describe('api client', () => {
  describe('getStations', () => {
    it('GETs /api/stations and returns the JSON list', async () => {
      const { fetch_, calls } = makeSpy({ body: [{ id: 1, slug: 's', name: 'S', template: {} }] });
      const out = await getStations(fetch_);
      expect(calls[0].input).toBe('/api/stations');
      expect(calls[0].init).toBeUndefined(); // plain GET
      expect(out).toEqual([{ id: 1, slug: 's', name: 'S', template: {} }]);
    });
  });

  describe('getStation', () => {
    it('URL-encodes the slug', async () => {
      const { fetch_, calls } = makeSpy({ body: { id: 1, slug: 'a b', name: 'AB', template: {} } });
      await getStation('a b', fetch_);
      expect(calls[0].input).toBe('/api/stations/a%20b');
    });

    it('raises ApiError with the response body when the server 404s', async () => {
      const { fetch_ } = makeSpy({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: '{"detail":"gone"}',
      });
      const err = await getStation('ghost', fetch_).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(404);
      expect(err.message).toContain('404');
      expect(err.message).toContain('{"detail":"gone"}');
    });
  });

  describe('getStationPlaylists', () => {
    it('GETs /api/stations/{slug}/playlists', async () => {
      const { fetch_, calls } = makeSpy({ body: [] });
      await getStationPlaylists('rebirth-classics', fetch_);
      expect(calls[0].input).toBe('/api/stations/rebirth-classics/playlists');
    });
  });

  describe('getPlaylist + getTrace', () => {
    it('build the /api/playlists/{id} and .../trace URLs', async () => {
      const spyPlaylist = makeSpy({ body: { id: 42 } });
      await getPlaylist(42, spyPlaylist.fetch_);
      expect(spyPlaylist.calls[0].input).toBe('/api/playlists/42');

      // §16.4 — getTrace now accepts (id, version, fetch_).
      // No version → no ?v= on the URL (server returns current).
      const spyTrace = makeSpy({ body: { trace_id: 'abc' } });
      await getTrace(42, null, spyTrace.fetch_);
      expect(spyTrace.calls[0].input).toBe('/api/playlists/42/trace');

      // With version → ?v=N appended.
      const spyTraceV = makeSpy({ body: { trace_id: 'def' } });
      await getTrace(42, 3, spyTraceV.fetch_);
      expect(spyTraceV.calls[0].input).toBe('/api/playlists/42/trace?v=3');
    });
  });

  describe('§16.4 getVersions', () => {
    it('builds the /api/playlists/{id}/versions URL', async () => {
      const spy = makeSpy({
        body: { playlist_id: 7, current_v: 2, versions: [] },
      });
      const { getVersions } = await import('./api');
      const result = await getVersions(7, spy.fetch_);
      expect(spy.calls[0].input).toBe('/api/playlists/7/versions');
      expect(result.current_v).toBe(2);
    });
  });

  describe('§16.4.t1 getTraceDiff', () => {
    it('builds the /diff?from=N&to=M URL', async () => {
      const spy = makeSpy({
        body: {
          playlist_id: 7,
          from: { version_no: 1, trace_id: 'a', generated_at: 'x' },
          to: { version_no: 3, trace_id: 'b', generated_at: 'y' },
          summary: { total_slots: 0, changed: 0, unchanged: 0 },
          slots: [],
        },
      });
      const { getTraceDiff } = await import('./api');
      const result = await getTraceDiff(7, 1, 3, spy.fetch_);
      expect(spy.calls[0].input).toBe('/api/playlists/7/diff?from=1&to=3');
      expect(result.from.version_no).toBe(1);
      expect(result.to.version_no).toBe(3);
    });
  });

  describe('createStation', () => {
    it('POSTs JSON to /api/stations with the payload serialised', async () => {
      const payload = { slug: 's', name: 'S', template: { x: 1 } };
      const { fetch_, calls } = makeSpy({ body: { id: 1, ...payload } });
      const out = await createStation(payload, fetch_);

      expect(calls[0].input).toBe('/api/stations');
      expect(calls[0].init?.method).toBe('POST');
      expect((calls[0].init?.headers as Record<string, string>)['content-type']).toBe(
        'application/json',
      );
      expect(JSON.parse(calls[0].init?.body as string)).toEqual(payload);
      expect(out).toMatchObject({ slug: 's', name: 'S' });
    });

    it('raises ApiError on 400 so the wizard can display the server message', async () => {
      const { fetch_ } = makeSpy({
        ok: false,
        status: 400,
        statusText: 'Bad Request',
        text: 'invalid template',
      });
      const err = await createStation(
        { slug: 'x', name: 'X', template: {} },
        fetch_,
      ).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(400);
      expect(err.message).toContain('invalid template');
    });
  });

  describe('generatePlaylist', () => {
    // The wider `generate panel` Vitest suite mocks $lib/api, so the real
    // body of generatePlaylist never executes there. This block exists to
    // close that gap — the body shape (week, optional seed) and the URL
    // build (slug encoded) are the load-bearing details for both the
    // station-detail page and any future caller.

    it('POSTs week + omits seed when undefined', async () => {
      const { fetch_, calls } = makeSpy({
        body: { playlist_id: 7, trace_id: 't', station: 'cls', week: 1, slot_count: 3010 },
      });
      const out = await generatePlaylist('cls', 1, undefined, fetch_);

      expect(calls[0].input).toBe('/api/stations/cls/generate');
      expect(calls[0].init?.method).toBe('POST');
      expect(JSON.parse(calls[0].init?.body as string)).toEqual({ week: 1 });
      expect(out.playlist_id).toBe(7);
    });

    it('includes seed in the body when provided (rng:on path)', async () => {
      const { fetch_, calls } = makeSpy({
        body: { playlist_id: 8, trace_id: 't', station: 'cls', week: 2, slot_count: 3010 },
      });
      await generatePlaylist('cls', 2, 4242, fetch_);
      expect(JSON.parse(calls[0].init?.body as string)).toEqual({ week: 2, seed: 4242 });
    });

    it('URL-encodes the slug', async () => {
      const { fetch_, calls } = makeSpy({
        body: { playlist_id: 9, trace_id: 't', station: 'a b', week: 1, slot_count: 0 },
      });
      await generatePlaylist('a b', 1, undefined, fetch_);
      expect(calls[0].input).toBe('/api/stations/a%20b/generate');
    });

    it('raises ApiError on engine failure (e.g. pool underflow → 400)', async () => {
      const { fetch_ } = makeSpy({
        ok: false,
        status: 400,
        statusText: 'Bad Request',
        text: 'generation failed: pool underflow at index 985 of RG_RADIO',
      });
      const err = await generatePlaylist('cls', 13, undefined, fetch_).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(400);
      expect(err.message).toContain('pool underflow');
    });
  });

  describe('deletePlaylist', () => {
    // 204 No Content responses have empty bodies — calling .json() on them
    // throws. The `noContent` helper exists exactly to skip that parse;
    // these tests prove it does the right thing on success and still maps
    // failures to ApiError so the UI can render them inline.

    it('issues DELETE /api/playlists/{id} and resolves on 204', async () => {
      const { fetch_, calls } = makeSpy({ status: 204, statusText: 'No Content' });
      await deletePlaylist(42, fetch_);

      expect(calls[0].input).toBe('/api/playlists/42');
      expect(calls[0].init?.method).toBe('DELETE');
    });

    it('raises ApiError on 404 (someone else already deleted, or wrong id)', async () => {
      const { fetch_ } = makeSpy({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: 'playlist not found',
      });
      const err = await deletePlaylist(999, fetch_).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(404);
      expect(err.message).toContain('playlist not found');
    });
  });

  describe('deleteStation', () => {
    it('URL-encodes the slug and issues DELETE /api/stations/{slug}', async () => {
      const { fetch_, calls } = makeSpy({ status: 204, statusText: 'No Content' });
      await deleteStation('a b', fetch_);

      expect(calls[0].input).toBe('/api/stations/a%20b');
      expect(calls[0].init?.method).toBe('DELETE');
    });

    it('raises ApiError on 404 unknown slug', async () => {
      const { fetch_ } = makeSpy({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: "station 'ghost' not found",
      });
      const err = await deleteStation('ghost', fetch_).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(404);
    });
  });

  describe('exportPlaylistToDisk', () => {
    // The trace viewer wires this to the "Export to disk" button (DESIGN
    // §16.6); these tests pin the URL build, the response shape parse, and
    // the ApiError mapping that drives the inline-error rendering.

    it('POSTs to /api/playlists/{id}/export-to-disk and returns {path, bytes}', async () => {
      const { fetch_, calls } = makeSpy({
        body: { path: '/abs/out/cls/001.xml', bytes: 868990 },
      });
      // §16.4.t2 signature change: (id, version, fetch_).
      const r = await exportPlaylistToDisk(42, null, fetch_);

      expect(calls[0].input).toBe('/api/playlists/42/export-to-disk');
      expect(calls[0].init?.method).toBe('POST');
      expect(r.path).toBe('/abs/out/cls/001.xml');
      expect(r.bytes).toBe(868990);
    });

    it('§16.4.t2 — historical-version export appends ?v=N', async () => {
      const { fetch_, calls } = makeSpy({
        body: { path: '/abs/out/cls/001-v3.xml', bytes: 870000 },
      });
      await exportPlaylistToDisk(42, 3, fetch_);
      expect(calls[0].input).toBe('/api/playlists/42/export-to-disk?v=3');
    });

    it('raises ApiError on 404 when the playlist id is unknown', async () => {
      // The backend's `if not p: raise HTTPException(404, ...)` branch.
      // Surfaces the message verbatim — see DESIGN.md §16.6 D3.
      const { fetch_ } = makeSpy({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: 'playlist 999 not found',
      });
      const err = await exportPlaylistToDisk(999, null, fetch_).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(404);
      expect(err.message).toContain('playlist 999 not found');
    });

    it('raises ApiError on 5xx (server-side IO failure)', async () => {
      // FastAPI's default 500 body for an untranslated PermissionError /
      // OSError. The trace viewer renders this as the inline error.
      const { fetch_ } = makeSpy({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        text: 'permission denied: /readonly/cls/001.xml',
      });
      const err = await exportPlaylistToDisk(42, null, fetch_).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(500);
      expect(err.message).toContain('permission denied');
    });
  });

  describe('getMeta', () => {
    // The /api/meta endpoint exposes the canonical option lists for the
    // form dropdowns (DESIGN.md §14.19). Tests pin URL build, response
    // parse, and ApiError mapping so a backend regression breaks here
    // before it cascades into wizard UX failures.

    it('GETs /api/meta and returns the four lists', async () => {
      const { fetch_, calls } = makeSpy({
        body: {
          strategies: ['pick', 'window'],
          rng: ['off', 'on'],
          roles: ['content', 'reference'],
          cadences: ['weekly'],
        },
      });
      const meta = await getMeta(fetch_);

      expect(calls[0].input).toBe('/api/meta');
      expect(calls[0].init).toBeUndefined(); // plain GET
      expect(meta.strategies).toEqual(['pick', 'window']);
      expect(meta.rng).toEqual(['off', 'on']);
      expect(meta.roles).toEqual(['content', 'reference']);
      expect(meta.cadences).toEqual(['weekly']);
    });

    it('raises ApiError on a 5xx so the page can render the error inline', async () => {
      const { fetch_ } = makeSpy({
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        text: 'meta endpoint down',
      });
      const err = await getMeta(fetch_).catch((e) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(503);
      expect(err.message).toContain('meta endpoint down');
    });
  });

  describe('ApiError', () => {
    it('preserves status and name so `instanceof` + `.status` checks in UI work', () => {
      const e = new ApiError(500, 'boom');
      expect(e.status).toBe(500);
      expect(e.name).toBe('ApiError');
      expect(e).toBeInstanceOf(Error);
    });
  });


  // §14.33 review fix — per-station rule CRUD client functions added to
  // api.ts. The CRUD route shipped in §14.29 but the frontend never
  // called it; future UI work (rule-settings panel) builds on these.

  describe('getStationRules', () => {
    it('GETs the per-station rules endpoint with the slug URL-encoded', async () => {
      const fetch_ = (async (url: string) => ({
        ok: true,
        status: 200,
        json: async () => ({ rules: [] }),
      })) as unknown as typeof fetch;
      const spy = vi.fn(fetch_);
      await getStationRules('a b', spy);
      expect(spy).toHaveBeenCalledWith('/api/stations/a%20b/rules');
    });

    it('parses the RuleListResponse shape', async () => {
      const payload = {
        rules: [
          {
            key: 'artist_dedup_window',
            value: 25,
            default_value: 10,
            description: 'X',
            max_value: 1000,
            is_override: true,
          },
        ],
      };
      const fetch_ = (async () => ({
        ok: true,
        status: 200,
        json: async () => payload,
      })) as unknown as typeof fetch;
      const out = await getStationRules('cls', fetch_);
      expect(out.rules[0].is_override).toBe(true);
      expect(out.rules[0].value).toBe(25);
    });
  });

  describe('setStationRule', () => {
    it('PUTs to the rule endpoint with JSON body {value}', async () => {
      const fetch_ = (async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          key: 'artist_dedup_window',
          value: 50,
          default_value: 10,
          description: '',
          max_value: 1000,
          is_override: true,
        }),
      })) as unknown as typeof fetch;
      const spy = vi.fn(fetch_);
      await setStationRule('cls', 'artist_dedup_window', 50, spy);
      const [url, init] = spy.mock.calls[0];
      expect(url).toBe('/api/stations/cls/rules/artist_dedup_window');
      expect((init as RequestInit).method).toBe('PUT');
      expect(JSON.parse((init as RequestInit).body as string)).toEqual({ value: 50 });
    });

    it('surfaces ApiError on 400 (bounds violation)', async () => {
      const fetch_ = (async () => ({
        ok: false,
        status: 400,
        statusText: 'Bad Request',
        text: async () => '{"detail":"value must be at most 1000, got 9999"}',
      })) as unknown as typeof fetch;
      const err = await setStationRule('cls', 'artist_dedup_window', 9999, fetch_).catch(
        (e) => e,
      );
      expect(err).toBeInstanceOf(ApiError);
      expect(err.status).toBe(400);
    });
  });

  describe('clearStationRule', () => {
    it('DELETEs the rule endpoint and returns void on 204', async () => {
      const fetch_ = (async () => ({
        ok: true,
        status: 204,
      })) as unknown as typeof fetch;
      const spy = vi.fn(fetch_);
      const out = await clearStationRule('cls', 'artist_dedup_window', spy);
      expect(out).toBeUndefined();
      const [url, init] = spy.mock.calls[0];
      expect(url).toBe('/api/stations/cls/rules/artist_dedup_window');
      expect((init as RequestInit).method).toBe('DELETE');
    });

    it('idempotent — 204 even when no override existed', async () => {
      const fetch_ = (async () => ({
        ok: true,
        status: 204,
      })) as unknown as typeof fetch;
      await expect(clearStationRule('cls', 'mix_exclusion_weeks', fetch_)).resolves.toBeUndefined();
    });
  });
});


// ---- §16.11 c4 — async job runner helpers ----------------------------


/**
 * Builds a spy fetch that returns the given list of responses in
 * sequence (one per call). Each call past the end of the list
 * triggers the test-failure assertion. Used by `watchJob` tests
 * where multiple polls each see a different snapshot.
 */
function makeSequencedSpy(
  responses: Array<{
    ok?: boolean;
    status?: number;
    body?: unknown;
    text?: string;
  }>,
) {
  const calls: SpyCall[] = [];
  let i = 0;
  const fetch_ = async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ input, init });
    if (i >= responses.length) {
      throw new Error(`spy fetch called past end of sequence (#${i})`);
    }
    const r = responses[i++];
    return {
      ok: r.ok ?? true,
      status: r.status ?? 200,
      statusText: 'OK',
      json: async () => r.body,
      text: async () => r.text ?? '',
    } as unknown as Response;
  };
  return { fetch_, calls };
}

function fakeSnap(over: Partial<JobSnapshot> = {}): JobSnapshot {
  return {
    job_id: 'j-1',
    slug: 'ts',
    kind: 'generate',
    status: 'running',
    phase: 'queued',
    current: 0,
    total: 10,
    label: null,
    result: null,
    error: null,
    started_at: '2026-05-27T10:00:00Z',
    finished_at: null,
    ...over,
  };
}


describe('startGenerateJob', () => {
  it('POSTs the week + seed body and returns {job_id, status}', async () => {
    const { fetch_, calls } = makeSpy({
      status: 201,
      body: { job_id: 'abc123', status: 'running' },
    });
    const out = await startGenerateJob('cls', 5, 42, fetch_);
    expect(out).toEqual({ job_id: 'abc123', status: 'running' });
    expect(calls[0].input).toBe('/api/stations/cls/jobs/generate');
    const init = calls[0].init!;
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ week: 5, seed: 42 });
  });

  it('omits seed from the body when not passed', async () => {
    const { fetch_, calls } = makeSpy({
      status: 201,
      body: { job_id: 'abc', status: 'running' },
    });
    await startGenerateJob('cls', 1, undefined, fetch_);
    expect(JSON.parse(calls[0].init!.body as string)).toEqual({ week: 1 });
  });

  it('URL-encodes the slug', async () => {
    const { fetch_, calls } = makeSpy({
      status: 201,
      body: { job_id: 'x', status: 'running' },
    });
    await startGenerateJob('a b', 1, undefined, fetch_);
    expect(calls[0].input).toBe('/api/stations/a%20b/jobs/generate');
  });

  it('throws JobStartConflict carrying existing_job_id on 409', async () => {
    const { fetch_ } = makeSpy({
      ok: false,
      status: 409,
      body: {
        detail: {
          message: 'a job for station ts is already running',
          existing_job_id: 'older-job-99',
        },
      },
    });
    const err = await startGenerateJob('ts', 1, undefined, fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(JobStartConflict);
    expect(err).toBeInstanceOf(ApiError); // also a regular ApiError
    expect(err.existing_job_id).toBe('older-job-99');
    expect(err.message).toContain('already running');
    expect(err.status).toBe(409);
  });

  it('throws ApiError on unexpected 4xx', async () => {
    const { fetch_ } = makeSpy({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      text: '{"detail":"station nope not found"}',
    });
    const err = await startGenerateJob('nope', 1, undefined, fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    // Plain ApiError, not JobStartConflict.
    expect(err).not.toBeInstanceOf(JobStartConflict);
  });
});


// §16.2.6 c2 — startPreviewJob hits /jobs/preview with the
// editor's IN-MEMORY template + sidecar. Server-recomputed
// inputs (excluded_mix_ids, previous_playlist, alias_map) are
// deliberately ABSENT from the request body — pinning this
// shape protects the recompute-per-request guarantee.
describe('startPreviewJob', () => {
  const stubTemplate = { station: 's', name: 'S' } as Record<string, unknown>;
  const stubSidecar = {
    schema_version: 1, sections: [], layout_order: [],
  } as Record<string, unknown>;

  it('POSTs template + sidecar + week (+ seed when supplied) to /jobs/preview', async () => {
    const { fetch_, calls } = makeSpy({
      status: 201,
      body: { job_id: 'pv-abc', status: 'running' },
    });
    const out = await startPreviewJob(
      'cls', stubTemplate, stubSidecar, 3, 99, fetch_,
    );
    expect(out).toEqual({ job_id: 'pv-abc', status: 'running' });
    expect(calls[0].input).toBe('/api/stations/cls/jobs/preview');
    const init = calls[0].init!;
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      template: stubTemplate,
      sidecar: stubSidecar,
      week: 3,
      seed: 99,
    });
    // Recompute-per-request guarantee — server-derived inputs
    // are NOT in the body.
    expect(body).not.toHaveProperty('excluded_mix_ids');
    expect(body).not.toHaveProperty('previous_playlist');
    expect(body).not.toHaveProperty('alias_map');
  });

  it('omits seed from the body when not passed', async () => {
    const { fetch_, calls } = makeSpy({
      status: 201,
      body: { job_id: 'pv', status: 'running' },
    });
    await startPreviewJob(
      'cls', stubTemplate, stubSidecar, 1, undefined, fetch_,
    );
    const body = JSON.parse(calls[0].init!.body as string);
    expect(body).toEqual({
      template: stubTemplate, sidecar: stubSidecar, week: 1,
    });
    expect(body).not.toHaveProperty('seed');
  });

  it('URL-encodes the slug', async () => {
    const { fetch_, calls } = makeSpy({
      status: 201,
      body: { job_id: 'pv', status: 'running' },
    });
    await startPreviewJob(
      'a b/c', stubTemplate, stubSidecar, 1, undefined, fetch_,
    );
    expect(calls[0].input).toBe('/api/stations/a%20b%2Fc/jobs/preview');
  });

  it('throws ApiError on 400 (invalid template/sidecar from operator edit)', async () => {
    const { fetch_ } = makeSpy({
      ok: false,
      status: 400,
      text: '{"detail":"invalid sidecar (grammar): missing layout_order"}',
    });
    const err = await startPreviewJob(
      'ts', stubTemplate, stubSidecar, 1, undefined, fetch_,
    ).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });
});


describe('pollJob', () => {
  it('GETs /api/jobs/{id} and returns the snapshot', async () => {
    const snap = fakeSnap({ status: 'running', phase: 'picks', current: 50 });
    const { fetch_, calls } = makeSpy({ body: snap });
    const out = await pollJob('j-1', fetch_);
    expect(out).toEqual(snap);
    expect(calls[0].input).toBe('/api/jobs/j-1');
  });

  it('returns null on 404 (job GC\'d / server restarted)', async () => {
    const { fetch_ } = makeSpy({ ok: false, status: 404, text: 'gone' });
    const out = await pollJob('j-1', fetch_);
    expect(out).toBeNull();
  });

  it('URL-encodes the job_id', async () => {
    const { fetch_, calls } = makeSpy({ body: fakeSnap() });
    await pollJob('weird id/with slash', fetch_);
    expect(calls[0].input).toBe('/api/jobs/weird%20id%2Fwith%20slash');
  });

  it('raises ApiError on 5xx', async () => {
    const { fetch_ } = makeSpy({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      text: 'boom',
    });
    const err = await pollJob('j-1', fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });
});


describe('cancelJob', () => {
  it('DELETEs /api/jobs/{id} and resolves to void on 204', async () => {
    const { fetch_, calls } = makeSpy({ ok: true, status: 204 });
    const out = await cancelJob('j-1', fetch_);
    expect(out).toBeUndefined();
    expect(calls[0].input).toBe('/api/jobs/j-1');
    expect(calls[0].init!.method).toBe('DELETE');
  });

  it('absorbs 404 (already-GC\'d job) as a no-op', async () => {
    // Cancel-after-GC has the same end state the caller wanted.
    // Helper must NOT throw — UI race against fast-finishing job
    // would otherwise show a spurious "couldn't cancel" toast.
    const { fetch_ } = makeSpy({ ok: false, status: 404 });
    await expect(cancelJob('j-1', fetch_)).resolves.toBeUndefined();
  });

  it('throws ApiError on other non-2xx', async () => {
    const { fetch_ } = makeSpy({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      text: 'kaboom',
    });
    const err = await cancelJob('j-1', fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });
});


describe('getStationSidecar', () => {
  /** §16.2.2.t1 — getStationSidecar now parses+validates the
   *  response. Fixtures must include the minimum shape the
   *  validator requires (template.station / .name / .sources / .layout
   *  + sidecar.schema_version=1 / .sections[] / .layout_order[]). */
  function validBody(): {
    template: Record<string, unknown>;
    sidecar: Record<string, unknown>;
    sidecar_error: string | null;
  } {
    return {
      template: {
        station: 's', name: 'S',
        sources: [{ name: 'A', colour: '#abc', role: 'content' }],
        layout: { unit: 'slot', unit_labels: [], row_start_offsets: [] },
      },
      sidecar: { schema_version: 1, sections: [], macros: [], layout_order: [] },
      sidecar_error: null,
    };
  }

  it('GETs /api/stations/{slug}/sidecar and returns the bundle', async () => {
    const body = validBody();
    const { fetch_, calls } = makeSpy({ body });
    const out = await getStationSidecar('s', fetch_);
    expect(calls[0].input).toBe('/api/stations/s/sidecar');
    // Returned shape is the PARSED one — same keys, but `template`
    // and `sidecar` carry the canvas-ready typed shapes.
    expect(out.template.station).toBe('s');
    expect(out.sidecar.schema_version).toBe(1);
    expect(out.sidecar_error).toBeNull();
  });

  it('URL-encodes the slug', async () => {
    const { fetch_, calls } = makeSpy({ body: validBody() });
    await getStationSidecar('a b', fetch_);
    expect(calls[0].input).toBe('/api/stations/a%20b/sidecar');
  });

  it('surfaces 404 station-not-found as ApiError', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 404, statusText: 'Not Found',
      text: '{"detail":"station nope not found"}',
    });
    const err = await getStationSidecar('nope', fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });

  it('does NOT throw on sidecar_error — that comes back in the 200 body', async () => {
    // Decision E contract: sidecar errors are non-HTTP-error data.
    const body = validBody();
    body.sidecar_error = 'your sidecar is malformed at line 3';
    const { fetch_ } = makeSpy({ body });
    const out = await getStationSidecar('broken', fetch_);
    expect(out.sidecar_error).toContain('malformed');
  });

  it('throws when the server returns a shape that fails validation', async () => {
    // §16.2.2.t1 regression guard: drift between server payload and
    // TS interfaces surfaces LOUDLY, not as a silent canvas crash
    // downstream. Caught at the network layer + bubbled out of
    // `getStationSidecar` for the route to render inline.
    const { fetch_ } = makeSpy({
      body: { template: { /* missing station/name/sources */ }, sidecar: {} },
    });
    await expect(getStationSidecar('drift', fetch_)).rejects.toThrow(
      /template\.station/,
    );
  });
});


describe('watchJob', () => {
  it('yields every snapshot until status flips to done', async () => {
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({ status: 'running', phase: 'queued', current: 0 }) },
      { body: fakeSnap({ status: 'running', phase: 'picks', current: 50 }) },
      { body: fakeSnap({ status: 'done', phase: 'picks', current: 100,
                         finished_at: '2026-05-27T10:00:05Z',
                         result: {
                           playlist_id: 7, trace_id: 't', station: 'ts',
                           week: 1, slot_count: 100,
                         } }) },
    ]);
    const sleep = vi.fn(async () => {});
    const snaps: JobSnapshot[] = [];
    for await (const snap of watchJob('j-1', { initialIntervalMs: 500, maxIntervalMs: 1000, fetch_, sleepFn: sleep })) {
      snaps.push(snap);
    }
    expect(snaps.map((s) => [s.status, s.current])).toEqual([
      ['running', 0],
      ['running', 50],
      ['done', 100],
    ]);
    // Sleep called BETWEEN polls (2x, not before the first or after
    // the last terminal poll). §16.11.t2: adaptive backoff doubles
    // the interval each iteration, capped at maxIntervalMs. With
    // initial=500 + max=1000, the schedule is 500 then 1000.
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenNthCalledWith(1, 500);
    expect(sleep).toHaveBeenNthCalledWith(2, 1000);
  });

  it('§16.11.t2 review K1: zero-args uses 250/1000 defaults (production callers do this)', async () => {
    // Both production callers (`+page.svelte` detail view +
    // visualise live-preview) call `watchJob(jobId)` with no
    // options. Pin the defaults so an accidental swap in the
    // function signature can't silently change production cadence.
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({ status: 'running', current: 0 }) },
      { body: fakeSnap({ status: 'running', current: 50 }) },
      { body: fakeSnap({ status: 'done', current: 100,
                         finished_at: '2026-05-27T10:00:05Z',
                         result: { playlist_id: 7, trace_id: 't',
                                   station: 'ts', week: 1, slot_count: 100 } }) },
    ]);
    const sleep = vi.fn(async () => {});
    // Inject fetch_ + sleepFn but leave the interval defaults alone.
    for await (const _ of watchJob('j-1', { fetch_, sleepFn: sleep })) {
      // drain
    }
    // Default initial=250, max=1000 → schedule 250 then 500.
    expect(sleep).toHaveBeenNthCalledWith(1, 250);
    expect(sleep).toHaveBeenNthCalledWith(2, 500);
  });

  it('stops cleanly when the job 404s mid-poll (yields nothing on 404)', async () => {
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({ status: 'running' }) },
      { ok: false, status: 404, text: 'gone' },
    ]);
    const sleep = vi.fn(async () => {});
    const snaps: JobSnapshot[] = [];
    for await (const snap of watchJob('j-1', { initialIntervalMs: 500, maxIntervalMs: 1000, fetch_, sleepFn: sleep })) {
      snaps.push(snap);
    }
    // First snapshot was yielded; second poll 404'd → generator
    // returns silently (consumer sees a clean for-await exit).
    expect(snaps.map((s) => s.status)).toEqual(['running']);
  });

  it('terminates immediately on error status', async () => {
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({
          status: 'error',
          phase: 'picks',
          current: 12,
          finished_at: '2026-05-27T10:00:02Z',
          error: { message: 'engine crashed', kind: 'RuntimeError' },
        }) },
    ]);
    const sleep = vi.fn(async () => {});
    const snaps: JobSnapshot[] = [];
    for await (const snap of watchJob('j-1', { initialIntervalMs: 500, maxIntervalMs: 1000, fetch_, sleepFn: sleep })) {
      snaps.push(snap);
    }
    expect(snaps.length).toBe(1);
    expect(snaps[0].status).toBe('error');
    expect(snaps[0].error?.kind).toBe('RuntimeError');
    expect(sleep).not.toHaveBeenCalled();
  });

  it('terminates immediately on cancelled status', async () => {
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({ status: 'cancelled' }) },
    ]);
    const sleep = vi.fn(async () => {});
    const snaps: JobSnapshot[] = [];
    for await (const snap of watchJob('j-1', { initialIntervalMs: 500, maxIntervalMs: 1000, fetch_, sleepFn: sleep })) {
      snaps.push(snap);
    }
    expect(snaps[0].status).toBe('cancelled');
  });

  it('§16.11.t2: adaptive backoff — interval doubles each running poll, capped at maxIntervalMs', async () => {
    // 5 running polls + 1 terminal: 4 sleeps between them.
    // With initial=250 + max=1000, the doubling schedule is:
    // 250 → 500 → 1000 → 1000 → 1000 (capped).
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({ status: 'running', current: 0 }) },
      { body: fakeSnap({ status: 'running', current: 20 }) },
      { body: fakeSnap({ status: 'running', current: 40 }) },
      { body: fakeSnap({ status: 'running', current: 60 }) },
      { body: fakeSnap({ status: 'running', current: 80 }) },
      { body: fakeSnap({ status: 'done', current: 100,
                         finished_at: '2026-05-27T10:00:05Z',
                         result: { playlist_id: 7, trace_id: 't',
                                   station: 'ts', week: 1, slot_count: 100 } }) },
    ]);
    const sleep = vi.fn(async () => {});
    const snaps: JobSnapshot[] = [];
    for await (const snap of watchJob('j-1', { initialIntervalMs: 250, maxIntervalMs: 1000, fetch_, sleepFn: sleep })) {
      snaps.push(snap);
    }
    expect(snaps).toHaveLength(6);
    // 5 sleeps between 6 polls.
    expect(sleep).toHaveBeenCalledTimes(5);
    expect(sleep).toHaveBeenNthCalledWith(1, 250);
    expect(sleep).toHaveBeenNthCalledWith(2, 500);
    expect(sleep).toHaveBeenNthCalledWith(3, 1000);
    expect(sleep).toHaveBeenNthCalledWith(4, 1000);
    expect(sleep).toHaveBeenNthCalledWith(5, 1000);
  });

  it('§16.11.t2: initialIntervalMs > maxIntervalMs is clamped (defensive cap)', async () => {
    // If a caller passes initial=2000 + max=500, clamp initial to 500
    // — protects against silly inputs.
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({ status: 'running' }) },
      { body: fakeSnap({ status: 'done',
                         finished_at: '2026-05-27T10:00:05Z',
                         result: { playlist_id: 7, trace_id: 't',
                                   station: 'ts', week: 1, slot_count: 100 } }) },
    ]);
    const sleep = vi.fn(async () => {});
    for await (const _ of watchJob('j-1', { initialIntervalMs: 2000, maxIntervalMs: 500, fetch_, sleepFn: sleep })) {
      // drain
    }
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(sleep).toHaveBeenCalledWith(500);
  });

  it('§16.11.t2 review K4: maxIntervalMs <= 0 falls back to the default cap (defensive)', async () => {
    // Silly input: maxIntervalMs = 0 → sleep(0) tight-loop without
    // the guard. We restore the default cap (1000) so the server
    // can't be DoS'd by a typo.
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({ status: 'running' }) },
      { body: fakeSnap({ status: 'done',
                         finished_at: '2026-05-27T10:00:05Z',
                         result: { playlist_id: 7, trace_id: 't',
                                   station: 'ts', week: 1, slot_count: 100 } }) },
    ]);
    const sleep = vi.fn(async () => {});
    for await (const _ of watchJob('j-1', { initialIntervalMs: 300, maxIntervalMs: 0, fetch_, sleepFn: sleep })) {
      // drain
    }
    // initial=300 clamped against the restored cap (1000) → 300
    // is fine; sleep called once with 300.
    expect(sleep).toHaveBeenCalledTimes(1);
    expect(sleep).toHaveBeenCalledWith(300);
  });

  it('propagates non-404 fetch errors out of the generator', async () => {
    const { fetch_ } = makeSequencedSpy([
      { ok: false, status: 500, text: 'engine wedged' },
    ]);
    const sleep = vi.fn(async () => {});
    const err = await (async () => {
      try {
        for await (const _ of watchJob('j-1', { initialIntervalMs: 500, maxIntervalMs: 1000, fetch_, sleepFn: sleep })) {
          // unreachable — pollJob raises before any yield
        }
      } catch (e) {
        return e;
      }
    })();
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
  });
});


// ---- §16.2.3 c2 — patchBlock + exportStationTemplate -----------------


describe('patchBlock', () => {
  it('PATCHes /api/stations/{slug}/blocks with address + fields + token', async () => {
    const { fetch_, calls } = makeSpy({
      body: { block: { strategy: 'mix', genre: 'New' }, updated_at: 't2' },
    });
    const out = await patchBlock(
      'cls', { slot_index: 3 }, { genre: 'New' }, 't1', fetch_,
    );
    expect(calls[0].input).toBe('/api/stations/cls/blocks');
    const init = calls[0].init!;
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body as string)).toEqual({
      address: { slot_index: 3 },
      fields: { genre: 'New' },
      expected_updated_at: 't1',
    });
    expect(out.updated_at).toBe('t2');
    expect(out.block.genre).toBe('New');
  });

  it('URL-encodes the slug', async () => {
    const { fetch_, calls } = makeSpy({ body: { block: {}, updated_at: null } });
    await patchBlock('a b', { slot_index: 0 }, {}, null, fetch_);
    expect(calls[0].input).toBe('/api/stations/a%20b/blocks');
  });

  it('throws BlockPatchConflict carrying current_updated_at on 409', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 409,
      body: { detail: { message: 'station changed', current_updated_at: 't9' } },
    });
    const err = await patchBlock('cls', { slot_index: 0 }, { genre: 'X' }, 't1', fetch_)
      .catch((e) => e);
    expect(err).toBeInstanceOf(BlockPatchConflict);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.current_updated_at).toBe('t9');
    expect(err.status).toBe(409);
  });

  it('throws plain ApiError on a 400 parameter-only / invalid-edit reject', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 400, statusText: 'Bad Request',
      text: '{"detail":"fields [\'size\'] are not editable for strategy ..."}',
    });
    const err = await patchBlock('cls', { slot_index: 0 }, { size: 5 } as never, 't1', fetch_)
      .catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).not.toBeInstanceOf(BlockPatchConflict);
    expect(err.status).toBe(400);
  });

  it('sends block_idx address for row_template edits', async () => {
    const { fetch_, calls } = makeSpy({ body: { block: {}, updated_at: 't2' } });
    await patchBlock('cls', { block_idx: 1 }, { genre: 'Night' }, 't1', fetch_);
    expect(JSON.parse(calls[0].init!.body as string).address).toEqual({ block_idx: 1 });
  });

  it('allowlist_sources rides through as a JSON array (§16.2.4 D3 + c2 review #2)', async () => {
    // Slice-review sweep: pin the wire shape for the string_list
    // field type. c2 review #2 widened the fields union to include
    // `string[]`; without this regression a future refactor that
    // re-narrows the type would silently coerce the array to a
    // string (the original `as unknown as string` cast did exactly
    // that on the DetailPanel side).
    const { fetch_, calls } = makeSpy({ body: { block: {}, updated_at: 't2' } });
    await patchBlock(
      'cls', { slot_index: 0 },
      { allowlist_sources: ['REFERENCE_ARCHIVE', 'REFERENCE_CLASSICS'] },
      't1', fetch_,
    );
    const body = JSON.parse(calls[0].init!.body as string);
    expect(body.fields.allowlist_sources).toEqual(
      ['REFERENCE_ARCHIVE', 'REFERENCE_CLASSICS'],
    );
    expect(Array.isArray(body.fields.allowlist_sources)).toBe(true);
  });
});


// ---- §16.2.4 c2 — retypeBlock + patchMacro ---------------------------


describe('retypeBlock', () => {
  it('PATCHes /api/stations/{slug}/blocks/retype with all four payload keys', async () => {
    const { fetch_, calls } = makeSpy({
      body: { block: { strategy: 'archive_ref', source: 'REF', position: 1 }, updated_at: 't2' },
    });
    const out = await retypeBlock(
      'cls', { slot_index: 3 }, 'archive_ref', 'REF', { position: 1 }, 't1', fetch_,
    );
    expect(calls[0].input).toBe('/api/stations/cls/blocks/retype');
    expect(calls[0].init!.method).toBe('PATCH');
    expect(JSON.parse(calls[0].init!.body as string)).toEqual({
      address: { slot_index: 3 },
      new_strategy: 'archive_ref',
      new_source: 'REF',
      new_fields: { position: 1 },
      expected_updated_at: 't1',
    });
    expect(out.block.strategy).toBe('archive_ref');
    expect(out.updated_at).toBe('t2');
  });

  it('null new_strategy / new_source pass through (partial retype)', async () => {
    const { fetch_, calls } = makeSpy({ body: { block: {}, updated_at: 't2' } });
    await retypeBlock('cls', { slot_index: 0 }, null, 'B', {}, 't1', fetch_);
    expect(JSON.parse(calls[0].init!.body as string)).toMatchObject({
      new_strategy: null,
      new_source: 'B',
      new_fields: {},
    });
  });

  it('throws BlockPatchConflict on 409 (same contract as patchBlock)', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 409,
      body: { detail: { message: 'station changed', current_updated_at: 't9' } },
    });
    const err = await retypeBlock('cls', { slot_index: 0 }, 'pick', null, {}, 't1', fetch_)
      .catch((e) => e);
    expect(err).toBeInstanceOf(BlockPatchConflict);
    expect(err.current_updated_at).toBe('t9');
  });

  it('URL-encodes the slug', async () => {
    const { fetch_, calls } = makeSpy({ body: { block: {}, updated_at: null } });
    await retypeBlock('a b', { slot_index: 0 }, 'pick', null, {}, null, fetch_);
    expect(calls[0].input).toBe('/api/stations/a%20b/blocks/retype');
  });
});


describe('patchMacro', () => {
  it('PATCHes /api/stations/{slug}/macros/{macro_id} with fields + token', async () => {
    const { fetch_, calls } = makeSpy({
      body: { blocks_updated: 3, updated_at: 't2' },
    });
    const out = await patchMacro(
      'cls', 'jingle-1', { genre: 'Loud' }, 't1', fetch_,
    );
    expect(calls[0].input).toBe('/api/stations/cls/macros/jingle-1');
    expect(calls[0].init!.method).toBe('PATCH');
    expect(JSON.parse(calls[0].init!.body as string)).toEqual({
      fields: { genre: 'Loud' },
      expected_updated_at: 't1',
    });
    expect(out.blocks_updated).toBe(3);
    expect(out.updated_at).toBe('t2');
  });

  it('URL-encodes BOTH slug and macro_id', async () => {
    const { fetch_, calls } = makeSpy({ body: { blocks_updated: 0, updated_at: null } });
    await patchMacro('a b', 'm/1', {}, null, fetch_);
    expect(calls[0].input).toBe('/api/stations/a%20b/macros/m%2F1');
  });

  it('throws BlockPatchConflict on 409', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 409,
      body: { detail: { message: 'station changed', current_updated_at: 't9' } },
    });
    const err = await patchMacro('cls', 'm', { genre: 'X' }, 't1', fetch_)
      .catch((e) => e);
    expect(err).toBeInstanceOf(BlockPatchConflict);
    expect(err.current_updated_at).toBe('t9');
  });

  it('raises ApiError on 404 unknown macro_id', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 404,
      text: '{"detail":"macro_id \'ghost\' not found"}',
    });
    const err = await patchMacro('cls', 'ghost', { genre: 'X' }, 't1', fetch_)
      .catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).not.toBeInstanceOf(BlockPatchConflict);
    expect(err.status).toBe(404);
  });
});


// ---- §16.2.5 c2 — section CRUD + move + structural --------------------


describe('createSection', () => {
  it('POSTs /api/stations/{slug}/sections with id + name + token', async () => {
    const { fetch_, calls } = makeSpy({
      body: {
        sidecar: { schema_version: 1, sections: [], macros: [], layout_order: [] },
        updated_at: 't2',
      },
    });
    const out = await createSection('cls', 'mornings', 'Mornings', 't1', fetch_);
    expect(calls[0].input).toBe('/api/stations/cls/sections');
    expect(calls[0].init!.method).toBe('POST');
    expect(JSON.parse(calls[0].init!.body as string)).toEqual({
      id: 'mornings', name: 'Mornings', expected_updated_at: 't1',
    });
    expect(out.updated_at).toBe('t2');
  });

  it('null name passes through', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: null },
    });
    await createSection('cls', 'x', null, null, fetch_);
    expect(JSON.parse(calls[0].init!.body as string).name).toBeNull();
  });

  it('409 → BlockPatchConflict with current_updated_at', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 409,
      body: { detail: { message: 'stale', current_updated_at: 't9' } },
    });
    const err = await createSection('cls', 'x', null, 't1', fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(BlockPatchConflict);
    expect(err.current_updated_at).toBe('t9');
  });
});


describe('renameSection', () => {
  it('PATCHes /sections/{id} with name + token', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: 't2' },
    });
    await renameSection('cls', 'lead', 'Opening', 't1', fetch_);
    expect(calls[0].input).toBe('/api/stations/cls/sections/lead');
    expect(calls[0].init!.method).toBe('PATCH');
    expect(JSON.parse(calls[0].init!.body as string)).toEqual({
      name: 'Opening', expected_updated_at: 't1',
    });
  });

  it('URL-encodes both slug + section_id', async () => {
    const { fetch_, calls } = makeSpy({ body: { sidecar: {}, updated_at: null } });
    await renameSection('a b', 'my/section', 'X', null, fetch_);
    expect(calls[0].input).toBe('/api/stations/a%20b/sections/my%2Fsection');
  });
});


describe('deleteSection', () => {
  it('DELETEs with expected_updated_at as query string', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: 't2' },
    });
    await deleteSection('cls', 'lead', 't1', fetch_);
    expect(calls[0].input).toBe(
      '/api/stations/cls/sections/lead?expected_updated_at=t1',
    );
    expect(calls[0].init!.method).toBe('DELETE');
    // DELETE bodies aren't universally supported → no body sent.
    expect(calls[0].init!.body).toBeUndefined();
  });

  it('omits qs entirely when expected_updated_at is null', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: null },
    });
    await deleteSection('cls', 'lead', null, fetch_);
    expect(calls[0].input).toBe('/api/stations/cls/sections/lead');
  });

  it('URL-encodes the token (handles + and = safely)', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: null },
    });
    await deleteSection('cls', 'lead', '2026-06-08T07:30:00+00:00', fetch_);
    expect(calls[0].input).toContain('expected_updated_at=2026-06-08T07%3A30%3A00%2B00%3A00');
  });
});


describe('reorderSections', () => {
  it('PUTs /sections/order with the full layout_order array', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: 't2' },
    });
    await reorderSections('cls', ['b', 'a'], 't1', fetch_);
    expect(calls[0].input).toBe('/api/stations/cls/sections/order');
    expect(calls[0].init!.method).toBe('PUT');
    expect(JSON.parse(calls[0].init!.body as string)).toEqual({
      layout_order: ['b', 'a'], expected_updated_at: 't1',
    });
  });
});


describe('moveBlock', () => {
  it('PATCHes /blocks/move with address + target_section + index', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: 't2' },
    });
    await moveBlock('cls', { slot_index: 3 }, 'tail', 0, 't1', fetch_);
    expect(calls[0].input).toBe('/api/stations/cls/blocks/move');
    expect(JSON.parse(calls[0].init!.body as string)).toEqual({
      address: { slot_index: 3 },
      target_section_id: 'tail',
      target_index: 0,
      expected_updated_at: 't1',
    });
  });

  it('null target_index passes through (server appends)', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: null },
    });
    await moveBlock('cls', { slot_index: 0 }, 'tail', null, null, fetch_);
    expect(JSON.parse(calls[0].init!.body as string).target_index).toBeNull();
  });
});


describe('structuralBlockEdit', () => {
  it('PATCHes /blocks/structure with edit + token (size op)', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: 't2' },
    });
    await structuralBlockEdit(
      'cls',
      { op: 'size', slot_index: 0, new_size: 5 },
      't1', fetch_,
    );
    expect(calls[0].input).toBe('/api/stations/cls/blocks/structure');
    expect(JSON.parse(calls[0].init!.body as string)).toEqual({
      edit: { op: 'size', slot_index: 0, new_size: 5 },
      expected_updated_at: 't1',
    });
  });

  it('add op carries new_block payload', async () => {
    const { fetch_, calls } = makeSpy({
      body: { sidecar: {}, updated_at: null },
    });
    await structuralBlockEdit(
      'cls',
      {
        op: 'add', slot_index: 2,
        new_block: { source: 'A', strategy: 'jingle', size: 1, genre: 'X' },
      },
      null, fetch_,
    );
    const body = JSON.parse(calls[0].init!.body as string);
    expect(body.edit.op).toBe('add');
    expect(body.edit.new_block.genre).toBe('X');
  });

  it('409 → BlockPatchConflict', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 409,
      body: { detail: { message: 'stale', current_updated_at: 't9' } },
    });
    const err = await structuralBlockEdit(
      'cls', { op: 'remove', slot_index: 0 }, 't1', fetch_,
    ).catch((e) => e);
    expect(err).toBeInstanceOf(BlockPatchConflict);
  });
});


describe('exportStationTemplate', () => {
  it('POSTs /api/stations/{slug}/export and returns {path, bytes}', async () => {
    const { fetch_, calls } = makeSpy({
      body: { path: '/repo/stations/cls.json', bytes: 1234 },
    });
    const out = await exportStationTemplate('cls', fetch_);
    expect(calls[0].input).toBe('/api/stations/cls/export');
    expect(calls[0].init!.method).toBe('POST');
    expect(out).toEqual({ path: '/repo/stations/cls.json', bytes: 1234 });
  });

  it('raises ApiError on 404 unknown station', async () => {
    const { fetch_ } = makeSpy({
      ok: false, status: 404, statusText: 'Not Found', text: 'nope',
    });
    const err = await exportStationTemplate('nope', fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});


describe('getStationSidecar — updated_at token (§16.2.3)', () => {
  it('surfaces the updated_at optimistic token', async () => {
    const { fetch_ } = makeSpy({
      body: {
        template: {
          station: 's', name: 'S',
          sources: [{ name: 'A', role: 'content' }],
          layout: { unit: 'slot', unit_labels: [], row_start_offsets: [] },
        },
        sidecar: { schema_version: 1, sections: [], macros: [], layout_order: [] },
        sidecar_error: null,
        updated_at: '2026-05-29T12:00:00+00:00',
      },
    });
    const out = await getStationSidecar('s', fetch_);
    expect(out.updated_at).toBe('2026-05-29T12:00:00+00:00');
  });

  it('treats an absent updated_at as null', async () => {
    const { fetch_ } = makeSpy({
      body: {
        template: {
          station: 's', name: 'S',
          sources: [{ name: 'A', role: 'content' }],
          layout: { unit: 'slot', unit_labels: [], row_start_offsets: [] },
        },
        sidecar: { schema_version: 1, sections: [], macros: [], layout_order: [] },
        sidecar_error: null,
      },
    });
    const out = await getStationSidecar('s', fetch_);
    expect(out.updated_at).toBeNull();
  });
});


// ---- §16.19 — getStationOverview -------------------------------------


describe('getStationOverview', () => {
  function overviewBody() {
    return {
      slug: 's', name: 'S', cadence: 'weekly', rng: 'off',
      updated_at: '2026-05-29T12:00:00+00:00',
      template_summary: {
        slot_count: 100, layout_form: 'flat_slots',
        strategy_counts: { pick: 80, mix: 20 },
        cursor_persist: false, source_count: 2,
      },
      pools: [
        { name: 'A', role: 'content', imported: true, track_count: 50, imported_at: '2026-05-01T00:00:00+00:00' },
        { name: 'B', role: 'content', imported: false, track_count: null, imported_at: null },
      ],
      cursors: [],
      generations: { count: 3, last_generated_week: 3, last_generated_at: '2026-05-20T00:00:00+00:00', stale_count: 1 },
    };
  }

  it('GETs /api/stations/{slug}/overview and returns the bundle', async () => {
    const { fetch_, calls } = makeSpy({ body: overviewBody() });
    const out = await getStationOverview('s', fetch_);
    expect(calls[0].input).toBe('/api/stations/s/overview');
    expect(out.template_summary.slot_count).toBe(100);
    expect(out.pools[1].imported).toBe(false);
    expect(out.generations.stale_count).toBe(1);
  });

  it('URL-encodes the slug', async () => {
    const { fetch_, calls } = makeSpy({ body: overviewBody() });
    await getStationOverview('a b', fetch_);
    expect(calls[0].input).toBe('/api/stations/a%20b/overview');
  });

  it('raises ApiError on 404', async () => {
    const { fetch_ } = makeSpy({ ok: false, status: 404, text: 'nope' });
    const err = await getStationOverview('nope', fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});


// ---- §16.25 — source-pool helpers ------------------------------------


describe('listSources', () => {
  it('GETs /api/sources and returns the list verbatim', async () => {
    const rows = [
      { id: 1, name: 'DAYTIME', role: 'content', track_count: 247 },
      { id: 2, name: 'JINGLES', role: 'content', track_count: 12 },
    ];
    const { fetch_, calls } = makeSpy({ body: rows });
    const out = await listSources(fetch_);
    expect(calls[0].input).toBe('/api/sources');
    expect(out).toEqual(rows);
  });

  it('raises ApiError on 500', async () => {
    const { fetch_ } = makeSpy({ ok: false, status: 500, text: 'oh no' });
    const err = await listSources(fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(500);
  });
});


describe('uploadSource', () => {
  function fakeFile() {
    return new File(['<plist></plist>'], 'tiny.xml', { type: 'application/xml' });
  }

  it('POSTs FormData (name + role + file) to /api/sources/upload', async () => {
    const summary = { name: 'TINY', role: 'content', tracks_imported: 1, entries_imported: 1 };
    const { fetch_, calls } = makeSpy({ body: summary });
    const out = await uploadSource('TINY', 'content', fakeFile(), fetch_);

    expect(calls[0].input).toBe('/api/sources/upload');
    expect(calls[0].init?.method).toBe('POST');
    // Body is a FormData instance — verify the fields.
    const body = calls[0].init?.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.get('name')).toBe('TINY');
    expect(body.get('role')).toBe('content');
    expect((body.get('file') as File).name).toBe('tiny.xml');
    // CRITICAL: content-type must NOT be set manually — browser fills
    // it with `multipart/form-data; boundary=...` automatically.
    // Setting it strips the boundary and the server fails to parse.
    expect(calls[0].init?.headers).toBeUndefined();

    expect(out).toEqual(summary);
  });

  it('passes role=reference through', async () => {
    const { fetch_, calls } = makeSpy({ body: {} });
    await uploadSource('REF', 'reference', fakeFile(), fetch_);
    const body = calls[0].init?.body as FormData;
    expect(body.get('role')).toBe('reference');
  });

  it('raises ApiError on 400 (e.g. empty name from the helper)', async () => {
    const { fetch_ } = makeSpy({ ok: false, status: 400, text: 'name must not be empty' });
    const err = await uploadSource('', 'content', fakeFile(), fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  // §16.25.t2 — AbortSignal threading
  it('threads the AbortSignal through to fetch.init.signal', async () => {
    const { fetch_, calls } = makeSpy({ body: {} });
    const ac = new AbortController();
    await uploadSource('S', 'content', fakeFile(), fetch_, ac.signal);
    expect(calls[0].init?.signal).toBe(ac.signal);
  });

  it('propagates an AbortError when the signal aborts mid-flight', async () => {
    // The fetch mock honours the abort by reading init.signal and
    // rejecting with a DOMException-shaped AbortError, matching the
    // real fetch contract.
    const fetch_: typeof fetch = (_input, init) => {
      return new Promise<Response>((_, reject) => {
        init?.signal?.addEventListener('abort', () => {
          const err = new Error('aborted');
          err.name = 'AbortError';
          reject(err);
        });
      });
    };
    const ac = new AbortController();
    const promise = uploadSource('S', 'content', fakeFile(), fetch_, ac.signal);
    ac.abort();
    const err = await promise.catch((e) => e);
    expect(err.name).toBe('AbortError');
  });
});


describe('importSourceByPath', () => {
  it('POSTs JSON {name, role, path} to /api/sources/import', async () => {
    const summary = { name: 'P', role: 'content', tracks_imported: 3, entries_imported: 3 };
    const { fetch_, calls } = makeSpy({ body: summary });
    const out = await importSourceByPath('P', 'content', '/abs/library.xml', fetch_);

    expect(calls[0].input).toBe('/api/sources/import');
    expect(calls[0].init?.method).toBe('POST');
    expect((calls[0].init?.headers as Record<string, string>)['content-type']).toBe(
      'application/json',
    );
    expect(JSON.parse(calls[0].init?.body as string)).toEqual({
      name: 'P',
      role: 'content',
      path: '/abs/library.xml',
    });
    expect(out).toEqual(summary);
  });

  it('raises ApiError on 400 (path not found)', async () => {
    const { fetch_ } = makeSpy({ ok: false, status: 400, text: 'path not found' });
    const err = await importSourceByPath('x', 'content', '/missing.xml', fetch_).catch(
      (e) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(400);
  });

  // §16.25.t2 — AbortSignal threading (parity with uploadSource).
  it('threads the AbortSignal through to fetch.init.signal', async () => {
    const { fetch_, calls } = makeSpy({ body: {} });
    const ac = new AbortController();
    await importSourceByPath('P', 'content', '/abs/p.xml', fetch_, ac.signal);
    expect(calls[0].init?.signal).toBe(ac.signal);
  });
});


describe('deleteSource', () => {
  it('DELETEs /api/sources/{id} and returns void on 204', async () => {
    const { fetch_, calls } = makeSpy({ status: 204, body: null });
    await deleteSource(7, fetch_);
    expect(calls[0].input).toBe('/api/sources/7');
    expect(calls[0].init?.method).toBe('DELETE');
  });

  it('raises ApiError on 404', async () => {
    const { fetch_ } = makeSpy({ ok: false, status: 404, text: 'not found' });
    const err = await deleteSource(999, fetch_).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});


// §16.2.6 c3 review #1 — type guard pins for JobSnapshot.result
// discriminator. Single-line predicates but they're the load-bearing
// discriminator + ship as the canonical narrow per c2 review #1
// finding #12.
describe('isGenerateResult / isPreviewResult', () => {
  const generateResult = {
    playlist_id: 42, trace_id: 't-1', station: 'cls', week: 5, slot_count: 10,
  };
  const previewResult = {
    trace: {
      trace_id: 'pv', station: 'cls', week: 1, rng: 'off', seed: null,
      generated_at: '2026-06-15T12:00:00Z', slots: [],
    },
    slot_count: 0,
  };

  it('isGenerateResult: true for GenerateResponse-shaped, false for PreviewResult / null', () => {
    expect(isGenerateResult(generateResult)).toBe(true);
    expect(isGenerateResult(previewResult)).toBe(false);
    expect(isGenerateResult(null)).toBe(false);
    expect(isGenerateResult(undefined)).toBe(false);
  });

  it('isPreviewResult: true for PreviewResult-shaped, false for GenerateResponse / null', () => {
    expect(isPreviewResult(previewResult)).toBe(true);
    expect(isPreviewResult(generateResult)).toBe(false);
    expect(isPreviewResult(null)).toBe(false);
    expect(isPreviewResult(undefined)).toBe(false);
  });

  it('discriminator is exclusive: no shape passes both guards', () => {
    // The whole point of the discriminator. If both ever returned
    // true for the same input, every consumer's narrow would be
    // ambiguous — the JobSnapshot.result widening (c2) only works
    // because the two shapes are disjoint.
    expect(isGenerateResult(generateResult) && isPreviewResult(generateResult)).toBe(false);
    expect(isGenerateResult(previewResult) && isPreviewResult(previewResult)).toBe(false);
  });
});
