<script lang="ts">
  /**
   * The week's just-this-week changes, one line each, with an Undo.
   * Undo is saved at once (a new version of the week, the rule's own song
   * back). Focus then goes to the next change's Undo, or to `emptyFocus`
   * once there are none, and the undo is announced.
   */
  import { tick } from 'svelte';
  import type { WeekChange } from '$lib/api';
  import type { StationTemplate } from '$lib/editor';
  import { changedFields, changeWords } from '$lib/weekChanges';

  interface Props {
    changes: WeekChange[];
    /** The station's rules as saved: what a rule change changed of them. */
    template: StationTemplate;
    /** The week's songs, for each change's song and day. */
    units: Map<number, string>;
    /** Undo it; true if it was. */
    onundo: (change: WeekChange) => Promise<boolean>;
    /** Pick the song in the list. */
    onshow?: (position: number) => void;
    busy?: boolean;
    emptyFocus?: () => HTMLElement | null;
  }

  let { changes, template, units, onundo, onshow, busy = false, emptyFocus }: Props = $props();

  /** In the week's order; one this version doesn't play, last. */
  const shown = $derived(
    [...changes].sort((a, b) => (a.position ?? Infinity) - (b.position ?? Infinity) || a.id - b.id),
  );

  let list: HTMLUListElement | null = $state(null);
  let announced = $state('');

  function words(c: WeekChange): string {
    return changeWords({
      kind: c.kind,
      track: c.track,
      fields: c.kind === 'rule' && c.rule ? changedFields(template, c.block, c.rule) : null,
    });
  }
  function where(c: WeekChange): string {
    if (c.position === null) return 'Not in this version of the week';
    const unit = units.get(c.position);
    return `Song ${c.position}${unit ? ` (${unit})` : ''}`;
  }

  async function undo(c: WeekChange, at: number): Promise<void> {
    if (busy) return;
    const label = `${where(c)}: ${words(c)}`;
    if (!(await onundo(c))) return;
    announced = `Undone: ${label}.`;
    await tick();
    const buttons = list?.querySelectorAll<HTMLButtonElement>('[data-testid="week-only-undo"]') ?? [];
    const next = buttons[Math.min(at, buttons.length - 1)];
    (next ?? emptyFocus?.())?.focus();
  }
</script>

{#if changes.length > 0}
  <section class="week-only" aria-labelledby="week-only-title" data-testid="week-only">
    <h3 id="week-only-title">This week only <span class="count">({changes.length})</span></h3>
    <ul bind:this={list}>
      {#each shown as c, i (c.id)}
        <li data-testid="week-only-item">
          <span class="where">
            {#if c.position !== null && onshow}
              <button type="button" class="link" data-testid="week-only-show" onclick={() => onshow(c.position!)}>{where(c)}</button>
            {:else}{where(c)}{/if}
          </span>
          <span class="words">{words(c)}</span>
          {#if c.position === null}
            <span class="note" data-testid="week-only-pending">The station's rules have moved since this week was made: regenerate it to hear this change.</span>
          {:else if c.also_plays_at.length > 0}
            <span class="note" data-testid="week-only-repeat">Also plays as song {c.also_plays_at.join(', ')}.</span>
          {/if}
          <button
            type="button"
            class="undo"
            data-testid="week-only-undo"
            disabled={busy}
            aria-label="Undo {where(c)}: {words(c)}"
            onclick={() => undo(c, i)}
          >Undo</button>
        </li>
      {/each}
    </ul>
  </section>
{/if}
<p class="visually-hidden" role="status" aria-live="polite">{announced}</p>

<style>
  .week-only {
    margin: 0 0 0.75rem;
    padding: 0.6rem 0.85rem;
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 6px;
    background: var(--panel);
  }
  h3 {
    margin: 0 0 0.35rem;
    font-size: 0.95rem;
  }
  .count {
    color: var(--muted);
    font-weight: normal;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 0.3rem;
  }
  li {
    display: grid;
    grid-template-columns: auto 1fr auto;
    grid-template-areas: 'where words undo' 'note note note';
    gap: 0.1rem 0.6rem;
    align-items: baseline;
  }
  .where {
    grid-area: where;
    font-weight: 600;
    white-space: nowrap;
  }
  .words {
    grid-area: words;
    overflow-wrap: anywhere;
  }
  .note {
    grid-area: note;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .undo {
    grid-area: undo;
  }
  .link {
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    font: inherit;
    font-weight: 600;
    text-decoration: underline;
    cursor: pointer;
  }
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
  @media (max-width: 40rem) {
    li {
      grid-template-columns: 1fr auto;
      grid-template-areas: 'where undo' 'words words' 'note note';
    }
  }
</style>
