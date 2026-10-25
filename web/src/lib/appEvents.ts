/**
 * The Settings page and the app bar at the foot of the sidebar both show
 * the app's state. Settings tells the bar when it has newer news (a check
 * it ran) and asks it to run Update now, so the update's progress shows
 * in one place whichever page started it.
 */
import type { AppState } from './desktopApp';

export const STATE_EVENT = 'station-studio:app-state';
export const UPDATE_EVENT = 'station-studio:update';

export function announceAppState(state: AppState, win: Window = window): void {
  win.dispatchEvent(new CustomEvent(STATE_EVENT, { detail: state }));
}

export function requestUpdate(win: Window = window): void {
  win.dispatchEvent(new Event(UPDATE_EVENT));
}
