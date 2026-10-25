import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import Page from './+page.svelte';
import type { AppState } from '$lib/desktopApp';
import { STATE_EVENT, UPDATE_EVENT } from '$lib/appEvents';
import { _reset, startInstallSupport } from '$lib/install';

function appState(over: Partial<AppState> = {}): AppState {
  return {
    service: 'station-studio',
    app: true,
    version: '1.8.0',
    kind: 'mac',
    can_update: true,
    latest: null,
    release_page: null,
    update: null,
    home: '/Users/rob/.station-studio',
    settings: { own_window: true, check_updates: true },
    login: false,
    browser: 'Google Chrome',
    ...over,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

type Call = [string, RequestInit | undefined];

describe('Settings › App', () => {
  beforeEach(() => _reset());
  afterEach(() => cleanup());

  it('shows the version, the switches as they are, and the data folder', async () => {
    const fetchFn = vi.fn(async () => json(appState({ login: true, settings: { own_window: false, check_updates: true } })));
    const { findByTestId, getByTestId, container } = render(Page, { props: { fetchFn } });
    expect(await findByTestId('settings-version')).toHaveTextContent('Station Studio 1.8.0');
    expect(getByTestId('settings-login')).toBeChecked();
    expect(getByTestId('settings-window')).not.toBeChecked();
    expect(getByTestId('settings-updates')).toBeChecked();
    expect(container).toHaveTextContent('using Google Chrome');
    expect(container).toHaveTextContent('/Users/rob/.station-studio');
    expect(container).toHaveTextContent('Applications folder or Launchpad');
  });

  it('changing a switch posts it with the header and says so', async () => {
    const calls: Call[] = [];
    const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return url === '/api/app/settings' ? json(appState({ login: true })) : json(appState());
    }) as unknown as typeof fetch;
    const heard = vi.fn();
    window.addEventListener(STATE_EVENT, heard);
    const { findByTestId } = render(Page, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('settings-login'));
    expect(await findByTestId('settings-note')).toHaveTextContent('Start at login turned on');
    const post = calls.find(([u]) => u === '/api/app/settings')!;
    expect(JSON.parse(String(post[1]?.body))).toEqual({ login: true });
    expect((post[1]?.headers as Record<string, string>)['X-Station-Studio']).toBe('1');
    expect(heard).toHaveBeenCalled();
    window.removeEventListener(STATE_EVENT, heard);
  });

  it('own window and new-version switches post their own keys', async () => {
    const bodies: unknown[] = [];
    const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/app/settings') bodies.push(JSON.parse(String(init?.body)));
      return json(appState());
    }) as unknown as typeof fetch;
    const { findByTestId } = render(Page, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('settings-window'));
    expect(await findByTestId('settings-note')).toHaveTextContent('next time Station Studio opens');
    await fireEvent.click(await findByTestId('settings-updates'));
    await waitFor(() => expect(bodies).toEqual([{ own_window: false }, { check_updates: false }]));
  });

  it('a refused change shows why and puts the switch back', async () => {
    const fetchFn = vi.fn(async (url: string) =>
      url === '/api/app/settings' ? json({ detail: "Couldn't change that: no room" }, 400) : json(appState()),
    ) as unknown as typeof fetch;
    const { findByTestId } = render(Page, { props: { fetchFn } });
    const box = await findByTestId('settings-login');
    await fireEvent.click(box);
    expect(await findByTestId('settings-error')).toHaveTextContent("Couldn't change that: no room");
    await waitFor(() => expect(box).not.toBeChecked());
  });

  it('Check for updates says when you have the newest', async () => {
    const fetchFn = vi.fn(async () => json(appState())) as unknown as typeof fetch;
    const { findByTestId } = render(Page, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('settings-check'));
    expect(await findByTestId('settings-newest')).toHaveTextContent('You have the newest version.');
  });

  it('Check for updates finds a new version, and Update now asks the app bar', async () => {
    const fetchFn = vi.fn(async (url: string) =>
      url === '/api/app/check' ? json(appState({ latest: '1.9.0' })) : json(appState()),
    ) as unknown as typeof fetch;
    const asked = vi.fn();
    window.addEventListener(UPDATE_EVENT, asked);
    const { findByTestId } = render(Page, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('settings-check'));
    expect(await findByTestId('settings-newer')).toHaveTextContent('Station Studio 1.9.0 is out.');
    await fireEvent.click(await findByTestId('settings-update-now'));
    expect(asked).toHaveBeenCalled();
    window.removeEventListener(UPDATE_EVENT, asked);
  });

  it('a failed check shows the error', async () => {
    const fetchFn = vi.fn(async (url: string) =>
      url === '/api/app/check' ? json({ detail: 'GitHub is not answering' }, 400) : json(appState()),
    ) as unknown as typeof fetch;
    const { findByTestId } = render(Page, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('settings-check'));
    expect(await findByTestId('settings-error')).toHaveTextContent('GitHub is not answering');
  });

  it('links to the release where the app cannot update itself', async () => {
    const fetchFn = vi.fn(async () =>
      json(appState({ kind: null, can_update: false, latest: '1.9.0', release_page: 'https://example/rel' })),
    );
    const { findByText, queryByTestId } = render(Page, { props: { fetchFn } });
    expect(await findByText('Download it')).toHaveAttribute('href', 'https://example/rel');
    expect(queryByTestId('settings-check')).toBeNull();
    expect(queryByTestId('settings-updates')).toBeNull();
  });

  it('leaves out what this computer cannot do', async () => {
    const fetchFn = vi.fn(async () => json(appState({ login: null, browser: null })));
    const { findByTestId, queryByTestId, container } = render(Page, { props: { fetchFn } });
    await findByTestId('settings-version');
    expect(queryByTestId('settings-login')).toBeNull();
    expect(queryByTestId('settings-window')).toBeNull();
    expect(container).toHaveTextContent('opens in a browser tab');
  });

  it('under serve or Docker says how it updates', async () => {
    const fetchFn = vi.fn(async () => json({ service: 'station-studio', app: false }));
    const { findByTestId, queryByTestId } = render(Page, { props: { fetchFn } });
    expect(await findByTestId('settings-server')).toHaveTextContent('station-studio serve');
    expect(queryByTestId('settings-check')).toBeNull();
  });

  it('offers Install as an app when the browser can', async () => {
    startInstallSupport(window);
    const offer = Object.assign(new Event('beforeinstallprompt', { cancelable: true }), {
      prompt: vi.fn(async () => {}),
      userChoice: Promise.resolve({ outcome: 'accepted' }),
    });
    window.dispatchEvent(offer);
    const fetchFn = vi.fn(async () => json(appState()));
    const { findByTestId, queryByTestId } = render(Page, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('settings-install-button'));
    expect(offer.prompt).toHaveBeenCalled();
    await waitFor(() => expect(queryByTestId('settings-install')).toBeNull());
  });
});
