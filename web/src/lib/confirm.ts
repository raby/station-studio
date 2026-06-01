/**
 * §16.25.t4 — promise-based confirm dialog.
 *
 * Replaces `window.confirm()` across the app (station delete, pool
 * delete, pool re-import, playlist regenerate/delete, batch
 * exit-with-dirty-rows). The native dialog is jarring, ignores the
 * page's design system, can't carry rich markup, and is awkward to
 * test (`vi.spyOn(window, 'confirm')`).
 *
 * API mirrors `window.confirm()` so call-site churn is minimal:
 * `const ok = confirm(text)` → `const ok = await confirmDialog({...})`.
 *
 * Mechanics: a single shared `<ConfirmDialog>` host mounted in
 * `+layout.svelte` subscribes to `confirmDialogState`. Calling
 * `confirmDialog(...)` flips the store to open + stashes a promise
 * resolver in the state; the host renders the dialog and calls the
 * resolver via `answerConfirmDialog(value)` when the user clicks.
 *
 * One instance per app — calling `confirmDialog()` while one is
 * already open would clobber the prior promise (the unresolved one
 * would dangle). Caller responsibility is to NOT stack confirms;
 * every call site today awaits, so this isn't reachable.
 */
import { writable } from 'svelte/store';

export interface ConfirmDialogState {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  /** Switches the Confirm button to the destructive (red) style. */
  danger: boolean;
  /** Internal: resolved by the host on Confirm / Cancel / Esc / click-outside. */
  resolve: (value: boolean) => void;
}

const closed: ConfirmDialogState = {
  open: false,
  title: '',
  body: '',
  confirmLabel: 'Confirm',
  cancelLabel: 'Cancel',
  danger: false,
  resolve: () => {
    // No-op when the dialog is closed — the host shouldn't be calling
    // `answerConfirmDialog` against a closed state, but if it does
    // (e.g. double-click race), this swallows the no-op cleanly.
  },
};

/** Shared state — the host subscribes; `confirmDialog()` writes. */
export const confirmDialogState = writable<ConfirmDialogState>(closed);

export interface ConfirmDialogOptions {
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

/**
 * Show the confirm dialog. Returns a promise resolving to `true` if
 * the user clicked Confirm, `false` on Cancel / Esc / click-outside.
 */
export function confirmDialog(options: ConfirmDialogOptions): Promise<boolean> {
  return new Promise((resolve) => {
    confirmDialogState.set({
      open: true,
      title: options.title,
      body: options.body,
      confirmLabel: options.confirmLabel ?? 'Confirm',
      cancelLabel: options.cancelLabel ?? 'Cancel',
      danger: options.danger ?? false,
      resolve,
    });
  });
}

/**
 * Internal — called by the host component to resolve the outstanding
 * promise and reset the store back to closed. Exposed for the host;
 * call sites should not call this directly.
 */
export function answerConfirmDialog(value: boolean): void {
  confirmDialogState.update((state) => {
    state.resolve(value);
    return closed;
  });
}
