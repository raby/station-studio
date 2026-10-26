import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import Panel from './StationServerPanel.svelte';
import type { StationServerStation } from '$lib/api';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function station(over: Partial<StationServerStation> = {}): StationServerStation {
  return {
    id: 'abc', name: 'Rebirth Classics', state: 'on', rehearsal: false,
    now: { title: 'Ghost Town', artist: 'The Specials' }, live: null,
    week: '012.xml', next: { week: '013.xml', at: '2026-11-02T00:00' }, weeks: ['011.xml', '012.xml', '013.xml'],
    problem: null, ...over,
  };
}

const playlists = [
  { id: 7, week: 12, trace_id: 't12', generated_at: null, entry_count: 100 },
  { id: 8, week: 13, trace_id: 't13', generated_at: null, entry_count: 100 },
];

function server(stations: StationServerStation[], target = { station: 'abc', auto: false }) {
  const calls: [string, RequestInit | undefined][] = [];
  const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push([url, init]);
    if (url === '/api/station-server') return json({ address: '10.0.0.5', has_code: true, stations: target.station ? { cls: target } : {} });
    if (url === '/api/station-server/status') return json({ configured: true, connected: true, error: null, server: 'Station Server', version: '0.13.0', stations });
    if (url === '/api/station-server/stations/cls') return json({ address: '10.0.0.5', has_code: true, stations: { cls: JSON.parse(String(init?.body)) } });
    if (url === '/api/playlists/8/send-to-station-server')
      return json({ station: 'Rebirth Classics', week: '013.xml', replaced: false, tracks: 412, missing_count: 0, missing: [], when: 'next', goes_on: '2026-11-02T00:00' });
    return json({ detail: 'not found' }, 404);
  });
  return { fetchFn, calls };
}

describe('On Station Server', () => {
  afterEach(() => cleanup());

  it('stays hidden until Settings › Station Server is filled in', async () => {
    const fetchFn = vi.fn(async (url: string) =>
      url.endsWith('/status') ? json({ configured: false, connected: false, error: null, stations: [] })
        : json({ address: '', has_code: false, stations: {} }));
    const { queryByTestId } = render(Panel, { props: { slug: 'cls', name: 'Classics', playlists, fetchFn: fetchFn as unknown as typeof fetch } });
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    expect(queryByTestId('station-server-panel')).toBeNull();
  });

  it("shows what's on air and the weeks it has", async () => {
    const { fetchFn } = server([station()]);
    const { findByTestId, queryByTestId } = render(Panel, { props: { slug: 'cls', name: 'Classics', playlists, fetchFn: fetchFn as unknown as typeof fetch } });
    const facts = await findByTestId('ss-facts');
    expect(facts).toHaveTextContent('On air · Ghost Town by The Specials');
    expect(facts).toHaveTextContent('11, 12, 13');
    expect(queryByTestId('ss-missing')).toBeNull();
  });

  it('warns when next week is missing and sends it', async () => {
    const { fetchFn, calls } = server([station({ next: null, weeks: ['011.xml', '012.xml'] })]);
    const { findByTestId } = render(Panel, { props: { slug: 'cls', name: 'Classics', playlists, fetchFn: fetchFn as unknown as typeof fetch } });
    expect(await findByTestId('ss-missing')).toHaveTextContent('nothing after week 12');
    await fireEvent.click(await findByTestId('ss-send-missing'));
    expect(await findByTestId('ss-sent')).toHaveTextContent('Rebirth Classics, week 13: 412 tracks found');
    expect(calls.some(([u, i]) => u === '/api/playlists/8/send-to-station-server' && i?.method === 'POST')).toBe(true);
  });

  it('chooses the station it sends to, and sending on export', async () => {
    const { fetchFn, calls } = server([station()], { station: '', auto: false });
    const { findByTestId } = render(Panel, { props: { slug: 'cls', name: 'Classics', playlists, fetchFn: fetchFn as unknown as typeof fetch } });
    const select = (await findByTestId('ss-target')) as HTMLSelectElement;
    select.value = 'abc';
    await fireEvent.change(select);
    await fireEvent.click(await findByTestId('ss-auto'));
    const puts = calls.filter(([u]) => u === '/api/station-server/stations/cls').map(([, i]) => JSON.parse(String(i?.body)));
    expect(puts).toEqual([{ station: 'abc', auto: false }, { station: 'abc', auto: true }]);
  });

  it("says when Station Server can't be reached", async () => {
    const fetchFn = vi.fn(async (url: string) =>
      url.endsWith('/status')
        ? json({ configured: true, connected: false, error: "Couldn't reach Station Server at 10.0.0.5", stations: [] })
        : json({ address: '10.0.0.5', has_code: true, stations: {} }));
    const { findByTestId } = render(Panel, { props: { slug: 'cls', name: 'Classics', playlists, fetchFn: fetchFn as unknown as typeof fetch } });
    expect(await findByTestId('ss-unreachable')).toHaveTextContent("Couldn't reach Station Server");
  });
});
