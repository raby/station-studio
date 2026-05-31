<script lang="ts">
  /**
   * §16.25 c2 — /sources page (single-pool add form by default).
   *
   * The canonical home for source-pool management (D3): lists every
   * imported pool with role + track count, and carries the add-pool
   * form. The §16.13 D1 gate-unblocker — a `pipx`- or docker-installed
   * user can load their iTunes XML from here without touching a
   * Makefile or the CLI manifest.
   *
   * c2 ships: list + single-pool form (file upload primary, advanced
   * typed-path secondary, inline iTunes-XML export hint).
   * c3 adds:  multi-pool batch expansion.
   * c4 adds:  per-row Re-import + Delete.
   * c5 adds:  embed on /stations/[slug] + §16.19 deep-link prefill.
   */
  import { onMount } from 'svelte';
  import {
    ApiError,
    importSourceByPath,
    listSources,
    uploadSource,
    type Source,
  } from '$lib/api';

  let sources: Source[] = [];
  let listError: string | null = null;
  let loading = true;

  // Add-pool form state.
  let name = '';
  let role: 'content' | 'reference' = 'content';
  // D1 — upload is the headline path; path-input is the advanced /
  // local-file option for pipx users with the XML already on disk.
  let mode: 'upload' | 'path' = 'upload';
  let fileList: FileList | null = null;
  // c2-review 🟡-1: `bind:files` is one-way (DOM → JS), so setting
  // `fileList = null` doesn't clear the underlying `<input type=file>`.
  // We keep a direct element ref and clear `.value` on it — scope-local
  // and robust to the conditional-render collapse in path mode
  // (`fileInputEl` just stays null, the optional-chain no-ops).
  let fileInputEl: HTMLInputElement | null = null;
  let path = '';

  let submitting = false;
  let formError: string | null = null;
  // Per-submit banner — clears on the next submit start, so the user
  // never sees a stale success message after a failed second attempt.
  let successMessage: string | null = null;

  onMount(async () => {
    await refresh();
  });

  async function refresh() {
    loading = true;
    listError = null;
    try {
      sources = await listSources();
    } catch (e) {
      listError = e instanceof ApiError ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function submit() {
    formError = null;
    successMessage = null;
    submitting = true;
    try {
      // Client-side first-pass validation (the server enforces the
      // same — see §16.25 c1's `_validate_name` — but rejecting
      // here avoids a round-trip for the obvious empty case).
      const trimmedName = name.trim();
      if (!trimmedName) {
        formError = 'Pool name is required.';
        return;
      }

      let summary;
      if (mode === 'upload') {
        const file = fileList?.[0];
        if (!file) {
          formError = 'Pick an iTunes XML file to upload.';
          return;
        }
        summary = await uploadSource(trimmedName, role, file);
      } else {
        const trimmedPath = path.trim();
        if (!trimmedPath) {
          formError = 'Type the server-side path to the XML.';
          return;
        }
        summary = await importSourceByPath(trimmedName, role, trimmedPath);
      }

      successMessage = `Imported "${summary.name}" — ${summary.tracks_imported} track${
        summary.tracks_imported === 1 ? '' : 's'
      } (${summary.role}).`;
      // Reset only the per-submit fields — keep role + mode so
      // adding "another from the same source" is fast. The DOM
      // file-input clear goes through the bound ref (c2-review 🟡-1):
      // `bind:files` is one-way, so we MUST clear `.value` on the
      // element directly. `fileInputEl` is null when we're in path
      // mode (the `{#if}` collapsed it out) — optional-chain handles
      // that as a valid no-op.
      name = '';
      fileList = null;
      path = '';
      if (fileInputEl) fileInputEl.value = '';

      await refresh();
    } catch (e) {
      formError = e instanceof ApiError ? e.message : String(e);
    } finally {
      submitting = false;
    }
  }
</script>

<svelte:head>
  <title>Sources — Station Studio</title>
</svelte:head>

<main>
  <p class="back"><a href="/">← Stations</a></p>
  <h1>Source pools</h1>
  <p class="sub">
    Import iTunes XMLs as named pools. Stations reference pools by
    name (declared in their template); this page is where the actual
    track data lives.
  </p>

  <!-- ===== Add-pool form ===== -->
  <section class="add-section">
    <h2>Add a pool</h2>

    <form class="add-pool-form" data-testid="add-pool-form" on:submit|preventDefault={submit}>
      <div class="field">
        <label for="pool-name">Pool name</label>
        <input
          id="pool-name"
          type="text"
          data-testid="name-input"
          placeholder="e.g. DAYTIME, JINGLES, WEEKEND"
          bind:value={name}
          disabled={submitting}
          required
        />
        <p class="hint">
          Must match the name your station template references (case-sensitive).
        </p>
      </div>

      <div class="field">
        <label for="pool-role">Role</label>
        <select
          id="pool-role"
          data-testid="role-select"
          bind:value={role}
          disabled={submitting}
        >
          <option value="content">content — engine samples from this pool</option>
          <option value="reference">
            reference — imported for cross-reference only (e.g. MASTER)
          </option>
        </select>
      </div>

      <!-- D1 mode toggle: upload is the headline; path is the
           advanced/local-file option. Buttons (not <a>) so they're
           keyboard-operable without page navigation. -->
      <div class="mode-toggle" role="tablist" aria-label="How to provide the XML">
        <button
          type="button"
          role="tab"
          class="mode-tab"
          class:active={mode === 'upload'}
          aria-selected={mode === 'upload'}
          data-testid="mode-toggle-upload"
          on:click={() => (mode = 'upload')}
        >
          Upload file
        </button>
        <button
          type="button"
          role="tab"
          class="mode-tab"
          class:active={mode === 'path'}
          aria-selected={mode === 'path'}
          data-testid="mode-toggle-path"
          on:click={() => (mode = 'path')}
        >
          Advanced: server path
        </button>
      </div>

      {#if mode === 'upload'}
        <div class="field">
          <label for="pool-file">iTunes XML</label>
          <input
            id="pool-file"
            type="file"
            accept=".xml,application/xml,text/xml"
            data-testid="file-input"
            bind:this={fileInputEl}
            bind:files={fileList}
            disabled={submitting}
          />
          <p class="hint" data-testid="itunes-hint">
            In <b>Music (macOS)</b>: File → Library → Export Playlist → choose
            "XML" format. (In iTunes on Windows: same path.)
          </p>
        </div>
      {:else}
        <div class="field">
          <label for="pool-path">Server-side path</label>
          <input
            id="pool-path"
            type="text"
            data-testid="path-input"
            placeholder="/Users/you/Music/exports/daytime.xml"
            bind:value={path}
            disabled={submitting}
          />
          <p class="hint">
            A typed absolute path to a file the <em>server</em> can read.
            Useful when running locally (<code>pipx</code>) with a large
            library already on disk. Restricted by
            <code>STATION_STUDIO_IMPORT_BASE_DIR</code> if set.
          </p>
        </div>
      {/if}

      {#if formError}
        <p class="error" data-testid="form-error">{formError}</p>
      {/if}
      {#if successMessage}
        <p class="success" data-testid="success-banner">{successMessage}</p>
      {/if}

      <button
        type="submit"
        class="button primary"
        data-testid="submit-button"
        disabled={submitting}
      >
        {submitting ? 'Importing…' : 'Import'}
      </button>
    </form>
  </section>

  <!-- ===== Imported pools list ===== -->
  <section class="list-section">
    <h2>Imported pools</h2>

    {#if loading}
      <p>Loading…</p>
    {:else if listError}
      <p class="error" data-testid="list-error">Could not load pools: {listError}</p>
    {:else if sources.length === 0}
      <p data-testid="empty-state">
        No pools imported yet. Use the form above to import your first one.
      </p>
    {:else}
      <table class="sources-list" data-testid="sources-list">
        <thead>
          <tr>
            <th>Name</th>
            <th>Role</th>
            <th class="num">Tracks</th>
          </tr>
        </thead>
        <tbody>
          {#each sources as s (s.id)}
            <tr data-testid="pool-row" data-pool-name={s.name}>
              <td class="name">{s.name}</td>
              <td>
                <span class="role-tag role-{s.role}">{s.role}</span>
              </td>
              <td class="num">{s.track_count.toLocaleString()}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</main>

<style>
  main {
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem 1rem;
  }
  .back {
    margin-bottom: 1rem;
  }
  h1 {
    margin: 0 0 0.5rem;
  }
  .sub {
    color: #666;
    margin-bottom: 2rem;
  }
  h2 {
    margin: 0 0 1rem;
    font-size: 1.2rem;
  }

  .add-section {
    background: #f7f7f8;
    border: 1px solid #e4e4e7;
    border-radius: 6px;
    padding: 1.25rem;
    margin-bottom: 2rem;
  }
  .add-pool-form {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }
  .field label {
    font-weight: 600;
    font-size: 0.9rem;
  }
  .field input[type='text'],
  .field select {
    padding: 0.5rem 0.6rem;
    border: 1px solid #c4c4c8;
    border-radius: 4px;
    font: inherit;
  }
  .field input[type='file'] {
    font: inherit;
  }
  .hint {
    color: #666;
    font-size: 0.85rem;
    margin: 0;
  }
  .mode-toggle {
    display: flex;
    gap: 0.5rem;
    border-bottom: 1px solid #d4d4d8;
    padding-bottom: 0.5rem;
  }
  .mode-tab {
    background: transparent;
    border: 1px solid transparent;
    padding: 0.4rem 0.8rem;
    border-radius: 4px 4px 0 0;
    cursor: pointer;
    font: inherit;
    color: #666;
  }
  .mode-tab.active {
    color: #111;
    border-color: #d4d4d8;
    border-bottom-color: #f7f7f8;
    background: #f7f7f8;
    margin-bottom: -1px;
  }

  .button {
    padding: 0.5rem 1rem;
    border: 1px solid #c4c4c8;
    border-radius: 4px;
    background: #fff;
    cursor: pointer;
    font: inherit;
  }
  .button.primary {
    background: #2563eb;
    color: #fff;
    border-color: #2563eb;
  }
  .button.primary:disabled {
    background: #9ab3e8;
    border-color: #9ab3e8;
    cursor: wait;
  }
  .button.primary:hover:not(:disabled) {
    background: #1d4fd1;
  }

  .error {
    color: #b91c1c;
    background: #fef2f2;
    border: 1px solid #fecaca;
    padding: 0.5rem 0.75rem;
    border-radius: 4px;
    margin: 0;
  }
  .success {
    color: #166534;
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
    padding: 0.5rem 0.75rem;
    border-radius: 4px;
    margin: 0;
  }

  .sources-list {
    width: 100%;
    border-collapse: collapse;
  }
  .sources-list th,
  .sources-list td {
    text-align: left;
    padding: 0.55rem 0.6rem;
    border-bottom: 1px solid #eee;
  }
  .sources-list .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .sources-list .name {
    font-weight: 500;
  }
  .role-tag {
    display: inline-block;
    padding: 0.1rem 0.55rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 500;
  }
  .role-content {
    background: #dbeafe;
    color: #1e40af;
  }
  .role-reference {
    background: #fef3c7;
    color: #92400e;
  }
</style>
