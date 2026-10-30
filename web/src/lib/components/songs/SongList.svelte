<script lang="ts">
  /**
   * A week's songs, each with the rule that put it there and why, in
   * playing order under the station's section names.
   *
   * Read-only on its own: clicking a song calls `onselect` with its row
   * (the rule's block and editor ref included), and the page decides
   * what that opens. With `saved` (the same week as saved), songs that
   * changed are marked.
   */
  import type { Trace } from '$lib/api';
  import { sourceColour, type Sidecar, type StationTemplate } from '$lib/editor';
  import { filterGroups, removedCount, rowRule, ruleKey, songGroups, type RuleDefaults, type SongRow } from '$lib/songList';

  interface Props {
    trace: Trace;
    template: StationTemplate;
    sidecar: Sidecar;
    /** The same week as saved, to mark changes against. */
    saved?: Trace | null;
    /** The selected song's position (1-based), if any. */
    selected?: number | null;
    /** Mark every song made by the rule with this key (ruleKey), so the
     *  page can show which songs a change to that rule touches. */
    related?: string | null;
    onselect?: (row: SongRow) => void;
    /** The station's defaults for settings a rule leaves out. */
    ruleDefaults?: RuleDefaults;
  }

  let { trace, template, sidecar, saved = null, selected = null, related = null, onselect, ruleDefaults = {} }: Props = $props();

  const allGroups = $derived(songGroups(trace, template, sidecar, saved));
  const removed = $derived(removedCount(trace, saved));

  // Search: song, artist, the rule in words, macro or section.
  let query = $state('');
  let onlyChanged = $state(false);
  const changedCount = $derived(allGroups.reduce((n, g) => n + g.rows.filter((r) => r.change !== null).length, 0));
  // "Only changed" applies while there's something to compare with.
  const filtering = $derived(!!query.trim() || (onlyChanged && saved !== null));
  const groups = $derived(filterGroups(allGroups, query, onlyChanged && saved !== null, ruleDefaults));
  const shown = $derived(groups.reduce((n, g) => n + g.rows.length, 0));

  function clear(): void {
    query = '';
    onlyChanged = false;
  }
</script>

<div class="song-filters" data-testid="song-filters">
  <input
    type="search"
    placeholder="Search songs, artists or rules…"
    aria-label="Search songs, artists or rules"
    autocomplete="off"
    data-testid="song-search"
    bind:value={query}
  />
  {#if saved !== null}
    <label class="only-changed">
      <input type="checkbox" data-testid="song-only-changed" bind:checked={onlyChanged} />
      Only changed songs ({changedCount})
    </label>
  {/if}
  {#if filtering}
    <span class="shown" data-testid="song-shown" aria-live="polite">
      Showing {shown.toLocaleString()} of {trace.slots.length.toLocaleString()}
    </span>
    <button type="button" class="clear" data-testid="song-search-clear" onclick={clear}>Clear</button>
  {/if}
</div>

<section class="song-list" aria-label="Songs this week" data-testid="song-list">
  {#each groups as group, gi (gi)}
    <h3 class="section-name" data-testid="song-section">
      {group.sectionName}
      <span class="count">{group.rows.length} {group.rows.length === 1 ? 'song' : 'songs'}</span>
    </h3>
    <ol class="songs" start={group.rows[0].position}>
      {#each group.rows as row (row.position)}
        <li>
          <button
            type="button"
            class="song"
            class:selected={selected === row.position}
            class:changed={row.change !== null}
            class:related={related !== null && row.ref !== null && ruleKey(row.ref) === related}
            aria-pressed={selected === row.position}
            data-testid="song-row"
            onclick={() => onselect?.(row)}
          >
            <span class="position">{row.position}</span>
            <span class="track">
              <span class="title">{row.slot.track_name}</span>
              {#if row.slot.track_artist}<span class="artist">{row.slot.track_artist}</span>{/if}
            </span>
            <span class="rule" data-testid="song-rule">
              <span
                class="dot"
                style:background={sourceColour(template, row.slot.source)}
                aria-hidden="true"
              ></span>
              {rowRule(row, ruleDefaults)}{#if row.macroName}<span class="macro">{' '}· {row.macroName}</span>{/if}{#if row.ruleChanged}<span
                  class="macro" data-testid="song-rule-changed">{' '}· rule changed since</span>{/if}
            </span>
            {#if row.change}
              <span class="change" data-testid="song-change">{row.change === 'added' ? 'new' : 'changed'}</span>
            {/if}
            <span class="why" data-testid="song-why"><span class="visually-hidden">Why:</span> {row.slot.reason}</span>
          </button>
        </li>
      {/each}
    </ol>
  {/each}
  {#if filtering && shown === 0}
    <p class="none" role="status" data-testid="song-search-empty">
      No songs match.
      <button type="button" class="clear" onclick={clear}>Clear the search</button>
    </p>
  {/if}
  {#if removed > 0}
    <p class="removed" data-testid="song-removed">
      {removed} {removed === 1 ? 'song' : 'songs'} from the saved week {removed === 1 ? 'is' : 'are'} no longer in it.
    </p>
  {/if}
</section>

<style>
  .song-filters {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem 1rem;
    margin: 0 0 0.75rem;
  }
  .song-filters input[type='search'] {
    flex: 1 1 16rem;
    max-width: 28rem;
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--panel);
    color: inherit;
    font: inherit;
  }
  .only-changed {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }
  .shown {
    color: var(--muted);
  }
  .clear {
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    text-decoration: underline;
    cursor: pointer;
    font: inherit;
  }
  .none {
    margin: 0.5rem 0;
    color: var(--muted);
  }
  .song-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .section-name {
    margin: 1rem 0 0.25rem;
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
  }
  .section-name:first-child {
    margin-top: 0;
  }
  .count {
    margin-left: 0.5rem;
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0;
    color: var(--faint);
  }
  .songs {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius, 6px);
    background: var(--panel);
    overflow: hidden;
  }
  .songs li + li {
    border-top: 1px solid var(--border);
  }
  /* A week can be 2,000+ songs: skip laying out rows that are off
     screen (they stay in the DOM, so find-in-page and focus still work). */
  .songs li {
    content-visibility: auto;
    contain-intrinsic-size: auto 4rem;
  }
  .song {
    display: grid;
    grid-template-columns: 2.5rem minmax(0, 1fr) minmax(0, 1fr) auto;
    grid-template-areas:
      'pos track rule change'
      'pos why   why  why';
    column-gap: 0.75rem;
    row-gap: 0.15rem;
    align-items: baseline;
    width: 100%;
    padding: 0.5rem 0.75rem;
    border: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .song:hover {
    background: var(--row-hover);
  }
  .song.related {
    background: var(--accent-soft);
  }
  .song.selected {
    background: var(--row-active);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .song:focus-visible {
    outline: 2px solid var(--focus, var(--accent));
    outline-offset: -2px;
  }
  .position {
    grid-area: pos;
    color: var(--faint);
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .track {
    grid-area: track;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .title {
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .artist {
    color: var(--muted);
    font-size: 0.9em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rule {
    grid-area: rule;
    min-width: 0;
    font-size: 0.9em;
  }
  .dot {
    display: inline-block;
    width: 0.6em;
    height: 0.6em;
    margin-right: 0.4em;
    border-radius: 50%;
    vertical-align: baseline;
  }
  .macro {
    color: var(--muted);
  }
  .change {
    grid-area: change;
    padding: 0.05em 0.5em;
    border-radius: 999px;
    background: var(--warn-soft);
    color: var(--warn);
    font-size: 0.75rem;
    font-weight: 600;
  }
  .why {
    grid-area: why;
    color: var(--muted);
    font-size: 0.8em;
    font-family: var(--font-mono, ui-monospace, monospace);
    overflow-wrap: anywhere;
  }
  .removed {
    margin: 0.5rem 0 0;
    color: var(--muted);
    font-size: 0.9em;
  }
  /* Narrow screens: rule under the song, the why under that. */
  @media (max-width: 40rem) {
    .song {
      grid-template-columns: 2rem minmax(0, 1fr) auto;
      grid-template-areas:
        'pos track change'
        'pos rule  rule'
        'pos why   why';
    }
  }
</style>
