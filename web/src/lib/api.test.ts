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
  patchBlock,
  pollJob,
  setStationRule,
  clearStationRule,
  startGenerateJob,
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

      const spyTrace = makeSpy({ body: { trace_id: 'abc' } });
      await getTrace(42, spyTrace.fetch_);
      expect(spyTrace.calls[0].input).toBe('/api/playlists/42/trace');
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
      const r = await exportPlaylistToDisk(42, fetch_);

      expect(calls[0].input).toBe('/api/playlists/42/export-to-disk');
      expect(calls[0].init?.method).toBe('POST');
      expect(r.path).toBe('/abs/out/cls/001.xml');
      expect(r.bytes).toBe(868990);
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
      const err = await exportPlaylistToDisk(999, fetch_).catch((e) => e);
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
      const err = await exportPlaylistToDisk(42, fetch_).catch((e) => e);
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
    for await (const snap of watchJob('j-1', 500, fetch_, sleep)) {
      snaps.push(snap);
    }
    expect(snaps.map((s) => [s.status, s.current])).toEqual([
      ['running', 0],
      ['running', 50],
      ['done', 100],
    ]);
    // Sleep called BETWEEN polls (2x, not before the first or after
    // the last terminal poll).
    expect(sleep).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledWith(500);
  });

  it('stops cleanly when the job 404s mid-poll (yields nothing on 404)', async () => {
    const { fetch_ } = makeSequencedSpy([
      { body: fakeSnap({ status: 'running' }) },
      { ok: false, status: 404, text: 'gone' },
    ]);
    const sleep = vi.fn(async () => {});
    const snaps: JobSnapshot[] = [];
    for await (const snap of watchJob('j-1', 500, fetch_, sleep)) {
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
    for await (const snap of watchJob('j-1', 500, fetch_, sleep)) {
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
    for await (const snap of watchJob('j-1', 500, fetch_, sleep)) {
      snaps.push(snap);
    }
    expect(snaps[0].status).toBe('cancelled');
  });

  it('propagates non-404 fetch errors out of the generator', async () => {
    const { fetch_ } = makeSequencedSpy([
      { ok: false, status: 500, text: 'engine wedged' },
    ]);
    const sleep = vi.fn(async () => {});
    const err = await (async () => {
      try {
        for await (const _ of watchJob('j-1', 500, fetch_, sleep)) {
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
    const fetch_ = async (_input: RequestInfo | URL, init?: RequestInit) => {
      return new Promise((_, reject) => {
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
