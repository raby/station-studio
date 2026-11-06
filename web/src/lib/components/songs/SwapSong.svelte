<script lang="ts">
  /**
   * Swap a song of the week for another, just this week: search the
   * station's pools (the rule's own first) and pick one. Songs already in
   * the week say where they play, so a repeat is a choice, not a
   * surprise. The song its rule picked isn't offered (that's Undo).
   */
  import { onDestroy, onMount, tick } from 'svelte';
  import { errorText, searchStationSongs, type StationSong } from '$lib/api';

  interface Props {
    slug: string;
    /** The song being swapped, in words. */
    song: string;
    /** Its rule's pool: searched first, and listed with no words typed. */
    pool: string;
    /** The song playing there now, and the one its rule picks (not offered). */
    currentTrackId: number | null;
    ruleTrackId: number | null;
    /** Where each song of the week plays (positions, from 1). */
    inWeek: Map<number, number[]>;
    /** Saving the swap: nothing can be picked. */
    busy?: boolean;
    error?: string | null;
    onpick: (song: StationSong) => void;
    oncancel: () => void;
    /** For tests: the search's debounce. */
    debounceMs?: number;
  }

  let {
    slug, song, pool, currentTrackId, ruleTrackId, inWeek, busy = false, error = null,
    onpick, oncancel, debounceMs = 250,
  }: Props = $props();

  let query = $state('');
  let songs: StationSong[] = $state([]);
  let more = $state(false);
  let searching = $state(false);
  let searchError: string | null = $state(null);
  let input: HTMLInputElement | null = $state(null);
  let opener: Element | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  /** Only the latest search's answer counts. */
  let asked = 0;

  async function search(q: string): Promise<void> {
    const mine = ++asked;
    searching = true;
    searchError = null;
    try {
      const r = await searchStationSongs(slug, q.trim(), pool);
      if (mine !== asked) return;
      songs = r.songs.filter((s) => s.id !== ruleTrackId);
      more = r.more;
    } catch (e) {
      if (mine !== asked) return;
      searchError = errorText(e);
    } finally {
      if (mine === asked) searching = false;
    }
  }

  function onInput(): void {
    clearTimeout(timer);
    timer = setTimeout(() => void search(query), debounceMs);
  }

  onMount(() => {
    opener = document.activeElement;
    void search('');
    void tick().then(() => input?.focus());
  });
  onDestroy(() => {
    clearTimeout(timer);
    // Closed by a pick: focus goes back to Swap song, not lost to the page.
    const lost = document.activeElement === null || document.activeElement === document.body;
    if (lost && opener instanceof HTMLElement && opener.isConnected) opener.focus();
  });

  function cancel(): void {
    oncancel();
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
  }
  let dialog: HTMLDivElement | null = $state(null);
  function onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape') {
      e.preventDefault();
      cancel();
    } else if (e.key === 'Tab' && dialog) {
      // Keep focus in the dialog.
      const all = [...dialog.querySelectorAll<HTMLElement>('input, button:not(:disabled)')];
      if (all.length === 0) return;
      const first = all[0];
      const last = all[all.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }
  function plays(id: number): string | null {
    const at = inWeek.get(id);
    if (!at || at.length === 0) return null;
    const list = at.length <= 3 ? at.join(', ') : `${at.slice(0, 3).join(', ')} and ${at.length - 3} more`;
    return `In this week already: song ${list}`;
  }
  const summary = $derived(
    searching ? 'Searching…'
      : searchError ? ''
        : songs.length === 0 ? (query.trim() ? 'No songs match.' : `No songs in ${pool}.`)
          : `${songs.length}${more ? '+' : ''} ${songs.length === 1 ? 'song' : 'songs'}${more ? ': type more to narrow it' : ''}`,
  );
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div
  class="backdrop"
  role="presentation"
  data-testid="swap-song-backdrop"
  onclick={(e) => e.target === e.currentTarget && cancel()}
>
  <div
    class="dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="swap-song-title"
    aria-describedby="swap-song-what"
    data-testid="swap-song"
    tabindex="-1"
    bind:this={dialog}
    onkeydown={onKey}
  >
    <h2 id="swap-song-title">Swap song, just this week</h2>
    <p id="swap-song-what" class="what">{song}</p>
    <label class="search">
      <span>Search the station's songs</span>
      <input
        type="search"
        bind:value={query}
        bind:this={input}
        oninput={onInput}
        placeholder="Title, artist or album"
        autocomplete="off"
        data-testid="swap-song-search"
        aria-describedby="swap-song-count"
      />
    </label>
    <p id="swap-song-count" class="count" role="status" data-testid="swap-song-count">{summary}</p>
    {#if searchError}<p class="error" role="alert" data-testid="swap-song-search-error">Search failed: {searchError}</p>{/if}
    {#if error}<p class="error" role="alert" data-testid="swap-song-error">{error}</p>{/if}
    <ul class="results" data-testid="swap-song-results" aria-busy={busy}>
      {#each songs as s (s.id)}
        {@const repeat = plays(s.id)}
        <li>
          <button
            type="button"
            class="result"
            data-testid="swap-song-result"
            disabled={busy || s.id === currentTrackId}
            onclick={() => onpick(s)}
          >
            <span class="title">{s.name}</span>
            {#if s.artist}<span class="artist">{s.artist}</span>{/if}
            <span class="meta">
              {s.pools.join(', ')}{#if s.id === currentTrackId}{' '}· playing here now{/if}
              {#if repeat}<span class="repeat" data-testid="swap-song-repeat">{' '}· {repeat}</span>{/if}
            </span>
          </button>
        </li>
      {/each}
    </ul>
    <div class="actions">
      <button type="button" data-testid="swap-song-cancel" onclick={cancel}>Cancel</button>
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
    max-width: 34rem;
    width: calc(100% - 2rem);
    max-height: calc(100vh - 2rem);
    display: flex;
    flex-direction: column;
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
  }
  h2 {
    margin: 0 0 0.25rem;
    font-size: 1.1rem;
  }
  .what {
    margin: 0 0 0.75rem;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .search {
    display: grid;
    gap: 0.25rem;
    font-size: 0.85rem;
  }
  .search input {
    font: inherit;
    font-size: 1rem;
    padding: 0.4rem 0.5rem;
  }
  .count {
    margin: 0.4rem 0;
    font-size: 0.85rem;
    color: var(--muted);
    min-height: 1.2em;
  }
  .error {
    margin: 0 0 0.5rem;
    color: var(--danger);
    font-size: 0.85rem;
  }
  .results {
    list-style: none;
    margin: 0;
    padding: 0;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: 6px;
    min-height: 6rem;
  }
  .results li + li {
    border-top: 1px solid var(--border);
  }
  .result {
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-areas: 'title artist' 'meta meta';
    gap: 0.1rem 0.75rem;
    width: 100%;
    text-align: left;
    padding: 0.45rem 0.65rem;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--text);
    cursor: pointer;
  }
  .result:hover:not(:disabled),
  .result:focus-visible {
    background: var(--bg-hover, var(--bg));
  }
  .result:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .result:disabled {
    cursor: default;
    opacity: 0.6;
  }
  .title {
    grid-area: title;
    font-weight: 600;
    overflow-wrap: anywhere;
  }
  .artist {
    grid-area: artist;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .meta {
    grid-area: meta;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .repeat {
    color: var(--warn);
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    margin-top: 0.75rem;
  }
</style>
