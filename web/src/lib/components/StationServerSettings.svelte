<script lang="ts">
  /**
   * Settings › Station Server: where Station Server is and its link code,
   * so weeks go straight to the station's computer (no shared folder).
   * Station Server shows both under its own Settings › Station Studio.
   * Test asks it for its stations. Which station each Studio station
   * feeds is chosen on the station's page (StationServerPanel).
   */
  import { onMount } from 'svelte';
  import {
    errorText,
    getStationServerSettings,
    getStationServerStatus,
    saveStationServerSettings,
    type StationServerSettings,
    type StationServerStatus,
  } from '$lib/api';

  export let fetchFn: typeof fetch = (...args) => fetch(...args);

  let settings: StationServerSettings | null = null;
  let address = '';
  let code = '';
  let saving = false;
  let testing = false;
  let status: StationServerStatus | null = null;
  let error: string | null = null;

  onMount(async () => {
    try {
      const got = await getStationServerSettings(fetchFn);
      if (got && typeof got.address === 'string') {
        settings = got;
        address = got.address;
      }
    } catch (e) {
      error = errorText(e);
    }
  });

  async function test(): Promise<void> {
    testing = true;
    try {
      status = await getStationServerStatus(fetchFn);
    } catch (e) {
      status = null;
      error = errorText(e);
    } finally {
      testing = false;
    }
  }

  async function save(): Promise<void> {
    saving = true;
    error = null;
    status = null;
    try {
      const change: { address: string; code?: string } = { address };
      if (code.trim()) change.code = code;
      settings = await saveStationServerSettings(change, fetchFn);
      address = settings.address;
      code = '';
      if (settings.address && settings.has_code) await test();
    } catch (e) {
      error = errorText(e);
    } finally {
      saving = false;
    }
  }

  const stateLabel = (s: StationServerStatus['stations'][number]): string =>
    s.live ? `live: ${s.live}` : s.state === 'on' ? (s.rehearsal ? 'rehearsing' : 'on air') : s.state === 'off' ? 'off air' : s.state;
</script>

<section class="card" aria-labelledby="station-server-heading" data-testid="station-server-settings">
  <h2 id="station-server-heading">Station Server</h2>
  <p class="help first">
    Send each week straight to the computer that plays the station, without a shared folder. In Station Server
    (on that computer), open Settings › Station Studio, turn on <em>Let Station Studio send weeks to this
    computer</em>, and enter the address and link code it shows here. Then choose which station each of yours
    sends to, on its page.
  </p>

  <form class="fields" on:submit|preventDefault={save}>
    <label>
      Address
      <input
        type="text"
        bind:value={address}
        placeholder="192.168.1.20:8772"
        autocomplete="off"
        spellcheck="false"
        data-testid="station-server-address"
      />
    </label>
    <label>
      Link code
      <input
        type="text"
        bind:value={code}
        placeholder={settings?.has_code ? 'Kept (type to change)' : 'k7m2-9qxr-4hpt'}
        autocomplete="off"
        spellcheck="false"
        data-testid="station-server-code"
      />
    </label>
    <div class="actions">
      <button type="submit" class="primary" disabled={saving} data-testid="station-server-save">
        {saving ? 'Saving…' : 'Save'}
      </button>
      {#if settings?.address && settings?.has_code}
        <button type="button" on:click={test} disabled={testing || saving} data-testid="station-server-test">
          {testing ? 'Asking…' : 'Test'}
        </button>
      {/if}
    </div>
  </form>

  {#if status}
    {#if status.connected}
      <div class="ok-box" role="status" data-testid="station-server-connected">
        <strong>Connected to {status.server ?? 'Station Server'} {status.version ?? ''}.</strong>
        {#if status.stations.length}
          Its stations:
          <ul>
            {#each status.stations as s}
              <li>{s.name} <span class="muted">({stateLabel(s)})</span></li>
            {/each}
          </ul>
        {:else}
          It has no stations yet: add one in Station Server first.
        {/if}
      </div>
    {:else if status.error}
      <p class="error" role="alert" data-testid="station-server-error">{status.error}</p>
    {/if}
  {/if}
  {#if error}
    <p class="error" role="alert" data-testid="station-server-error">{error}</p>
  {/if}
  <p class="help">
    The address is Station Server's computer on your network (or over a VPN such as Tailscale), with the link port,
    8772 unless it was changed. The link code stops anyone else sending it playlists; Station Server can make a new
    one, and then it's typed in here again.
  </p>
</section>

<style>
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-top: 18px;
    padding: 18px 20px;
  }
  h2 {
    font-size: 18px;
    margin: 0 0 10px;
  }
  .fields {
    display: grid;
    gap: 10px 14px;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    margin-top: 14px;
  }
  .fields label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-weight: 600;
  }
  .fields input {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-weight: 400;
  }
  .actions {
    align-items: flex-end;
    display: flex;
    gap: 8px;
  }
  button.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--accent-text);
    font-weight: 650;
  }
  .help {
    color: var(--muted);
    margin: 14px 0 0;
  }
  .help.first {
    margin-top: 0;
  }
  .ok-box {
    border: 1px solid var(--ok);
    border-radius: 8px;
    margin: 14px 0 0;
    padding: 10px 12px;
  }
  .ok-box ul {
    margin: 6px 0 0;
    padding-left: 20px;
  }
  .muted {
    color: var(--muted);
  }
  .error {
    color: var(--danger);
    margin: 14px 0 0;
  }
</style>
