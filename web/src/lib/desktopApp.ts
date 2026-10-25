// The downloaded Station Studio app's own endpoints (station_studio/desktop.py).
//
// They exist only when Station Studio was opened as an app (the Mac or
// Windows download, or `station-studio app`); under `serve` or Docker
// GET /api/app is a 404 and the page shows nothing extra.

export const API_HEADER = 'X-Station-Studio';

export interface UpdateStatus {
  version: string;
  stage: 'downloading' | 'installing' | 'restarting' | 'failed';
  percent: number | null;
  error: string | null;
  page: string;
}

export interface AppState {
  service: 'station-studio';
  app: true;
  version: string;
  kind: 'mac' | 'windows' | null;
  can_update: boolean;
  latest: string | null;
  release_page: string | null;
  update: UpdateStatus | null;
  home: string;
  settings: AppSettings;
  /** Starts when you log in; null where it can't. */
  login: boolean | null;
  /** The browser that draws its own window, or null when there isn't one. */
  browser: string | null;
}

export interface AppSettings {
  own_window: boolean;
  check_updates: boolean;
}

export type SettingsChange = Partial<AppSettings> & { login?: boolean };

/** The app's state, or null when this isn't the app (or it isn't answering). */
export async function getAppState(fetchFn: typeof fetch = fetch): Promise<AppState | null> {
  try {
    const res = await fetchFn('/api/app', { cache: 'no-store' });
    if (!res.ok) return null;
    const body = await res.json();
    return body && body.app === true ? (body as AppState) : null;
  } catch {
    return null;
  }
}

async function post(path: string, body: unknown, fetchFn: typeof fetch): Promise<Response> {
  const res = await fetchFn(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', [API_HEADER]: '1' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const err = await res.json();
      if (err && typeof err.detail === 'string') detail = err.detail;
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return res;
}

export async function startUpdate(version: string, fetchFn: typeof fetch = fetch): Promise<AppState> {
  return (await post('/api/app/update', { version }, fetchFn)).json();
}

export async function changeAppSettings(change: SettingsChange, fetchFn: typeof fetch = fetch): Promise<AppState> {
  return (await post('/api/app/settings', change, fetchFn)).json();
}

/** Ask GitHub for the newest version now. */
export async function checkNow(fetchFn: typeof fetch = fetch): Promise<AppState> {
  return (await post('/api/app/check', {}, fetchFn)).json();
}

export async function quitApp(fetchFn: typeof fetch = fetch): Promise<void> {
  await post('/api/app/quit', {}, fetchFn);
}

/** Words for where an update has got to. */
export function stageText(update: UpdateStatus): string {
  switch (update.stage) {
    case 'downloading':
      return update.percent != null
        ? `Downloading Station Studio ${update.version}… ${update.percent}%`
        : `Downloading Station Studio ${update.version}…`;
    case 'installing':
      return `Installing Station Studio ${update.version}…`;
    case 'restarting':
      return `Restarting into Station Studio ${update.version}…`;
    case 'failed':
      return update.error || "The update didn't work.";
  }
}

/**
 * After an update, wait for the new version to answer. Resolves 'updated'
 * once it does, 'unchanged' if the old version comes back instead (the
 * swap didn't take), or 'gone' if nothing answers in time.
 */
export async function waitForRestart(
  target: string,
  opts: { fetchFn?: typeof fetch; sleep?: (ms: number) => Promise<void>; tries?: number } = {},
): Promise<'updated' | 'unchanged' | 'gone'> {
  const fetchFn = opts.fetchFn ?? fetch;
  const sleep = opts.sleep ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  const tries = opts.tries ?? 150; // ~5 minutes at 2s: a first start is slow while the OS checks the app
  let wentAway = false;
  for (let i = 0; i < tries; i++) {
    await sleep(2000);
    const state = await getAppState(fetchFn);
    if (!state) {
      wentAway = true;
      continue;
    }
    if (state.version === target) return 'updated';
    if (wentAway && !state.update) return 'unchanged';
  }
  return 'gone';
}
