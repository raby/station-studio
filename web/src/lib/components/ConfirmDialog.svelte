<script lang="ts">
  /**
   * §16.25.t4 — confirm dialog host.
   *
   * Single instance mounted in `+layout.svelte`. Subscribes to the
   * `confirmDialogState` store; renders the modal when `open` and
   * resolves the outstanding promise on Confirm / Cancel / Esc /
   * click-outside.
   *
   * Design choices:
   * - Cancel button gets initial focus (safer default for destructive
   *   ops than auto-focusing the Confirm).
   * - Esc + click-outside both cancel (returns false).
   * - `body` is a plain string today; line-break support is preserved
   *   by CSS `white-space: pre-line`. Rich markup (HTML) would need
   *   `{@html}` — not enabled, no caller needs it.
   * - No focus trap: keyboard tab order is "Cancel → Confirm → Cancel"
   *   (only 2 focusable elements; native tab cycling stays in-dialog
   *   without explicit trap logic).
   */
  import { onMount, tick } from 'svelte';
  import {
    confirmDialogState,
    answerConfirmDialog,
  } from '$lib/confirm';

  $: state = $confirmDialogState;

  let cancelButton: HTMLButtonElement | null = null;

  // Focus the Cancel button on open. Watched reactively rather than
  // via `onMount` because the dialog mounts/unmounts via `{#if}`.
  $: if (state.open) focusCancelOnNextTick();

  async function focusCancelOnNextTick() {
    await tick();
    cancelButton?.focus();
  }

  function handleConfirm() {
    answerConfirmDialog(true);
  }
  function handleCancel() {
    answerConfirmDialog(false);
  }
  function handleBackdropClick(event: MouseEvent) {
    // Only fire when the click landed on the backdrop itself, NOT on
    // the dialog card. `currentTarget` is the backdrop element; if
    // `target` equals it, the click was outside the dialog content.
    if (event.target === event.currentTarget) handleCancel();
  }
  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault();
      handleCancel();
    }
  }

  // Global keydown listener — attached when open, cleaned up on close.
  // Cheaper than a node-level listener (the backdrop only catches Esc
  // if focus is inside the dialog; this catches it everywhere).
  onMount(() => {
    const sub = confirmDialogState.subscribe((s) => {
      if (s.open) {
        window.addEventListener('keydown', handleKeydown);
      } else {
        window.removeEventListener('keydown', handleKeydown);
      }
    });
    return () => {
      window.removeEventListener('keydown', handleKeydown);
      sub();
    };
  });
</script>

{#if state.open}
  <!-- Backdrop click-outside is the secondary dismiss path (Esc is
       primary). `role=presentation` flags the backdrop as decorative
       so screen readers skip it. -->
  <div
    class="backdrop"
    role="presentation"
    data-testid="confirm-dialog-backdrop"
    on:click={handleBackdropClick}
  >
    <div
      class="dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-body"
      data-testid="confirm-dialog"
    >
      <h2 id="confirm-dialog-title" data-testid="confirm-dialog-title">
        {state.title}
      </h2>
      <p
        id="confirm-dialog-body"
        class="body"
        data-testid="confirm-dialog-body"
      >
        {state.body}
      </p>
      <div class="actions">
        <button
          type="button"
          class="button"
          data-testid="confirm-dialog-cancel"
          bind:this={cancelButton}
          on:click={handleCancel}
        >
          {state.cancelLabel}
        </button>
        <button
          type="button"
          class="button primary"
          class:danger={state.danger}
          data-testid="confirm-dialog-confirm"
          on:click={handleConfirm}
        >
          {state.confirmLabel}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .dialog {
    background: var(--panel);
    border-radius: 8px;
    padding: 1.5rem;
    max-width: 32rem;
    width: calc(100% - 2rem);
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
  }
  h2 {
    margin: 0 0 0.75rem;
    font-size: 1.1rem;
  }
  .body {
    margin: 0 0 1.25rem;
    color: var(--text);
    line-height: 1.5;
    /* Caller copy may include line breaks for readability. */
    white-space: pre-line;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
  .button {
    padding: 0.5rem 1rem;
    border: 1px solid var(--border-strong);
    border-radius: 4px;
    background: var(--panel);
    cursor: pointer;
    font: inherit;
  }
  .button:hover {
    background: var(--panel-2);
  }
  .button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .button.primary {
    background: var(--accent);
    color: var(--accent-text);
    border-color: var(--accent);
  }
  .button.primary:hover {
    background: var(--accent);
  }
  .button.primary.danger {
    background: var(--danger);
    border-color: var(--danger);
  }
  .button.primary.danger:hover {
    background: var(--danger);
  }
</style>
