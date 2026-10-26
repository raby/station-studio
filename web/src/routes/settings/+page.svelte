<script lang="ts">
  /**
   * Settings › App, as Station Player has it: this version, Check for
   * updates, Start at login, the window of its own, new-version notices,
   * and Install as an app. The app's own routes (station_studio/desktop.py)
   * exist only when Station Studio was opened as an app; under `serve` or
   * Docker the page says so and offers what still applies.
   */
  import { onMount } from 'svelte';
  import {
    changeAppSettings,
    checkNow,
    getAppState,
    type AppState,
    type SettingsChange,
  } from '$lib/desktopApp';
  import { installApp, installOffer } from '$lib/install';
  import { announceAppState, requestUpdate } from '$lib/appEvents';
  import StationServerSettings from '$lib/components/StationServerSettings.svelte';

  export let fetchFn: typeof fetch = (...args) => fetch(...args);

  let state: AppState | null = null;
  let loading = true;
  let error: string | null = null;
  let note: string | null = null;
  let checking = false;
  let checked = false;

  onMount(async () => {
    state = await getAppState(fetchFn);
    loading = false;
  });

  function changed(next: AppState, message: string): void {
    state = next;
    note = message;
    error = null;
    announceAppState(next);
  }

  async function change(key: keyof SettingsChange, box: HTMLInputElement, message: string): Promise<void> {
    const on = box.checked;
    try {
      changed(await changeAppSettings({ [key]: on }, fetchFn), message);
    } catch (e) {
      error = (e as Error).message;
      box.checked = !on; // put back a change that didn't take
    }
  }

  async function check(): Promise<void> {
    checking = true;
    error = null;
    try {
      const next = await checkNow(fetchFn);
      checked = true;
      changed(next, next.latest ? `Station Studio ${next.latest} is out.` : 'You have the newest version.');
    } catch (e) {
      error = (e as Error).message;
    } finally {
      checking = false;
    }
  }

  const where: Record<string, string> = {
    mac: 'Applications folder or Launchpad',
    windows: 'Start menu',
  };
</script>

<svelte:head>
  <title>Settings · Station Studio</title>
</svelte:head>

<main id="main-content" tabindex="-1">
  <header class="view-head">
    <div class="kicker">Station Studio</div>
    <h1>Settings</h1>
  </header>

  <section class="card" aria-labelledby="app-heading">
    <h2 id="app-heading">App</h2>

    {#if loading}
      <p>Loading…</p>
    {:else if state}
      <div class="version-row" data-testid="settings-version">
        <div>
          <strong>Station Studio {state.version}</strong>
          {#if state.latest}
            <span class="newer" data-testid="settings-newer">Station Studio {state.latest} is out.</span>
          {:else if checked}
            <span class="ok" data-testid="settings-newest">You have the newest version.</span>
          {/if}
        </div>
        <div class="actions">
          {#if state.latest && state.can_update}
            <button type="button" class="primary" on:click={() => requestUpdate()} data-testid="settings-update-now">
              Update now
            </button>
          {:else if state.latest && state.release_page}
            <a class="btn" href={state.release_page} target="_blank" rel="noopener">Download it</a>
          {/if}
          {#if state.kind}
            <button type="button" on:click={check} disabled={checking} data-testid="settings-check">
              {checking ? 'Checking…' : 'Check for updates'}
            </button>
          {/if}
        </div>
      </div>

      <div class="checks">
        {#if state.login !== null}
          <label>
            <input
              type="checkbox"
              checked={state.login}
              data-testid="settings-login"
              on:change={(e) =>
                change('login', e.currentTarget, e.currentTarget.checked ? 'Start at login turned on' : 'Start at login turned off')}
            />
            Start Station Studio in the background when I log in
          </label>
        {/if}
        {#if state.browser || state.mac_app}
          <label>
            <input
              type="checkbox"
              checked={state.settings.own_window}
              data-testid="settings-window"
              on:change={(e) =>
                change(
                  'own_window',
                  e.currentTarget,
                  `Own window turned ${e.currentTarget.checked ? 'on' : 'off'}. This applies the next time Station Studio opens.`,
                )}
            />
            {#if state.mac_app}
              Open in Station Studio's own window, not a browser tab
            {:else}
              Open in a window of its own (using {state.browser}), not a browser tab
            {/if}
          </label>
        {/if}
        {#if state.kind}
          <label>
            <input
              type="checkbox"
              checked={state.settings.check_updates}
              data-testid="settings-updates"
              on:change={(e) =>
                change(
                  'check_updates',
                  e.currentTarget,
                  `New version notices turned ${e.currentTarget.checked ? 'on' : 'off'}`,
                )}
            />
            Tell me when a new version is out (asks GitHub once a day)
          </label>
        {/if}
      </div>

      {#if !state.browser && !state.mac_app}
        <p class="help">
          Station Studio opens in a browser tab. With Google Chrome, Microsoft Edge or Brave installed it opens in a
          window of its own instead.
        </p>
      {/if}
      <p class="help">
        Your stations, source pools and playlists are kept in <code>{state.home}</code>, which updating never touches.
        {#if state.kind && where[state.kind]}
          After Quit, open Station Studio again from your {where[state.kind]}.
        {/if}
      </p>
    {:else}
      <p class="help" data-testid="settings-server">
        This Station Studio was started with <code>station-studio serve</code> or Docker, so it updates the way it was
        installed. The downloaded Mac and Windows apps have Update now, Quit and Start at login here.
      </p>
    {/if}

    {#if $installOffer}
      <p class="install" data-testid="settings-install">
        <button type="button" on:click={() => $installOffer && installApp($installOffer)} data-testid="settings-install-button">
          Install Station Studio as an app
        </button>
        <span class="help">so it has its own icon in the Dock (or taskbar), with New station and Source pools on its menu.</span>
      </p>
    {/if}

    {#if note}
      <p class="note" role="status" data-testid="settings-note">{note}</p>
    {/if}
    {#if error}
      <p class="error" role="alert" data-testid="settings-error">{error}</p>
    {/if}
  </section>

  <StationServerSettings {fetchFn} />
</main>

<style>
  main {
    max-width: 780px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .view-head {
    margin-bottom: 18px;
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
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 18px 20px;
  }
  h2 {
    font-size: 18px;
    margin: 0 0 14px;
  }
  .version-row {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 10px 16px;
    justify-content: space-between;
    margin-bottom: 14px;
  }
  .version-row strong {
    margin-right: 8px;
  }
  .newer {
    color: var(--accent);
    font-weight: 600;
  }
  .ok {
    color: var(--ok);
  }
  .actions {
    display: flex;
    gap: 8px;
  }
  button.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--accent-text);
    font-weight: 650;
  }
  button.primary:hover:not(:disabled) {
    background: var(--accent-strong);
    border-color: var(--accent-strong);
  }
  .btn {
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    color: var(--text);
    padding: 6px 13px;
    text-decoration: none;
  }
  .checks {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .checks label {
    align-items: flex-start;
    display: flex;
    gap: 10px;
  }
  .checks input {
    margin-top: 3px;
  }
  .help {
    color: var(--muted);
    margin: 14px 0 0;
  }
  .install {
    align-items: center;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 10px;
    margin: 16px 0 0;
  }
  .install .help {
    margin: 0;
  }
  .note {
    color: var(--ok);
    margin: 14px 0 0;
  }
  .error {
    color: var(--danger);
    margin: 14px 0 0;
  }
</style>
