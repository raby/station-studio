<script lang="ts">
  /**
   * The Help page: a short guide to Station Studio for someone using it
   * for the first time, in the style of Station Player's Help.
   */
  type Part = string | { b: string };
  interface Topic {
    id: string;
    title: string;
    lines: Part[][];
    link?: { href: string; label: string };
  }

  const TOPICS: Topic[] = [
    {
      id: 'start',
      title: 'Getting started',
      lines: [
        ['Station Studio builds a radio station’s playlists, week by week, from your music and the rules you give it.'],
        ['Three steps: import your music as ', { b: 'Source pools' }, ', make a ', { b: 'station' }, ', then ', { b: 'generate' }, ' its weeks.'],
        ['Rebirth Radio and Rebirth Classics are there to start with, so you can see how a finished station looks.'],
      ],
    },
    {
      id: 'pools',
      title: 'Source pools',
      lines: [
        ['A source pool is a playlist exported from Music or iTunes as XML (File › Library › Export Playlist). Give it a name, like DAYTIME or JINGLES, and import it under ', { b: 'Source pools' }, '.'],
        ['A station asks for its pools by name, so the name must match what the station expects. A pool the station needs but you haven’t imported shows as ', { b: 'not imported' }, ' on the station; click it to import it.'],
        ['Re-import a pool when its playlist changes. ', { b: 'Last imported' }, ' shows when each was last brought in.'],
      ],
      link: { href: '/sources', label: 'Go to Source pools' },
    },
    {
      id: 'stations',
      title: 'Stations',
      lines: [
        [{ b: 'New station' }, ' in the sidebar makes one: its name, which pools it uses, and how it moves through them each week.'],
        ['On a station, ', { b: 'Visualise template' }, ' shows the week as blocks of picks you can rearrange, and ', { b: 'Rules' }, ' changes how strict it is about repeating artists and tracks.'],
        [{ b: 'Fork station' }, ' copies a station to try changes without touching the original.'],
      ],
      link: { href: '/stations/new', label: 'Make a station' },
    },
    {
      id: 'generate',
      title: 'Making the weekly playlists',
      lines: [
        ['On a station, ', { b: 'Generate week 1' }, ' (then ', { b: 'Generate next week' }, ') makes that week’s playlist. Each one is listed under Playlists, with why every track was picked.'],
        [{ b: 'Export to disk' }, ' on a playlist writes it into the exports folder as iTunes XML, and ', { b: 'Download iTunes XML' }, ' saves a copy wherever you like.'],
        ['Station Player finds the exports folder by itself, so a week you export shows up there as a playlist.'],
      ],
    },
    {
      id: 'app',
      title: 'The app',
      lines: [
        ['When a new version is out, ', { b: 'Update now' }, ' appears at the foot of the sidebar. Your stations, pools and playlists are kept.'],
        [{ b: 'Settings' }, ' has Check for updates, ', { b: 'Start Station Studio in the background when I log in' }, ', and whether it opens in a window of its own.'],
        [{ b: 'On a Mac' }, ', the downloaded Station Studio has a window of its own, like Apple Music: closing the window leaves Station Studio running, clicking its Dock icon brings it back, and its menus have Go, Settings (⌘,) and Quit (⌘Q). Right-click its Dock icon for New station and Source pools.'],
        [{ b: 'Install Station Studio as an app' }, ' (in Settings, with Chrome, Edge or Brave) gives it its own icon in the Dock or taskbar. Right-click that icon for New station and Source pools.'],
        [{ b: 'Quit' }, ' at the foot of the sidebar stops Station Studio until you open it again.'],
      ],
      link: { href: '/settings', label: 'Go to Settings' },
    },
  ];
</script>

<svelte:head>
  <title>Help · Station Studio</title>
</svelte:head>

<main id="main-content" tabindex="-1">
  <header class="view-head">
    <div class="kicker">Station Studio</div>
    <h1>Help</h1>
  </header>

  <nav class="toc" aria-label="Help topics">
    {#each TOPICS as t}
      <a href="#help-{t.id}">{t.title}</a>
    {/each}
  </nav>

  {#each TOPICS as t}
    <section class="card" id="help-{t.id}" data-testid="help-{t.id}">
      <h2>{t.title}</h2>
      <ul>
        {#each t.lines as line}
          <li>
            {#each line as part}{#if typeof part === 'string'}{part}{:else}<strong>{part.b}</strong>{/if}{/each}
          </li>
        {/each}
      </ul>
      {#if t.link}
        <a class="go" href={t.link.href}>{t.link.label} →</a>
      {/if}
    </section>
  {/each}
</main>

<style>
  main {
    max-width: 780px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .view-head {
    margin-bottom: 14px;
  }
  .kicker {
    color: var(--accent);
    font-size: 12px;
    font-weight: 650;
    letter-spacing: 0.08em;
    margin-bottom: 4px;
    text-transform: uppercase;
  }
  h1 {
    font-size: 28px;
    margin: 0;
  }
  .toc {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 18px;
  }
  .toc a {
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--muted);
    font-size: 13px;
    padding: 3px 11px;
    text-decoration: none;
  }
  .toc a:hover {
    color: var(--text);
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 14px;
    padding: 16px 20px;
    scroll-margin-top: 16px;
  }
  h2 {
    font-size: 17px;
    margin: 0 0 8px;
  }
  ul {
    margin: 0;
    padding-left: 1.1rem;
  }
  li {
    color: var(--muted);
    margin: 6px 0;
  }
  li strong {
    color: var(--text);
    font-weight: 600;
  }
  .go {
    display: inline-block;
    font-weight: 600;
    margin-top: 6px;
    text-decoration: none;
  }
</style>
