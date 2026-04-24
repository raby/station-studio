import { describe, it, expect } from 'vitest';
import {
  ApiError,
  getStations,
  getStation,
  getStationPlaylists,
  getPlaylist,
  getTrace,
  createStation,
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

  describe('ApiError', () => {
    it('preserves status and name so `instanceof` + `.status` checks in UI work', () => {
      const e = new ApiError(500, 'boom');
      expect(e.status).toBe(500);
      expect(e.name).toBe('ApiError');
      expect(e).toBeInstanceOf(Error);
    });
  });
});
