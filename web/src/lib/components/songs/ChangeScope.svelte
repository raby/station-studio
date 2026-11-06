<script lang="ts" module>
  export type ChangeScopeChoice = 'every' | 'week';
  const LAST_KEY = 'changeScope';

  /** The choice made last time (offered first next time). */
  export function lastScope(): ChangeScopeChoice {
    try {
      return window.localStorage.getItem(LAST_KEY) === 'week' ? 'week' : 'every';
    } catch {
      return 'every';
    }
  }
  function remember(choice: ChangeScopeChoice): void {
    try {
      window.localStorage.setItem(LAST_KEY, choice);
    } catch {
      // Privacy mode or full: it just isn't remembered.
    }
  }
</script>

<script lang="ts">
  /**
   * Asked when a song's rule is changed on a week's page: for every week
   * (the rule itself, queued with the other changes to save), or just
   * this song, this week (saved now, as a new version of the week).
   *
   * Quick to answer: the choice made last time has focus, so Enter takes
   * it; Escape (or the backdrop) cancels and nothing changes.
   */
  import { onDestroy, onMount, tick } from 'svelte';

  interface Props {
    /** The song, in words ("song 7, Tuesday: Song by Band"). */
    song: string;
    week: number;
    /** Why it can't be just this week, or null when it can. */
    weekOnlyBlocked: string | null;
    onchoose: (choice: ChangeScopeChoice) => void;
    oncancel: () => void;
  }

  let { song, week, weekOnlyBlocked, onchoose, oncancel }: Props = $props();

  let everyButton: HTMLButtonElement | null = $state(null);
  let weekButton: HTMLButtonElement | null = $state(null);
  /** Where focus was, to go back to on cancel. */
  let opener: Element | null = null;

  onMount(() => {
    opener = document.activeElement;
    void tick().then(() => {
      const first = lastScope() === 'week' && !weekOnlyBlocked ? weekButton : everyButton;
      first?.focus();
    });
  });

  // Closed by a choice: focus goes back where it was (the panel's
  // Apply), not lost to the page, when nothing else took it.
  onDestroy(() => {
    const lost = document.activeElement === null || document.activeElement === document.body;
    if (lost && opener instanceof HTMLElement && opener.isConnected) opener.focus();
  });

  function choose(choice: ChangeScopeChoice): void {
    if (choice === 'week' && weekOnlyBlocked) return;
    remember(choice);
    onchoose(choice);
  }
  function cancel(): void {
    oncancel();
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
  }
  function onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape') {
      e.preventDefault();
      // Only the dialog closes: the rule panel behind (which closes on
      // Escape anywhere) stays open.
      e.stopPropagation();
      cancel();
    } else if (e.key === 'Tab') {
      // Keep focus in the dialog: its buttons, round and round.
      // The blocked choice stays reachable, so its reason is read.
      const focusable = [everyButton, weekButton, cancelButton].filter(
        (b): b is HTMLButtonElement => b !== null,
      );
      const at = focusable.indexOf(document.activeElement as HTMLButtonElement);
      const next = e.shiftKey ? (at <= 0 ? focusable.length - 1 : at - 1) : (at + 1) % focusable.length;
      e.preventDefault();
      focusable[next]?.focus();
    }
  }
  let cancelButton: HTMLButtonElement | null = $state(null);
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div
  class="backdrop"
  role="presentation"
  data-testid="change-scope-backdrop"
  onclick={(e) => e.target === e.currentTarget && cancel()}
>
  <div
    class="dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="change-scope-title"
    aria-describedby="change-scope-song"
    data-testid="change-scope"
    tabindex="-1"
    onkeydown={onKey}
  >
    <h2 id="change-scope-title">Change it for…</h2>
    <p id="change-scope-song" class="song">{song}</p>
    <div class="choices">
      <button
        type="button"
        class="choice"
        data-testid="change-scope-every"
        bind:this={everyButton}
        onclick={() => choose('every')}
      >
        <span class="label">Every week</span>
        <span class="what">The rule itself. Queued with your other changes; saved for every week when you save.</span>
      </button>
      <button
        type="button"
        class="choice"
        data-testid="change-scope-week"
        aria-disabled={weekOnlyBlocked ? 'true' : undefined}
        aria-describedby={weekOnlyBlocked ? 'change-scope-why' : undefined}
        bind:this={weekButton}
        onclick={() => choose('week')}
      >
        <span class="label">Just this song, week {week}</span>
        <span class="what">Only this song changes. Saved now as a new version of the week, and kept when it's regenerated.</span>
      </button>
      {#if weekOnlyBlocked}
        <p id="change-scope-why" class="why" data-testid="change-scope-why">{weekOnlyBlocked}</p>
      {/if}
    </div>
    <div class="actions">
      <button type="button" data-testid="change-scope-cancel" bind:this={cancelButton} onclick={cancel}>Cancel</button>
    </div>
  </div>
</div>

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
    color: var(--text);
    border-radius: 8px;
    padding: 1.25rem 1.5rem;
    max-width: 30rem;
    width: calc(100% - 2rem);
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
  }
  h2 {
    margin: 0 0 0.25rem;
    font-size: 1.1rem;
  }
  .song {
    margin: 0 0 1rem;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .choices {
    display: grid;
    gap: 0.5rem;
  }
  .choice {
    display: grid;
    gap: 0.2rem;
    text-align: left;
    padding: 0.65rem 0.85rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    cursor: pointer;
  }
  .choice:hover:not([aria-disabled='true']),
  .choice:focus-visible {
    border-color: var(--accent);
  }
  .choice:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .choice[aria-disabled='true'] {
    cursor: not-allowed;
    opacity: 0.6;
  }
  .label {
    font-weight: 600;
  }
  .what {
    font-size: 0.85rem;
    color: var(--muted);
  }
  .why {
    margin: 0;
    font-size: 0.85rem;
    color: var(--warn);
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    margin-top: 1rem;
  }
</style>
