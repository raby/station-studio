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

  // ---- Add-pool form state (shared between single + batch modes) ----
  //
  // `mode` is GLOBAL across both modes: all rows in a batch share the
  // upload-vs-path choice. Per-row mode would multiply the UX with
  // little real-world payoff (a hobbyist's 6 pools are typically
  // all from disk OR all uploaded, not mixed).
  let role: 'content' | 'reference' = 'content';
  let mode: 'upload' | 'path' = 'upload';

  // ---- Single-pool form state (c2) ----
  let name = '';
  let fileList: FileList | null = null;
  // c2-review 🟡-1: `bind:files` is one-way (DOM → JS), so setting
  // `fileList = null` doesn't clear the underlying `<input type=file>`.
  // We keep a direct element ref and clear `.value` on it — scope-local
  // and robust to the conditional-render collapse in path mode
  // (`fileInputEl` just stays null, the optional-chain no-ops).
  let fileInputEl: HTMLInputElement | null = null;
  let path = '';

  // ---- Batch (multi-pool) form state (c3) ----
  // Toggle off → single-pool form (c2 UX, unchanged). Toggle on →
  // a list of `BatchRow`s with per-row status. Per-row state is
  // surfaced in the DOM (`data-row-status` + the visible badge) so
  // the user sees `pending → uploading → imported | failed` as the
  // sequential submit walks the list (G3 — no parallel uploads).
  //
  // Sequential submit is enforced by `for...of await` in `submitBatch`;
  // there is no Promise.all path. Justified by §16.11's single-worker
  // backend constraint + the 100 MB-library memory ceiling.
  interface BatchRow {
    id: string;
    name: string;
    role: 'content' | 'reference';
    file: File | null;
    path: string;
    status: 'pending' | 'uploading' | 'imported' | 'failed';
    error?: string;
  }
  let batchMode = false;
  let rows: BatchRow[] = [];
  let nextRowId = 0;

  function makeEmptyRow(): BatchRow {
    return {
      id: String(nextRowId++),
      name: '',
      // First row inherits the global `role` so toggling into batch
      // mode from a half-filled single form (or just having clicked
      // "reference" earlier) preserves the user's intent for the
      // initial row. Subsequent rows added via "+ Add row" also
      // inherit the current global role.
      role: role,
      file: null,
      path: '',
      status: 'pending',
    };
  }

  function enterBatchMode() {
    rows = [makeEmptyRow()];
    batchMode = true;
    formError = null;
    successMessage = null;
  }

  function exitBatchMode() {
    rows = [];
    batchMode = false;
    formError = null;
    successMessage = null;
  }

  function addRow() {
    rows = [...rows, makeEmptyRow()];
  }

  function removeRow(id: string) {
    const filtered = rows.filter((r) => r.id !== id);
    // Never let the form become row-less — the user can exit batch
    // mode via the dedicated button. Re-seed with one empty row.
    rows = filtered.length === 0 ? [makeEmptyRow()] : filtered;
  }

  // Per-row file-input change handler. Extracted because Svelte's
  // template parser doesn't allow `as` TS casts inside inline arrow
  // functions in event handlers — this gives the cast its own scope.
  function onRowFileChange(row: BatchRow, event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    row.file = input.files?.[0] ?? null;
    rows = rows;
  }

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

  // ---- Batch submit (c3) ----
  //
  // Two-phase: validate all rows up front (mark invalid ones as
  // `failed` without an API call), then walk the survivors
  // sequentially, flipping each through `uploading → imported|failed`.
  // After all rows are processed: drop the succeeded ones, keep the
  // failed for retry, write a summary banner.
  function validateBatchRow(row: BatchRow): string | null {
    const trimmedName = row.name.trim();
    if (!trimmedName) return 'Pool name is required.';
    if (mode === 'upload' && !row.file) {
      return 'Pick an iTunes XML file to upload.';
    }
    if (mode === 'path' && !row.path.trim()) {
      return 'Type the server-side path to the XML.';
    }
    return null;
  }

  async function submitBatch() {
    formError = null;
    successMessage = null;
    submitting = true;

    let successes = 0;
    let failures = 0;

    try {
      // Phase 1: validation. Mark invalid rows as failed; survivors
      // reset to 'pending' (so a retry on a previously-failed row
      // that's now valid starts from a clean slate).
      for (const row of rows) {
        const validationError = validateBatchRow(row);
        if (validationError) {
          row.status = 'failed';
          row.error = validationError;
          failures++;
        } else {
          row.status = 'pending';
          row.error = undefined;
        }
      }
      rows = rows;

      // Phase 2: sequential submit. G3 — no parallel uploads;
      // walks one row at a time, awaiting each before moving on.
      // Per-row try/catch isolates failures so one bad XML doesn't
      // poison the rest of the batch (D2 partial-success contract).
      for (const row of rows) {
        if (row.status === 'failed') continue; // skip pre-flight failures
        row.status = 'uploading';
        rows = rows;
        try {
          if (mode === 'upload') {
            await uploadSource(row.name.trim(), row.role, row.file!);
          } else {
            await importSourceByPath(row.name.trim(), row.role, row.path.trim());
          }
          row.status = 'imported';
          successes++;
        } catch (e) {
          row.status = 'failed';
          row.error = e instanceof ApiError ? e.message : String(e);
          failures++;
        }
        rows = rows;
      }

      // Phase 3: summary + cleanup. Drop succeeded rows; keep failed
      // for retry. If everything succeeded, re-seed with one empty
      // row so the form stays usable for the next batch.
      const total = successes + failures;
      if (failures === 0) {
        successMessage = `Imported all ${successes} pool${successes === 1 ? '' : 's'}.`;
        rows = [makeEmptyRow()];
      } else {
        formError =
          successes > 0
            ? `Imported ${successes} of ${total}. ${failures} failed — see row errors below.`
            : `All ${failures} import${failures === 1 ? '' : 's'} failed — see row errors below.`;
        rows = rows.filter((r) => r.status !== 'imported');
      }

      await refresh();
    } catch (e) {
      // c3 mid-slice review 🟡-1: defensive parity with submit(). The
      // per-row API errors are caught inside Phase 2's inner try/catch
      // (that's how partial-success works). This OUTER catch handles
      // unexpected throws from Phase 1 (validation), Phase 3 (summary),
      // or refresh() — none of which today have a reachable failure
      // path, but a future refactor could introduce one. Without this,
      // the error would escape silently: `finally` would clear
      // `submitting` but the user would see no feedback at all.
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
    <header class="section-row">
      <h2>{batchMode ? 'Add multiple pools' : 'Add a pool'}</h2>
      {#if batchMode}
        <button
          type="button"
          class="link-button"
          data-testid="single-mode-toggle"
          on:click={exitBatchMode}
          disabled={submitting}
        >
          ← Back to single pool
        </button>
      {:else}
        <button
          type="button"
          class="link-button"
          data-testid="batch-mode-toggle"
          on:click={enterBatchMode}
          disabled={submitting}
        >
          + Add multiple at once
        </button>
      {/if}
    </header>

    <!-- Mode toggle (upload vs path) sits ABOVE the form body so it
         applies uniformly in both single + batch modes. -->
    <div class="mode-toggle" role="tablist" aria-label="How to provide the XML">
      <button
        type="button"
        role="tab"
        class="mode-tab"
        class:active={mode === 'upload'}
        aria-selected={mode === 'upload'}
        data-testid="mode-toggle-upload"
        on:click={() => (mode = 'upload')}
        disabled={submitting}
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
        disabled={submitting}
      >
        Advanced: server path
      </button>
    </div>

    {#if batchMode}
      <!-- ===== Batch mode (c3 — D2 multi-pool) ===== -->
      <form
        class="batch-form"
        data-testid="batch-form"
        on:submit|preventDefault={submitBatch}
      >
        <ul class="batch-rows" data-testid="batch-rows">
          {#each rows as row (row.id)}
            <li
              class="batch-row"
              data-testid="batch-row"
              data-row-id={row.id}
              data-row-status={row.status}
            >
              <div class="batch-row-fields">
                <input
                  type="text"
                  class="batch-name"
                  placeholder="Pool name"
                  data-testid="batch-row-name"
                  bind:value={row.name}
                  disabled={submitting}
                />
                <select
                  class="batch-role"
                  data-testid="batch-row-role"
                  bind:value={row.role}
                  disabled={submitting}
                >
                  <option value="content">content</option>
                  <option value="reference">reference</option>
                </select>
                {#if mode === 'upload'}
                  <input
                    type="file"
                    accept=".xml,application/xml,text/xml"
                    class="batch-file"
                    data-testid="batch-row-file"
                    on:change={(e) => onRowFileChange(row, e)}
                    disabled={submitting}
                  />
                {:else}
                  <input
                    type="text"
                    class="batch-path"
                    placeholder="/path/to/library.xml"
                    data-testid="batch-row-path"
                    bind:value={row.path}
                    disabled={submitting}
                  />
                {/if}
                <span
                  class="status-badge status-{row.status}"
                  data-testid="batch-row-status"
                >
                  {row.status}
                </span>
                <button
                  type="button"
                  class="remove-row"
                  data-testid="batch-row-remove"
                  aria-label="Remove row"
                  on:click={() => removeRow(row.id)}
                  disabled={submitting}
                >
                  ×
                </button>
              </div>
              {#if row.error}
                <p class="row-error" data-testid="batch-row-error">{row.error}</p>
              {/if}
            </li>
          {/each}
        </ul>

        <button
          type="button"
          class="button"
          data-testid="batch-add-row"
          on:click={addRow}
          disabled={submitting}
        >
          + Add another row
        </button>

        {#if formError}
          <p class="error" data-testid="form-error">{formError}</p>
        {/if}
        {#if successMessage}
          <p class="success" data-testid="success-banner">{successMessage}</p>
        {/if}

        <button
          type="submit"
          class="button primary"
          data-testid="batch-submit"
          disabled={submitting || rows.length === 0}
        >
          {submitting
            ? `Importing… (${rows.filter((r) => r.status === 'imported').length}/${rows.length})`
            : `Import ${rows.length} pool${rows.length === 1 ? '' : 's'}`}
        </button>
      </form>
    {:else}
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
    {/if}
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

  /* ===== c3 batch UI ===== */
  .section-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 1rem;
  }
  .section-row h2 {
    margin: 0;
  }
  .link-button {
    background: none;
    border: none;
    padding: 0;
    color: #2563eb;
    cursor: pointer;
    font: inherit;
    font-size: 0.9rem;
    text-decoration: underline;
  }
  .link-button:disabled {
    color: #9ab3e8;
    cursor: wait;
  }

  .batch-form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .batch-rows {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .batch-row {
    border: 1px solid #e4e4e7;
    border-radius: 4px;
    padding: 0.5rem;
    background: #fff;
  }
  .batch-row[data-row-status='imported'] {
    background: #f0fdf4;
    border-color: #bbf7d0;
  }
  .batch-row[data-row-status='failed'] {
    background: #fef2f2;
    border-color: #fecaca;
  }
  .batch-row[data-row-status='uploading'] {
    background: #fef3c7;
    border-color: #fde68a;
  }
  .batch-row-fields {
    display: grid;
    grid-template-columns: 2fr 1.2fr 2fr auto auto;
    gap: 0.5rem;
    align-items: center;
  }
  .batch-row-fields input[type='text'],
  .batch-row-fields select {
    padding: 0.35rem 0.5rem;
    border: 1px solid #c4c4c8;
    border-radius: 4px;
    font: inherit;
  }
  .batch-row-fields input[type='file'] {
    font-size: 0.85rem;
  }
  .status-badge {
    font-size: 0.78rem;
    font-weight: 500;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    text-align: center;
    text-transform: lowercase;
  }
  .status-pending {
    background: #f3f4f6;
    color: #4b5563;
  }
  .status-uploading {
    background: #fde68a;
    color: #92400e;
  }
  .status-imported {
    background: #bbf7d0;
    color: #166534;
  }
  .status-failed {
    background: #fecaca;
    color: #991b1b;
  }
  .remove-row {
    background: transparent;
    border: 1px solid #d4d4d8;
    border-radius: 4px;
    padding: 0.1rem 0.5rem;
    cursor: pointer;
    color: #6b7280;
    font-size: 1.1rem;
    line-height: 1;
  }
  .remove-row:hover:not(:disabled) {
    background: #fef2f2;
    color: #b91c1c;
  }
  .row-error {
    margin: 0.4rem 0 0;
    padding-left: 0.25rem;
    color: #991b1b;
    font-size: 0.85rem;
  }
</style>
