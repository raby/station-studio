<script lang="ts">
  /**
   * The station's page › On Station Server: which Station Server station
   * this one sends its weeks to, whether Export to disk sends them too,
   * and what that station is doing: on air or not, the week playing, the
   * weeks it holds, and a warning when it has nothing after this week
   * (it would play this week again). Quiet until Settings › Station
   * Server is filled in.
   */
  import { onMount } from 'svelte';
  import {
    errorText,
    getStationServerSettings,
    getStationServerStatus,
    sendToStationServer,
    setStationServerTarget,
    type PlaylistSummary,
    type StationServerSent,
    type StationServerStatus,
    type StationServerTarget,
  } from '$lib/api';
  import { sentText, shortTime as when, weekNo } from '$lib/stationServer';

  export let slug: string;
  export let name: string;
  export let playlists: PlaylistSummary[] = [];
  export let fetchFn: typeof fetch = (...args) => fetch(...args);

  let target: StationServerTarget = { station: '', auto: false };
  let status: StationServerStatus | null = null;
  let loading = true;
  let busy = false;
  let error: string | null = null;
  let sent: StationServerSent | null = null;

  onMount(refresh);

  async function refresh(): Promise<void> {
    error = null;
    try {
      const [settings, got] = await Promise.all([
        getStationServerSettings(fetchFn),
        getStationServerStatus(fetchFn),
      ]);
      target = settings.stations?.[slug] ?? { station: '', auto: false };
      status = got;
    } catch (e) {
      error = errorText(e);
    } finally {
      loading = false;
    }
  }

  async function choose(next: StationServerTarget): Promise<void> {
    busy = true;
    error = null;
    try {
      const settings = await setStationServerTarget(slug, next, fetchFn);
      target = settings.stations[slug] ?? { station: '', auto: false };
    } catch (e) {
      error = errorText(e);
      target = { ...target }; // put the choice that didn't take back on the page
    } finally {
      busy = false;
    }
  }

  async function send(playlist: PlaylistSummary): Promise<void> {
    busy = true;
    error = null;
    sent = null;
    try {
      sent = await sendToStationServer(playlist.id, fetchFn);
      status = await getStationServerStatus(fetchFn);
    } catch (e) {
      error = errorText(e);
    } finally {
      busy = false;
    }
  }

  const STATES: Record<string, string> = {
    on: 'On air',
    off: 'Off air',
    starting: 'Going on air',
    waiting: 'Trying to reach the stream server again',
    stopping: 'Going off air',
    error: 'Off air: stopped by a problem',
  };
  $: there = status?.stations.find((s) => s.id === target.station) ?? null;
  $: onWeek = weekNo(there?.week);
  $: held = (there?.weeks ?? []).map(weekNo).filter((n): n is number => n !== null);
  // The week Station Server needs next: the one after the week on air,
  // or (holding none) Studio's newest.
  $: wanted = there && !there.next && !there.problem
    ? onWeek !== null
      ? onWeek + 1
      : playlists.length
        ? Math.max(...playlists.map((p) => p.week))
        : null
    : null;
  $: wantedPlaylist = wanted !== null ? (playlists.find((p) => p.week === wanted) ?? null) : null;
  $: stateText = there
    ? there.live
      ? `Live: ${there.live}`
      : there.state === 'on' && there.rehearsal
        ? 'Rehearsing (not sent to listeners)'
        : (STATES[there.state] ?? there.state)
    : '';
</script>

{#if !loading && status?.configured}
  <section class="card" aria-labelledby="ss-heading" data-testid="station-server-panel">
    <h2 id="ss-heading">On Station Server</h2>

    {#if !status.connected}
      <p class="error" role="alert" data-testid="ss-unreachable">{status.error}</p>
      <button type="button" on:click={refresh} disabled={busy}>Try again</button>
    {:else}
      <div class="row">
        <label>
          {name} sends to
          <select
            data-testid="ss-target"
            value={target.station}
            disabled={busy}
            on:change={(e) => choose({ station: e.currentTarget.value, auto: target.auto })}
          >
            <option value="">Nothing (not sent)</option>
            {#each status.stations as s}
              <option value={s.id}>{s.name}</option>
            {/each}
            {#if target.station && !there}
              <option value={target.station}>(a station Station Server no longer has)</option>
            {/if}
          </select>
        </label>
        {#if target.station}
          <label class="check">
            <input
              type="checkbox"
              data-testid="ss-auto"
              checked={target.auto}
              disabled={busy}
              on:change={(e) => choose({ station: target.station, auto: e.currentTarget.checked })}
            />
            Also send each week when I export it to disk
          </label>
        {/if}
      </div>

      {#if there}
        <dl class="facts" data-testid="ss-facts">
          <dt>Now</dt>
          <dd>
            <strong>{stateText}</strong>
            {#if there.now?.title}
              · {there.now.title}{there.now.artist ? ` by ${there.now.artist}` : ''}
            {/if}
          </dd>
          <dt>Week playing</dt>
          <dd>{onWeek ?? '—'}</dd>
          <dt>Next</dt>
          <dd>
            {#if there.next}
              Week {weekNo(there.next.week)}, from {when(there.next.at)}
            {:else}
              —
            {/if}
          </dd>
          <dt>Weeks it has</dt>
          <dd>{held.length ? held.join(', ') : 'None yet'}</dd>
        </dl>
        <button type="button" class="small" on:click={refresh} disabled={busy} data-testid="ss-refresh">Refresh</button>

        {#if there.problem}
          <p class="warn" role="alert" data-testid="ss-problem">{there.problem}</p>
        {:else if wanted !== null}
          <p class="warn" data-testid="ss-missing">
            {#if onWeek !== null}
              Station Server has nothing after week {onWeek}, so when this week ends it plays week {onWeek} again.
            {:else}
              Station Server has no weeks for {there.name} yet.
            {/if}
            {#if wantedPlaylist}
              <button type="button" class="primary" disabled={busy} on:click={() => wantedPlaylist && send(wantedPlaylist)} data-testid="ss-send-missing">
                {busy ? 'Sending…' : `Send week ${wanted} now`}
              </button>
            {:else}
              Generate week {wanted} here and send it.
            {/if}
          </p>
        {/if}
      {:else if target.station}
        <p class="warn" data-testid="ss-gone">
          Station Server no longer has the station this one sent to. Choose another.
        </p>
      {/if}
    {/if}

    {#if sent}
      <p class="ok" role="status" data-testid="ss-sent">{sentText(sent)}</p>
    {/if}
    {#if error}
      <p class="error" role="alert" data-testid="ss-error">{error}</p>
    {/if}
  </section>
{/if}

<style>
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin: 18px 0;
    padding: 16px 20px;
  }
  h2 {
    font-size: 18px;
    margin: 0 0 12px;
  }
  .row {
    align-items: flex-end;
    display: flex;
    flex-wrap: wrap;
    gap: 10px 20px;
  }
  .row label {
    display: flex;
    flex-direction: column;
    font-weight: 600;
    gap: 4px;
  }
  .row label.check {
    align-items: center;
    flex-direction: row;
    font-weight: 400;
  }
  .facts {
    display: grid;
    gap: 4px 14px;
    grid-template-columns: max-content 1fr;
    margin: 14px 0 0;
  }
  .facts dt {
    color: var(--muted);
  }
  .facts dd {
    margin: 0;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  button.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--accent-text);
    font-weight: 650;
    margin-left: 8px;
  }
  button.small {
    font-size: 13px;
    margin-top: 10px;
    padding: 3px 10px;
  }
  .warn {
    border-left: 3px solid var(--warn);
    margin: 14px 0 0;
    padding: 4px 0 4px 10px;
  }
  .ok {
    color: var(--ok);
    margin: 12px 0 0;
  }
  .error {
    color: var(--danger);
    margin: 12px 0 8px;
  }
</style>
