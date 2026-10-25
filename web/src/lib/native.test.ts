import { describe, it, expect, vi } from 'vitest';
import { exposeToApp, isNative, tellApp } from './native';

function win(over: Record<string, unknown> = {}): Window {
  return { navigator: { userAgent: 'Mozilla/5.0' }, ...over } as unknown as Window;
}

describe("the Mac app's own window", () => {
  it('is found by its message handler or its user agent', () => {
    expect(isNative(win())).toBe(false);
    expect(isNative(win({ webkit: { messageHandlers: { stationApp: { postMessage: vi.fn() } } } }))).toBe(true);
    expect(isNative(win({ navigator: { userAgent: 'Mozilla/5.0 StationStudioApp/1.9.0' } }))).toBe(true);
  });

  it('is told things, and a browser is told nothing', () => {
    const postMessage = vi.fn();
    expect(tellApp('show', win({ webkit: { messageHandlers: { stationApp: { postMessage } } } }))).toBe(true);
    expect(postMessage).toHaveBeenCalledWith('show');
    expect(tellApp('quit', win())).toBe(false);
  });

  it("gives the app's menus a way to move between pages", () => {
    const w = win();
    const go = vi.fn();
    exposeToApp({ go }, w);
    (w as unknown as { stationApp: { go(p: string): void } }).stationApp.go('/sources');
    expect(go).toHaveBeenCalledWith('/sources');
  });
});
