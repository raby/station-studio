import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import Settings from './StationServerSettings.svelte';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('Settings › Station Server', () => {
  afterEach(() => cleanup());

  it('saves the address and code, never shows the code, and tests the link', async () => {
    const puts: unknown[] = [];
    const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/station-server' && init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)));
        return json({ address: '10.0.0.5:8772', has_code: true, stations: {} });
      }
      if (url === '/api/station-server') return json({ address: '', has_code: false, stations: {} });
      return json({ configured: true, connected: true, error: null, server: 'Station Server', version: '0.13.0',
        stations: [{ id: 'a', name: 'Rebirth Radio', state: 'off', now: null, week: null, next: null, weeks: [] }] });
    });
    const { getByTestId, findByTestId } = render(Settings, { props: { fetchFn } });
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    await fireEvent.input(getByTestId('station-server-address'), { target: { value: '10.0.0.5:8772' } });
    await fireEvent.input(getByTestId('station-server-code'), { target: { value: 'k7m2-9qxr-4hpt' } });
    await fireEvent.click(getByTestId('station-server-save'));
    expect(await findByTestId('station-server-connected')).toHaveTextContent('Rebirth Radio (off air)');
    expect(puts).toEqual([{ address: '10.0.0.5:8772', code: 'k7m2-9qxr-4hpt' }]);
    expect((getByTestId('station-server-code') as HTMLInputElement).value).toBe('');
  });

  it("shows why it can't connect", async () => {
    const fetchFn = vi.fn(async (url: string) =>
      url === '/api/station-server'
        ? json({ address: '10.0.0.5', has_code: true, stations: {} })
        : json({ configured: true, connected: false, error: "That isn't the link code", stations: [] }));
    const { findByTestId } = render(Settings, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('station-server-test'));
    expect(await findByTestId('station-server-error')).toHaveTextContent("That isn't the link code");
  });
});
