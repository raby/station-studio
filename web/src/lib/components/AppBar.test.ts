import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import AppBar from './AppBar.svelte';
import { answerConfirmDialog } from '$lib/confirm';
import { stageText, waitForRestart, type AppState } from '$lib/desktopApp';
import { announceAppState, requestUpdate } from '$lib/appEvents';

function appState(over: Partial<AppState> = {}): AppState {
  return {
    service: 'station-studio',
    app: true,
    version: '1.6.0',
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

describe('AppBar', () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('renders nothing under serve / Docker (app: false)', async () => {
    const fetchFn = vi.fn(async () => json({ service: 'station-studio', app: false }));
    const { container } = render(AppBar, { props: { fetchFn } });
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="app-bar"]')).toBeNull();
  });

  it('renders nothing when /api/app fails', async () => {
    const fetchFn = vi.fn(async () => {
      throw new Error('offline');
    });
    const { container } = render(AppBar, { props: { fetchFn } });
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="app-bar"]')).toBeNull();
  });

  it('shows the version and Quit in the app, with no notice when up to date', async () => {
    const fetchFn = vi.fn(async () => json(appState()));
    const { findByTestId, queryByTestId } = render(AppBar, { props: { fetchFn } });
    expect(await findByTestId('app-bar')).toHaveTextContent('Station Studio 1.6.0');
    expect(queryByTestId('app-update-notice')).toBeNull();
    expect(await findByTestId('app-quit')).toBeEnabled();
  });

  it('offers Update now when a newer version is out', async () => {
    const fetchFn = vi.fn(async () =>
      json(appState({ latest: '1.7.0', release_page: 'https://github.com/raby/station-studio/releases/tag/studio-v1.7.0' })),
    );
    const { findByTestId } = render(AppBar, { props: { fetchFn } });
    const notice = await findByTestId('app-update-notice');
    expect(notice).toHaveTextContent('Station Studio 1.7.0 is out.');
    expect(notice.querySelector('a')).toHaveAttribute('href', expect.stringContaining('studio-v1.7.0'));
  });

  it('Not now hides the notice for that version, and a newer one shows again', async () => {
    localStorage.clear();
    let latest = '1.7.0';
    const fetchFn = vi.fn(async () => json(appState({ latest }))) as unknown as typeof fetch;
    const first = render(AppBar, { props: { fetchFn } });
    await fireEvent.click(await first.findByTestId('app-update-later'));
    expect(first.queryByTestId('app-update-notice')).toBeNull();
    first.unmount();
    const again = render(AppBar, { props: { fetchFn } });
    await again.findByTestId('app-bar');
    expect(again.queryByTestId('app-update-notice')).toBeNull(); // remembered
    again.unmount();
    latest = '1.8.0';
    const newer = render(AppBar, { props: { fetchFn } });
    expect(await newer.findByTestId('app-update-notice')).toHaveTextContent('Station Studio 1.8.0 is out.');
    localStorage.clear();
  });

  it('looks again now and then, so a new version turns up on an open page', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let latest: string | null = null;
    const fetchFn = vi.fn(async () => json(appState({ latest }))) as unknown as typeof fetch;
    const { findByTestId, queryByTestId } = render(AppBar, { props: { fetchFn, refreshEvery: 1000 } });
    await findByTestId('app-bar');
    expect(queryByTestId('app-update-notice')).toBeNull();
    latest = '1.7.0';
    await vi.advanceTimersByTimeAsync(1100);
    expect(await findByTestId('app-update-notice')).toHaveTextContent('Station Studio 1.7.0 is out.');
  });

  it('hides Update now where the app cannot update itself', async () => {
    const fetchFn = vi.fn(async () => json(appState({ kind: null, can_update: false, latest: '1.7.0' })));
    const { findByTestId, queryByTestId } = render(AppBar, { props: { fetchFn } });
    await findByTestId('app-bar');
    expect(queryByTestId('app-update-notice')).toBeNull();
  });

  it('Update now posts the version with the header, then reloads into the new version', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls: Array<[string, RequestInit | undefined]> = [];
    let phase = 'idle';
    const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      if (url === '/api/app/update') {
        phase = 'restarting';
        return json(appState({ latest: '1.7.0', update: { version: '1.7.0', stage: 'downloading', percent: 0, error: null, page: 'p' } }));
      }
      if (phase === 'restarting') {
        phase = 'gone';
        return json(appState({ latest: '1.7.0', update: { version: '1.7.0', stage: 'restarting', percent: 100, error: null, page: 'p' } }));
      }
      if (phase === 'gone') {
        phase = 'new';
        throw new Error('connection refused');
      }
      if (phase === 'new') return json(appState({ version: '1.7.0' }));
      return json(appState({ latest: '1.7.0' }));
    }) as unknown as typeof fetch;
    const reload = vi.fn();
    const sleep = vi.fn(async () => {});
    const { findByTestId } = render(AppBar, { props: { fetchFn, reload, sleep } });
    await fireEvent.click(await findByTestId('app-update-now'));
    expect(await findByTestId('app-update-progress')).toHaveTextContent('Downloading Station Studio 1.7.0… 0%');
    await vi.advanceTimersByTimeAsync(1100);
    await waitFor(() => expect(reload).toHaveBeenCalled());
    const post = calls.find(([u]) => u === '/api/app/update')!;
    expect(post[1]?.method).toBe('POST');
    expect((post[1]?.headers as Record<string, string>)['X-Station-Studio']).toBe('1');
    expect(JSON.parse(String(post[1]?.body))).toEqual({ version: '1.7.0' });
  });

  it('shows why an update failed, with the release page', async () => {
    const fetchFn = vi.fn(async () =>
      json(
        appState({
          latest: '1.7.0',
          update: { version: '1.7.0', stage: 'failed', percent: 40, error: 'The download stopped.', page: 'https://example/rel' },
        }),
      ),
    );
    const { findByTestId } = render(AppBar, { props: { fetchFn } });
    const failed = await findByTestId('app-update-failed');
    expect(failed).toHaveTextContent('The download stopped.');
    expect(failed.querySelector('a')).toHaveAttribute('href', 'https://example/rel');
  });

  it('shows the server error when Update now is refused', async () => {
    const fetchFn = vi.fn(async (url: string) =>
      url === '/api/app/update' ? json({ detail: 'Station Studio is already updating' }, 400) : json(appState({ latest: '1.7.0' })),
    ) as unknown as typeof fetch;
    const { findByTestId } = render(AppBar, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('app-update-now'));
    expect(await findByTestId('app-error')).toHaveTextContent('Station Studio is already updating');
  });

  it('Quit asks first, then says Station Studio has stopped', async () => {
    const fetchFn = vi.fn(async (url: string) => (url === '/api/app/quit' ? json({ ok: true }) : json(appState()))) as unknown as typeof fetch;
    const closeWindow = vi.fn();
    const { findByTestId } = render(AppBar, { props: { fetchFn, closeWindow } });
    await fireEvent.click(await findByTestId('app-quit'));
    answerConfirmDialog(true);
    expect(await findByTestId('app-stopped')).toHaveTextContent('Station Studio has stopped');
    expect(closeWindow).toHaveBeenCalled(); // its own window closes; a tab keeps the notice
    expect((fetchFn as unknown as ReturnType<typeof vi.fn>).mock.calls.some(([u]) => u === '/api/app/quit')).toBe(true);
  });

  it("Quit in the Mac app's own window quits the app too", async () => {
    const fetchFn = vi.fn(async (url: string) => (url === '/api/app/quit' ? json({ ok: true }) : json(appState({ mac_app: true })))) as unknown as typeof fetch;
    const postMessage = vi.fn();
    (window as unknown as { webkit: unknown }).webkit = { messageHandlers: { stationApp: { postMessage } } };
    try {
      const { findByTestId } = render(AppBar, { props: { fetchFn, native: true } });
      await fireEvent.click(await findByTestId('app-quit'));
      answerConfirmDialog(true);
      await waitFor(() => expect(postMessage).toHaveBeenCalledWith('quit'));
    } finally {
      delete (window as unknown as { webkit?: unknown }).webkit;
    }
  });

  it("offers the Mac app's own window once to a browser window", async () => {
    localStorage.removeItem('station-studio:mac-window-offered');
    const fetchFn = vi.fn(async (url: string) =>
      url === '/api/app/window' ? json({ ok: true }) : json(appState({ mac_app: true })),
    ) as unknown as typeof fetch;
    const { findByTestId, queryByTestId } = render(AppBar, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('app-window-open'));
    expect(queryByTestId('app-window-offer')).toBeNull();
    const calls = (fetchFn as unknown as ReturnType<typeof vi.fn>).mock.calls;
    await waitFor(() => expect(calls.some(([u, init]) => u === '/api/app/window' && init?.method === 'POST')).toBe(true));
    cleanup();
    const again = render(AppBar, { props: { fetchFn } });
    expect(await again.findByTestId('app-bar')).toBeInTheDocument();
    expect(again.queryByTestId('app-window-offer')).toBeNull();
  });

  it("doesn't offer the window inside it, or with own window off", async () => {
    localStorage.removeItem('station-studio:mac-window-offered');
    const inside = render(AppBar, { props: { fetchFn: vi.fn(async () => json(appState({ mac_app: true }))), native: true } });
    expect(await inside.findByTestId('app-bar')).toBeInTheDocument();
    expect(inside.queryByTestId('app-window-offer')).toBeNull();
    cleanup();
    const off = appState({ mac_app: true, settings: { own_window: false, check_updates: true } });
    const tab = render(AppBar, { props: { fetchFn: vi.fn(async () => json(off)) } });
    expect(await tab.findByTestId('app-bar')).toBeInTheDocument();
    expect(tab.queryByTestId('app-window-offer')).toBeNull();
  });

  it('Quit cancelled does nothing', async () => {
    const fetchFn = vi.fn(async () => json(appState())) as unknown as typeof fetch;
    const { findByTestId, queryByTestId } = render(AppBar, { props: { fetchFn } });
    await fireEvent.click(await findByTestId('app-quit'));
    answerConfirmDialog(false);
    await Promise.resolve();
    expect(queryByTestId('app-stopped')).toBeNull();
    expect((fetchFn as unknown as ReturnType<typeof vi.fn>).mock.calls.every(([u]) => u === '/api/app')).toBe(true);
  });
});

describe('desktopApp helpers', () => {
  const u = { version: '2.0.0', percent: 5, error: null, page: 'p' };

  it('describes each stage', () => {
    expect(stageText({ ...u, stage: 'downloading' })).toBe('Downloading Station Studio 2.0.0… 5%');
    expect(stageText({ ...u, stage: 'downloading', percent: null })).toBe('Downloading Station Studio 2.0.0…');
    expect(stageText({ ...u, stage: 'installing' })).toBe('Installing Station Studio 2.0.0…');
    expect(stageText({ ...u, stage: 'restarting' })).toBe('Restarting into Station Studio 2.0.0…');
    expect(stageText({ ...u, stage: 'failed' })).toBe("The update didn't work.");
    expect(stageText({ ...u, stage: 'failed', error: 'No room' })).toBe('No room');
  });

  it('waitForRestart: the old version coming back means the swap did not take', async () => {
    const answers: Array<AppState | null> = [null, appState({ version: '1.6.0' })];
    const fetchFn = vi.fn(async () => {
      const next = answers.shift();
      if (!next) throw new Error('down');
      return json(next);
    }) as unknown as typeof fetch;
    expect(await waitForRestart('1.7.0', { fetchFn, sleep: async () => {} })).toBe('unchanged');
  });

  it('waitForRestart: gives up when nothing answers', async () => {
    const fetchFn = vi.fn(async () => {
      throw new Error('down');
    }) as unknown as typeof fetch;
    expect(await waitForRestart('1.7.0', { fetchFn, sleep: async () => {}, tries: 3 })).toBe('gone');
  });
});

describe('AppBar and the Settings page', () => {
  afterEach(() => cleanup());

  it('shows news the Settings page announces', async () => {
    const fetchFn = vi.fn(async () => json(appState())) as unknown as typeof fetch;
    const { findByTestId, queryByTestId } = render(AppBar, { props: { fetchFn } });
    await findByTestId('app-bar');
    expect(queryByTestId('app-update-notice')).toBeNull();
    announceAppState(appState({ latest: '1.9.0' }));
    expect(await findByTestId('app-update-notice')).toHaveTextContent('Station Studio 1.9.0 is out.');
  });

  it('runs Update now when the Settings page asks', async () => {
    const fetchFn = vi.fn(async (url: string) =>
      url === '/api/app/update' ? json({ detail: 'refused' }, 400) : json(appState({ latest: '1.9.0' })),
    ) as unknown as typeof fetch;
    const { findByTestId } = render(AppBar, { props: { fetchFn } });
    await findByTestId('app-update-notice');
    requestUpdate();
    expect(await findByTestId('app-error')).toHaveTextContent('refused');
  });
});
