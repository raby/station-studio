<script lang="ts">
  import { onMount } from 'svelte';
  import { getReport, type ProblemReport } from '$lib/api';

  // Help › Report a problem: this copy's details, from /api/report.
  let report: ProblemReport | null = null;
  onMount(() => {
    getReport()
      .then((r) => (report = r))
      .catch(() => {}); // the topic still says where to report, without the link
  });

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
    /** Buttons under it: another app (a station-player: link) or the docs site. */
    opens?: { href: string; label: string; external?: boolean }[];
  }

  const WHICH_APP = 'https://raby.github.io/station-studio/which-app/';

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
        ['Click a column’s heading to sort the list by it: ', { b: 'Last imported' }, ' puts the newest first, and a second click turns it round. The list stays sorted that way next time.'],
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
        [{ b: 'Export to disk' }, ' on a playlist writes it into the exports folder as iTunes XML, and ', { b: 'Download iTunes XML' }, ' saves a copy to your Downloads folder.'],
        ['Station Player and Station Server find the exports folder by themselves, so a week you export shows up there. ', { b: 'Settings › Exports' }, ' shows where it is, and in the app ', { b: 'Show in Finder' }, ' (or File Explorer) opens it.'],
      ],
    },
    {
      id: 'station-server',
      title: 'Sending weeks to Station Server',
      lines: [
        ['Station Server plays the station on its own computer. Station Studio can send each week straight to it over the network, with no shared folder.'],
        ['In Station Server, turn on ', { b: 'Settings › Station Studio' }, '. It shows its address and a link code: enter both in Station Studio’s ', { b: 'Settings › Station Server' }, ', and press ', { b: 'Test' }, '.'],
        ['On each station’s page, under ', { b: 'On Station Server' }, ', choose the station there it sends to. Tick ', { b: 'Also send each week when I export it to disk' }, ' to send each week as you export it, or use ', { b: 'Send to Station Server' }, ' on a week’s page.'],
        ['Station Server says how many of the week’s songs it has, lists any it can’t find, and says when the week goes on air. The station’s page shows what’s on air there, the weeks it has, and warns when it has nothing after this week.'],
      ],
      link: { href: '/settings', label: 'Go to Settings' },
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
    {
      id: 'apps',
      title: 'The other Station apps',
      lines: [
        ['Station Studio is one of three apps, each installed on its own.'],
        [{ b: 'Station Player' }, ' plays radio and music. A week you export here appears in it by itself, so you can hear it before it goes out.'],
        [{ b: 'Station Server' }, ' puts a station on air, to a SHOUTcast or Icecast server, playing the weeks Station Studio sends it (see Sending weeks to Station Server above).'],
        ['The buttons open them when they’re installed on this computer. ', { b: 'Which app do I need?' }, ' on the docs site says what each is for, with the downloads.'],
      ],
      opens: [
        { href: 'station-player://open', label: 'Open Station Player' },
        { href: 'station-server://open', label: 'Open Station Server' },
        { href: WHICH_APP, label: 'Which app do I need?', external: true },
      ],
    },
    {
      id: 'report',
      title: 'Report a problem',
      lines: [
        ['Something not working, or not as it should? Report it on GitHub, where Station Studio is made. The button opens a new report with this copy’s version and your computer already filled in: say what happened, what you expected, and how to make it happen again.'],
        ['You’ll need a free GitHub account. If something went wrong, the log file helps: have a look before you attach it, as it lists the names of files and folders Station Studio has used.'],
      ],
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
      {#if t.id === 'report'}
        {#if report}
          <p>
            {#if report.log_file}
              The log file: <code class="path" data-testid="log-file">{report.log_file}</code>
            {:else if report.in_docker}
              This copy runs in Docker, so its log is the container’s:
              <code class="path">docker compose logs station-studio</code> shows it.
            {:else}
              This copy writes its log in the terminal it was started from: copy what it says there.
            {/if}
          </p>
          <p class="muted">
            The report says: {report.app} {report.version}, {report.installed}, on {report.system}, Python
            {report.python}.
          </p>
          <div class="opens">
            <a class="btn" href={report.issue_url} target="_blank" rel="noopener">Report a problem</a>
          </div>
        {:else}
          <div class="opens">
            <a class="btn" href="https://github.com/raby/station-studio/issues/new" target="_blank" rel="noopener"
              >Report a problem</a
            >
          </div>
        {/if}
      {/if}
      {#if t.opens}
        <div class="opens">
          {#each t.opens as o}
            {#if o.external}
              <a class="btn" href={o.href} target="_blank" rel="noopener">{o.label}</a>
            {:else}
              <a class="btn" href={o.href} data-sveltekit-reload>{o.label}</a>
            {/if}
          {/each}
        </div>
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
  .path {
    overflow-wrap: anywhere;
    user-select: all;
  }
  .muted {
    color: var(--muted);
  }
  .opens {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }
  .btn {
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    color: var(--text);
    padding: 6px 13px;
    text-decoration: none;
  }
  .btn:hover {
    border-color: var(--accent);
    color: var(--accent);
  }
  .btn:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
