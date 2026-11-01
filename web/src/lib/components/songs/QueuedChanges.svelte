<script lang="ts">
  /**
   * The queued (not yet saved) changes to a station's rules, one line
   * each in plain words, each with an Undo. The week page and the
   * station editor both show it; `queuedChanges` makes the lines.
   *
   * `onundo` gets the queued edits a change is and does the undoing; it
   * says whether it did (the editor may ask first, when later changes
   * build on it). Then focus moves to the next change's Undo, so undoing
   * several in a row needs no hunting, or to `emptyFocus` once there are
   * none; and the undone change is announced.
   */
  import { tick } from 'svelte';
  import type { QueuedChange } from '$lib/songList';

  interface Props {
    changes: QueuedChange[];
    /** Undo the change made of these queued edits; true if it was. */
    onundo: (indices: number[]) => boolean | Promise<boolean>;
    /** While saving: nothing can be undone. */
    busy?: boolean;
    /** Where focus goes when the last change is undone. */
    emptyFocus?: () => HTMLElement | null;
    /** Test id prefix: `<testid>-list`, `-item`, `-undo`. */
    testid?: string;
  }

  let { changes, onundo, busy = false, emptyFocus, testid = 'queued' }: Props = $props();

  let list: HTMLOListElement | null = $state(null);
  let announced = $state('');

  async function undo(change: QueuedChange, at: number): Promise<void> {
    if (!(await onundo(change.indices))) return;
    announced = `Undone: ${change.text}`;
    await tick();
    const buttons = list?.querySelectorAll<HTMLButtonElement>('button.undo') ?? [];
    const next = buttons.length > 0 ? buttons[Math.min(at, buttons.length - 1)] : (emptyFocus?.() ?? null);
    next?.focus();
  }
</script>

<!-- Outside the list, so it's still there to announce the last undo. -->
<span class="visually-hidden" role="status" aria-live="polite">{announced}</span>
{#if changes.length > 0}
  <ol class="queued-changes" bind:this={list} data-testid="{testid}-list">
    {#each changes as change, i (change.indices.join(','))}
      <li data-testid="{testid}-item">
        <span>{change.text}</span>
        <button
          type="button"
          class="undo"
          data-testid="{testid}-undo"
          aria-label="Undo: {change.text}"
          disabled={busy}
          onclick={() => undo(change, i)}
        >Undo</button>
      </li>
    {/each}
  </ol>
{/if}

<style>
  /* A long queue scrolls within the list rather than pushing what's
     below it away. */
  .queued-changes {
    max-height: 7.5rem;
    overflow-y: auto;
    margin: 0;
    padding-left: 1.4rem;
    font-size: 0.9em;
  }
  li {
    padding: 0.1rem 0;
  }
  .undo {
    margin-left: 0.5rem;
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    text-decoration: underline;
    cursor: pointer;
  }
  .undo:hover:not(:disabled) {
    color: var(--accent-strong);
  }
  .undo:disabled {
    cursor: default;
    opacity: 0.6;
  }
</style>
