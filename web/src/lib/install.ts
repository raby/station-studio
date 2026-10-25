/**
 * Installing Station Studio as an app from its window, which gives it
 * its own icon and menu in the Dock or taskbar (as Station Player does).
 * The browser offers it with `beforeinstallprompt`; it doesn't once it's
 * installed. Opened from the Dock while Station Studio isn't running,
 * the window shows a page that starts it (static/sw.js, offline.html).
 */
import { writable, type Readable } from 'svelte/store';

interface InstallPrompt extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

const offer = writable<InstallPrompt | null>(null);
/** The browser's install offer, while there is one. */
export const installOffer: Readable<InstallPrompt | null> = offer;

let started = false;

/** Listen for the browser's offer and register the service worker. */
export function startInstallSupport(win: Window = window): void {
  if (started) return;
  started = true;
  win.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    offer.set(e as InstallPrompt);
  });
  win.addEventListener('appinstalled', () => offer.set(null));
  const sw = win.navigator.serviceWorker;
  if (sw) sw.register('/sw.js').catch(() => {});
}

/** Ask the browser to install it; true if the person said yes. */
export async function installApp(prompt: InstallPrompt): Promise<boolean> {
  await prompt.prompt();
  const { outcome } = await prompt.userChoice;
  offer.set(null);
  return outcome === 'accepted';
}

/** For tests. */
export function _reset(): void {
  started = false;
  offer.set(null);
}
