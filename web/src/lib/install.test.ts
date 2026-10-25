import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { installApp, installOffer, startInstallSupport, _reset } from './install';

function fakeWindow(sw?: { register: ReturnType<typeof vi.fn> }) {
  const target = new EventTarget();
  return Object.assign(target, { navigator: { serviceWorker: sw } }) as unknown as Window;
}

function offerEvent(outcome: 'accepted' | 'dismissed') {
  const e = new Event('beforeinstallprompt', { cancelable: true });
  return Object.assign(e, { prompt: vi.fn(async () => {}), userChoice: Promise.resolve({ outcome }) });
}

describe('install as an app', () => {
  beforeEach(() => _reset());

  it('keeps the browser offer, registers the service worker once', () => {
    const register = vi.fn(async () => ({}));
    const win = fakeWindow({ register });
    startInstallSupport(win);
    startInstallSupport(win);
    expect(register).toHaveBeenCalledTimes(1);
    expect(register).toHaveBeenCalledWith('/sw.js');
    const e = offerEvent('accepted');
    win.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);
    expect(get(installOffer)).toBe(e);
    win.dispatchEvent(new Event('appinstalled'));
    expect(get(installOffer)).toBeNull();
  });

  it("registers no service worker in the Mac app's own window", () => {
    const register = vi.fn(async () => ({}));
    const win = Object.assign(fakeWindow({ register }), {
      webkit: { messageHandlers: { stationApp: { postMessage: vi.fn() } } },
    }) as unknown as Window;
    startInstallSupport(win);
    expect(register).not.toHaveBeenCalled();
  });

  it('works where there are no service workers', () => {
    expect(() => startInstallSupport(fakeWindow())).not.toThrow();
  });

  it('installApp asks the browser and forgets the offer', async () => {
    const win = fakeWindow();
    startInstallSupport(win);
    const yes = offerEvent('accepted');
    win.dispatchEvent(yes);
    expect(await installApp(yes as never)).toBe(true);
    expect(yes.prompt).toHaveBeenCalled();
    expect(get(installOffer)).toBeNull();
    expect(await installApp(offerEvent('dismissed') as never)).toBe(false);
  });
});
