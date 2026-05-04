import { describe, it, expect } from 'vitest';
import {
  ApiError,
  getStations,
  getStation,
  getStationPlaylists,
  getPlaylist,
  getTrace,
  createStation,
  generatePlaylist,
  deletePlaylist,
  deleteStation,
  exportPlaylistToDisk,
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

    it('raises ApiError when the destination is unwritable (404 / 500)', async () => {
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

  describe('ApiError', () => {
    it('preserves status and name so `instanceof` + `.status` checks in UI work', () => {
      const e = new ApiError(500, 'boom');
      expect(e.status).toBe(500);
      expect(e.name).toBe('ApiError');
      expect(e).toBeInstanceOf(Error);
    });
  });
});
