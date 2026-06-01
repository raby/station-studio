import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

import ConfirmDialog from './ConfirmDialog.svelte';
import { confirmDialog, confirmDialogState } from '$lib/confirm';

/**
 * §16.25.t4 — confirm dialog primitive tests.
 *
 * Covers the promise-based API + the host component's open/close/
 * resolve mechanics. Each call site's migrated test (PoolImportPanel,
 * stations/[slug]) mocks `confirmDialog` directly — these tests own
 * the underlying contract.
 */


beforeEach(() => {
  // Reset the store to closed between tests. Without this, a prior
  // test that left the dialog open would bleed into the next.
  confirmDialogState.set({
    open: false,
    title: '',
    body: '',
    confirmLabel: 'Confirm',
    cancelLabel: 'Cancel',
    danger: false,
    resolve: () => {},
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});


describe('confirmDialog() promise + host integration', () => {
  it('resolves to true when the Confirm button is clicked', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({
      title: 'Test action',
      body: 'Are you sure?',
    });

    const confirmBtn = await screen.findByTestId('confirm-dialog-confirm');
    await fireEvent.click(confirmBtn);

    expect(await promise).toBe(true);
  });

  it('resolves to false when the Cancel button is clicked', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({ title: 'X', body: 'Y' });

    await fireEvent.click(await screen.findByTestId('confirm-dialog-cancel'));

    expect(await promise).toBe(false);
  });

  it('resolves to false on Escape', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({ title: 'X', body: 'Y' });

    // Esc is handled at the window level — fire on document so the
    // host's `window.addEventListener('keydown', ...)` catches it.
    await fireEvent.keyDown(window, { key: 'Escape' });

    expect(await promise).toBe(false);
  });

  it('resolves to false when the backdrop is clicked', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({ title: 'X', body: 'Y' });

    // Clicking the backdrop (target === currentTarget) cancels.
    const backdrop = await screen.findByTestId('confirm-dialog-backdrop');
    await fireEvent.click(backdrop);

    expect(await promise).toBe(false);
  });

  it('does NOT cancel when a click lands inside the dialog card', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({ title: 'X', body: 'Y' });
    let resolved = false;
    promise.then(() => {
      resolved = true;
    });

    // Click on the dialog body (NOT the backdrop). Should not cancel.
    await fireEvent.click(await screen.findByTestId('confirm-dialog-body'));

    // Wait a tick to give any false resolve a chance to surface.
    await new Promise((r) => setTimeout(r, 10));
    expect(resolved).toBe(false);

    // Cleanup: cancel so the promise resolves.
    await fireEvent.click(screen.getByTestId('confirm-dialog-cancel'));
    await promise;
  });

  it('renders title + body + custom button labels', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({
      title: 'Delete pool DAYTIME',
      body: 'Replace 247 tracks. This cannot be undone.',
      confirmLabel: 'Delete',
      cancelLabel: 'Keep it',
    });

    expect(await screen.findByTestId('confirm-dialog-title')).toHaveTextContent(
      'Delete pool DAYTIME',
    );
    expect(screen.getByTestId('confirm-dialog-body')).toHaveTextContent(
      'Replace 247 tracks',
    );
    expect(screen.getByTestId('confirm-dialog-confirm')).toHaveTextContent('Delete');
    expect(screen.getByTestId('confirm-dialog-cancel')).toHaveTextContent('Keep it');

    await fireEvent.click(screen.getByTestId('confirm-dialog-cancel'));
    await promise;
  });

  it('applies the danger class when danger: true', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({
      title: 'X',
      body: 'Y',
      danger: true,
    });

    const confirmBtn = await screen.findByTestId('confirm-dialog-confirm');
    expect(confirmBtn).toHaveClass('danger');

    await fireEvent.click(screen.getByTestId('confirm-dialog-cancel'));
    await promise;
  });

  it('omits the danger class when danger is unset / false', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({ title: 'X', body: 'Y' });

    const confirmBtn = await screen.findByTestId('confirm-dialog-confirm');
    expect(confirmBtn).not.toHaveClass('danger');

    await fireEvent.click(screen.getByTestId('confirm-dialog-cancel'));
    await promise;
  });

  it('is NOT rendered when the store is closed', () => {
    render(ConfirmDialog);
    // No confirmDialog() call yet — store stays at the initial closed
    // state from beforeEach.
    expect(screen.queryByTestId('confirm-dialog')).not.toBeInTheDocument();
  });

  it('closes (unmounts the dialog) after Confirm resolves', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({ title: 'X', body: 'Y' });
    await screen.findByTestId('confirm-dialog');

    await fireEvent.click(screen.getByTestId('confirm-dialog-confirm'));
    await promise;

    await waitFor(() => {
      expect(screen.queryByTestId('confirm-dialog')).not.toBeInTheDocument();
    });
  });

  it('🟡-1 stacking guard: a second confirmDialog() call auto-resolves the first as false', async () => {
    // Before the guard, the first promise dangled forever — the
    // store.set() in the original implementation overwrote the prior
    // resolver without releasing it. With the update()-based guard
    // in place, the prior caller sees a clean `false`.
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(ConfirmDialog);

    const first = confirmDialog({ title: 'First', body: '...' });
    // Synchronously, without awaiting `first` — the bug we're
    // guarding against is exactly this pattern (forgot to await,
    // OR genuinely concurrent triggers).
    const second = confirmDialog({ title: 'Second', body: '...' });

    // First promise resolves to false (auto-cancelled by the guard).
    expect(await first).toBe(false);

    // Second dialog is the one rendered now.
    expect(await screen.findByTestId('confirm-dialog-title')).toHaveTextContent(
      'Second',
    );

    // Warning was emitted so the unusual stacking is findable in logs.
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringMatching(/another was open/i),
    );

    // Cleanup: cancel the second so the test ends.
    await fireEvent.click(screen.getByTestId('confirm-dialog-cancel'));
    expect(await second).toBe(false);
  });

  it('focuses the Cancel button on open (safer default for destructive ops)', async () => {
    render(ConfirmDialog);
    const promise = confirmDialog({ title: 'X', body: 'Y', danger: true });

    const cancelBtn = await screen.findByTestId('confirm-dialog-cancel');
    await waitFor(() => {
      expect(document.activeElement).toBe(cancelBtn);
    });

    await fireEvent.click(cancelBtn);
    await promise;
  });
});
