/**
 * The downloaded Mac app shows Station Studio in a window of its own
 * (station_studio/macwindow.py), not a browser. Its menus and Dock menu
 * reach the page through `window.stationApp`, and the page reaches the
 * app through the `stationApp` message handler.
 */

interface Bridge {
  postMessage(message: string): void;
}

function bridge(win: Window): Bridge | null {
  const handlers = (win as unknown as { webkit?: { messageHandlers?: Record<string, Bridge> } }).webkit
    ?.messageHandlers;
  return handlers?.stationApp ?? null;
}

/** Whether this page is inside the Mac app's own window. */
export function isNative(win: Window = window): boolean {
  return bridge(win) !== null || /StationStudioApp\//.test(win.navigator?.userAgent ?? '');
}

/** Tell the Mac app something ("show", "quit"); false outside it. */
export function tellApp(message: 'show' | 'quit', win: Window = window): boolean {
  const b = bridge(win);
  if (!b) return false;
  try {
    b.postMessage(message);
    return true;
  } catch {
    return false;
  }
}

/** What the app's menus call on the page (macwindow.py's `tell`). */
export function exposeToApp(calls: { go(path: string): void }, win: Window = window): void {
  (win as unknown as { stationApp?: unknown }).stationApp = calls;
}
