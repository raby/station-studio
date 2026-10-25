<script lang="ts">
  /**
   * Shown only in the downloaded Station Studio app (or `station-studio
   * app`): the version, a Quit button (there's no window to close), and
   * when a newer version is out, Update now. Under `serve` or Docker
   * /api/app doesn't exist and this renders nothing.
   */
  import { onDestroy, onMount } from 'svelte';
  import { confirmDialog } from '$lib/confirm';
  import { STATE_EVENT, UPDATE_EVENT } from '$lib/appEvents';
  import {
    getAppState,
    quitApp,
    stageText,
    startUpdate,
    waitForRestart,
    type AppState,
  } from '$lib/desktopApp';

  // Injectable for tests.
  export let fetchFn: typeof fetch = (...args) => fetch(...args);
  export let reload: () => void = () => location.reload();
  export let sleep: ((ms: number) => Promise<void>) | undefined = undefined;
  export let closeWindow: () => void = () => window.close();
  // How often to look again, so a new version's notice turns up on a
  // page that's been open a while (the app checks GitHub once a day).
  export let refreshEvery = 5 * 60 * 1000;

  let state: AppState | null = null;
  let error: string | null = null;
  // Not now hides the notice for that version, as in Station Player.
  const DISMISSED = 'station-studio:dismissed-update';
  let dismissed: string | null = remembered();
  let stopped = false;
  let busy = false;
  let poll: ReturnType<typeof setInterval> | null = null;
  let refresh: ReturnType<typeof setInterval> | null = null;

  onMount(() => {
    window.addEventListener(STATE_EVENT, onState);
    window.addEventListener(UPDATE_EVENT, onUpdate);
    return () => {
      window.removeEventListener(STATE_EVENT, onState);
      window.removeEventListener(UPDATE_EVENT, onUpdate);
    };
  });

  onMount(async () => {
    state = await getAppState(fetchFn);
    if (state?.update && state.update.stage !== 'failed') follow();
    if (state) {
      refresh = setInterval(async () => {
        if (poll || stopped || busy) return;
        const next = await getAppState(fetchFn);
        if (next && !poll) state = next;
      }, refreshEvery);
    }
  });

  onDestroy(() => {
    if (poll) clearInterval(poll);
    if (refresh) clearInterval(refresh);
  });

  // From the Settings page (see $lib/appEvents).
  function remembered(): string | null {
    try {
      return localStorage.getItem(DISMISSED);
    } catch {
      return null; // private window: nothing remembered
    }
  }
  function notNow(version: string): void {
    dismissed = version;
    try {
      localStorage.setItem(DISMISSED, version);
    } catch {
      /* forget it next time */
    }
  }

  function onState(e: Event): void {
    const next = (e as CustomEvent<AppState>).detail;
    if (next && !poll) state = next;
  }
  function onUpdate(): void {
    if (!busy && !updating) update();
  }

  function follow(): void {
    if (poll) return;
    poll = setInterval(async () => {
      const next = await getAppState(fetchFn);
      if (next) state = next;
      const stage = (next ?? state)?.update?.stage;
      if (!next || stage === 'restarting' || stage === 'failed') {
        if (poll) clearInterval(poll);
        poll = null;
        if (stage !== 'failed') await restarted();
      }
    }, 1000);
  }

  async function restarted(): Promise<void> {
    const target = state?.update?.version ?? state?.latest;
    if (!target) return;
    const outcome = await waitForRestart(target, { fetchFn, sleep });
    if (outcome === 'updated') {
      reload();
      return;
    }
    error =
      outcome === 'unchanged'
        ? "The update didn't finish, so this is still the old version. Try again, or download it from the release page."
        : 'Station Studio hasn\'t come back after updating. Open it again from Applications or the Start menu.';
    state = (await getAppState(fetchFn)) ?? state;
  }

  async function update(): Promise<void> {
    if (!state?.latest) return;
    error = null;
    busy = true;
    try {
      state = await startUpdate(state.latest, fetchFn);
      follow();
    } catch (e) {
      error = (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function quit(): Promise<void> {
    const ok = await confirmDialog({
      title: 'Quit Station Studio?',
      body: 'Station Studio stops until you open it again. Your stations and playlists are kept.',
      confirmLabel: 'Quit',
    });
    if (!ok) return;
    try {
      await quitApp(fetchFn);
      stopped = true;
      // In Station Studio's own window, close it too (a browser tab
      // won't let a page close it, and keeps the stopped notice).
      try {
        closeWindow();
      } catch {
        /* the notice stays */
      }
    } catch (e) {
      error = (e as Error).message;
    }
  }

  $: updating = !!state?.update && state.update.stage !== 'failed';
  $: failed = state?.update?.stage === 'failed' ? state.update : null;
</script>

{#if stopped}
  <div class="stopped" role="alert" data-testid="app-stopped">
    <h2>Station Studio has stopped</h2>
    <p>Open it again from your Applications folder, Launchpad or the Start menu.</p>
  </div>
{:else if state}
  <div class="app-bar" data-testid="app-bar">
    {#if updating && state.update}
      <span class="notice" role="status" data-testid="app-update-progress">{stageText(state.update)}</span>
    {:else if state.latest && state.can_update && state.latest !== dismissed}
      {@const latest = state.latest}
      <span class="notice" data-testid="app-update-notice">
        Station Studio {latest} is out.
        <span class="row">
          <button type="button" class="primary" on:click={update} disabled={busy} data-testid="app-update-now">Update now</button>
          <button type="button" on:click={() => notNow(latest)} data-testid="app-update-later">Not now</button>
        </span>
        {#if state.release_page}
          <a href={state.release_page} target="_blank" rel="noopener">What's new</a>
        {/if}
      </span>
    {/if}
    {#if failed}
      <span class="error" role="alert" data-testid="app-update-failed">
        {stageText(failed)}
        <a href={failed.page} target="_blank" rel="noopener">Release page</a>
      </span>
    {/if}
    {#if error}
      <span class="error" role="alert" data-testid="app-error">{error}</span>
    {/if}
    <div class="foot">
      <span class="version" title={`Your stations and playlists are kept in ${state.home}`}>
        Station Studio {state.version}
      </span>
      <button type="button" class="quit" on:click={quit} disabled={updating} data-testid="app-quit">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18.36 6.64a9 9 0 1 1-12.73 0" /><path d="M12 2v10" /></svg>
        Quit
      </button>
    </div>
  </div>
{/if}

<style>
  /* Sits at the foot of the sidebar (see +layout.svelte). */
  .app-bar {
    display: flex;
    flex-direction: column;
    font-size: 13px;
    gap: 8px;
  }
  .notice,
  .error {
    border-radius: 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px 12px;
  }
  .notice {
    background: var(--accent-soft);
    border: 1px solid var(--accent);
    color: var(--text);
    font-weight: 600;
  }
  .error {
    background: var(--danger-soft);
    border: 1px solid var(--danger);
    color: var(--danger);
  }
  .notice .row {
    display: flex;
    gap: 6px;
  }
  .notice .row button {
    font-size: 13px;
    padding: 4px 10px;
    white-space: nowrap;
  }
  .notice a,
  .error a {
    font-weight: 500;
  }
  .foot {
    align-items: center;
    display: flex;
    gap: 8px;
    justify-content: space-between;
    padding: 0 4px 0 8px;
  }
  .version {
    color: var(--muted);
  }
  button {
    font-size: 13px;
    padding: 4px 10px;
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
  .quit {
    align-items: center;
    display: inline-flex;
    gap: 5px;
  }
  .quit svg {
    fill: none;
    height: 14px;
    stroke: currentColor;
    stroke-linecap: round;
    stroke-width: 2;
    width: 14px;
  }
  .stopped {
    align-items: center;
    background: var(--bg);
    display: flex;
    flex-direction: column;
    inset: 0;
    justify-content: center;
    padding: 1rem;
    position: fixed;
    text-align: center;
    z-index: 10001;
  }
</style>
