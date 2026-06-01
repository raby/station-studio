<script lang="ts">
  /**
   * <PoolImportPanel> — §16.25 c5 extraction.
   *
   * Owns "list of imported pools + add-pool form (single + batch) +
   * per-row Re-import + Delete." Mounted in two places (D3 — "both"):
   *
   *   1. /sources — global view. No `scope` → list shows every pool.
   *   2. /stations/[slug] — embedded view, scoped to the station's
   *      declared source names. The list filters to those; the form
   *      stays unconstrained (the user can still add any pool — but
   *      typically they came here to fix a "not imported" gap).
   *
   * Props:
   *   scope         — when set, the imported-pools list shows ONLY
   *                   sources whose `name` is in this array. The
   *                   add-form is NOT constrained (no name validation
   *                   against scope; the user can add anything). Pure
   *                   list-view filter.
   *   initialName   — prefill the single-pool form's name. Drives
   *                   the §16.19 deep-link from a station detail's
   *                   "not imported" badge → `/sources?pool=X&role=Y`.
   *   initialRole   — same, for the role select.
   *
   * Initial prop values are applied ONCE at component instantiation;
   * later prop changes do NOT reactively clobber user edits to the
   * form. (You get a fresh prefill by re-mounting the panel.)
   *
   * c5 also folds in queued cleanups from the c3 review:
   *   🟢-3 (full)  — replace the `row.file!` non-null assertion in
   *                  both `submitBatch` and `submitReimport` with an
   *                  explicit runtime check that narrows the type and
   *                  surfaces an honest error if validation drifts.
   *   🟢-4 (partial) — whole-array operations stay immutable
   *                  (`addRow` spreads, `removeRow` filters,
   *                  Phase 3's `.filter(r => r.status !== 'imported')`).
   *                  Per-row status flips stay in-place mutation +
   *                  `rows = rows`. We tried full immutability during
   *                  the extraction and it broke Svelte 4's
   *                  keyed-each `bind:value={row.name}` — replacing
   *                  every row object on every status flip confused
   *                  the binding identity enough that sibling inputs
   *                  lost data. The hybrid is the working contract:
   *                  identity-stable per-row mutation, immutable
   *                  whole-array refresh.
   */
  import { onMount, createEventDispatcher } from 'svelte';
  import {
    ApiError,
    deleteSource,
    importSourceByPath,
    listSources,
    uploadSource,
    type Source,
  } from '$lib/api';
  import { confirmDialog } from '$lib/confirm';

  export let scope: string[] | undefined = undefined;
  export let initialName: string | undefined = undefined;
  export let initialRole: 'content' | 'reference' | undefined = undefined;

  // c5 mid-slice review 🟡-1: fired after any successful list-modifying
  // op (single import, batch import, re-import, delete). Embedding
  // pages listen via `on:changed` to refresh their OWN derived views —
  // the station-detail embed re-fetches `getStationOverview` so the
  // §16.19 "not imported" badge clears in real time. /sources doesn't
  // need to listen (its only derived view is the panel's own list,
  // already refreshed inside `refresh()`).
  const dispatch = createEventDispatcher<{ changed: void }>();

  let sources: Source[] = [];
  let listError: string | null = null;
  let loading = true;

  // Scope filter: pure derived state, only narrows the list view.
  $: visibleSources =
    scope === undefined ? sources : sources.filter((s) => scope!.includes(s.name));

  // ---- Add-pool form state (shared between single + batch modes) ----
  //
  // `mode` is GLOBAL across both modes: all rows in a batch share the
  // upload-vs-path choice. Per-row mode would multiply the UX with
  // little real-world payoff (a hobbyist's 6 pools are typically all
  // from disk OR all uploaded, not mixed).
  let role: 'content' | 'reference' = initialRole ?? 'content';
  let mode: 'upload' | 'path' = 'upload';

  // ---- Single-pool form state (c2) ----
  let name = initialName ?? '';
  let fileList: FileList | null = null;
  // c2-review 🟡-1: `bind:files` is one-way (DOM → JS), so setting
  // `fileList = null` doesn't clear the underlying `<input type=file>`.
  // We keep a direct element ref and clear `.value` on it.
  let fileInputEl: HTMLInputElement | null = null;
  let path = '';

  // ---- Batch (multi-pool) form state (c3) ----
  interface BatchRow {
    id: string;
    name: string;
    role: 'content' | 'reference';
    file: File | null;
    path: string;
    // §16.25.t2: `cancelled` is user-initiated abort (visually
    // distinct from `failed` — different copy, no error message,
    // doesn't suggest "the server rejected this").
    status: 'pending' | 'uploading' | 'imported' | 'failed' | 'cancelled';
    error?: string;
  }
  let batchMode = false;
  let rows: BatchRow[] = [];
  let nextRowId = 0;

  function makeEmptyRow(): BatchRow {
    return {
      id: String(nextRowId++),
      name: '',
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

  // c5 full-slice review F: confirm before discarding non-empty batch
  // rows. "Back to single pool" was a silent discard — if the user had
  // filled 5 rows and clicked once by accident, all work vanished. A
  // row counts as "dirty" if name OR file OR path has content (a fresh
  // row from `makeEmptyRow()` has all three empty).
  // §16.25.t1: in batch mode, switching upload ↔ path with rows
  // already holding content from the OLD mode causes a confusing
  // submit failure (every dormant row fails validation with "type
  // the server-side path" or "pick an iTunes XML file"). The content
  // is preserved across the toggle (recoverable by toggling back),
  // but the user has no signal that it's still there.
  //
  // Fix: confirm the toggle when dormant content exists. The
  // confirmDialog body explains the recovery path ("toggle back to
  // use them") so the user doesn't feel data was lost.
  //
  // Single mode skips this — the user has at most one file/path,
  // can see what they have, and toggles back trivially.
  async function setMode(newMode: 'upload' | 'path') {
    if (newMode === mode) return;
    if (batchMode) {
      const dormantCount = rows.filter((r) =>
        newMode === 'path'
          ? r.file !== null
          : r.path.trim() !== '',
      ).length;
      if (dormantCount > 0) {
        const ok = await confirmDialog({
          title: `Switch to ${newMode === 'upload' ? 'upload' : 'path'} mode?`,
          body:
            `${dormantCount} row${dormantCount === 1 ? '' : 's'} ` +
            `${dormantCount === 1 ? 'has' : 'have'} content from the other ` +
            `mode that will be ignored on submit. (Toggle back to use it.)`,
          confirmLabel: 'Switch',
          // Not `danger: true` — toggling is reversible, no data loss.
        });
        if (!ok) return;
      }
    }
    mode = newMode;
  }

  async function exitBatchMode() {
    const dirtyRows = rows.filter(
      (r) => r.name.trim() !== '' || r.file !== null || r.path.trim() !== '',
    );
    if (dirtyRows.length > 0) {
      const ok = await confirmDialog({
        title: 'Discard unsaved rows?',
        body: `${dirtyRows.length} row${dirtyRows.length === 1 ? '' : 's'} ${dirtyRows.length === 1 ? 'has' : 'have'} content that will be lost.`,
        confirmLabel: 'Discard',
        cancelLabel: 'Keep editing',
        danger: true,
      });
      if (!ok) return;
    }
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
    rows = filtered.length === 0 ? [makeEmptyRow()] : filtered;
  }

  // c3-review 🟢-4 (partial): per-row field handler stays in-place
  // mutation + `rows = rows` rather than immutable `.map(...)`. We
  // tried the all-immutable form during the c5 extraction and it
  // broke Svelte 4's keyed-each bindings — replacing every row object
  // every keystroke confuses `bind:value={row.name}` enough that
  // sibling rows lose state between rapid events. In-place mutation
  // keeps the row identity stable while reactivity propagates via
  // the array re-assignment. The bulk Phase 1/2 mutations in
  // submitBatch DO use the immutable `.map(...)` form (whole-array
  // refresh, not per-keystroke), where Svelte handles it cleanly.
  function onRowFileChange(row: BatchRow, event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    row.file = input.files?.[0] ?? null;
    rows = rows;
  }

  let submitting = false;
  let formError: string | null = null;
  // §16.25.t2: per-submission AbortController. Created at submit
  // start, signalled by the Cancel button, cleared when the submission
  // settles (success / failure / cancel). One controller per submit
  // call — single-mode uses it for the one upload; batch-mode uses
  // the same one for the in-flight row + as the "stop the tail" flag.
  let abortController: AbortController | null = null;
  function cancelSubmission() {
    abortController?.abort();
  }
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
    console.log('[panel] submit() fired', { name, role, mode, hasFile: !!fileList?.[0] });
    formError = null;
    successMessage = null;
    submitting = true;
    abortController = new AbortController();
    try {
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
        summary = await uploadSource(
          trimmedName, role, file, undefined, abortController.signal,
        );
      } else {
        const trimmedPath = path.trim();
        if (!trimmedPath) {
          formError = 'Type the server-side path to the XML.';
          return;
        }
        summary = await importSourceByPath(
          trimmedName, role, trimmedPath, undefined, abortController.signal,
        );
      }

      successMessage = `Imported "${summary.name}" — ${summary.tracks_imported} track${
        summary.tracks_imported === 1 ? '' : 's'
      } (${summary.role}).`;
      name = '';
      fileList = null;
      path = '';
      if (fileInputEl) fileInputEl.value = '';

      await refresh();
      dispatch('changed');
    } catch (e) {
      // §16.25.t2: distinguish user-aborted from server-errored.
      // AbortError → "Upload cancelled." (no scary red error text);
      // anything else flows through the existing ApiError path.
      if (e instanceof Error && e.name === 'AbortError') {
        formError = 'Upload cancelled.';
      } else {
        formError = e instanceof ApiError ? e.message : String(e);
      }
    } finally {
      submitting = false;
      abortController = null;
    }
  }

  // ---- Batch submit ----
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
    abortController = new AbortController();

    let successes = 0;
    let failures = 0;
    let cancelled = 0;

    try {
      // Phase 1: validation. In-place mutation + `rows = rows` keeps
      // row identity stable so Svelte 4's keyed-each bindings survive
      // the bulk update (we briefly tried `.map(...)` here as part of
      // the 🟢-4 unification but it broke `bind:value={row.name}`
      // across replaced objects).
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

      // Phase 2: sequential submit (G3 — no parallel uploads).
      // Per-row mutation + reactivity flush keeps the UI in sync
      // through each `await`. c3-review 🟢-3 is still folded in
      // below: an explicit runtime check narrows the type cleanly
      // instead of the `row.file!` non-null assertion.
      for (const row of rows) {
        if (row.status === 'failed') continue;
        // §16.25.t2: if cancel fired before this row's turn (the
        // abort was while we were mid-await on a prior row), the
        // signal is already aborted — skip the remaining tail
        // without starting a new fetch. The current row gets
        // 'cancelled' too (it never ran). Surfaces in Phase 3
        // as part of the cancelled count.
        if (abortController.signal.aborted) {
          row.status = 'cancelled';
          cancelled++;
          continue;
        }
        row.status = 'uploading';
        rows = rows;
        try {
          if (mode === 'upload') {
            if (!row.file) {
              throw new Error(
                `bug: upload row '${row.name}' reached Phase 2 with no file`,
              );
            }
            await uploadSource(
              row.name.trim(), row.role, row.file,
              undefined, abortController.signal,
            );
          } else {
            await importSourceByPath(
              row.name.trim(),
              row.role,
              row.path.trim(),
              undefined, abortController.signal,
            );
          }
          row.status = 'imported';
          successes++;
        } catch (e) {
          // §16.25.t2: AbortError → cancelled (user wanted to stop);
          // other errors → failed (server / parse / IO).
          if (e instanceof Error && e.name === 'AbortError') {
            row.status = 'cancelled';
            cancelled++;
          } else {
            row.status = 'failed';
            row.error = e instanceof ApiError ? e.message : String(e);
            failures++;
          }
        }
        rows = rows;
      }

      // Phase 3: summary + cleanup. §16.25.t2 added a third outcome
      // (cancelled) on top of imported/failed. Cancelled rows stay
      // visible (like failed) for retry; imported rows drop.
      // Summary banner combines all three counts honestly.
      const total = successes + failures + cancelled;
      if (failures === 0 && cancelled === 0) {
        successMessage = `Imported all ${successes} pool${successes === 1 ? '' : 's'}.`;
        rows = [makeEmptyRow()];
      } else {
        const parts: string[] = [];
        if (successes > 0) parts.push(`Imported ${successes} of ${total}`);
        if (failures > 0) parts.push(`${failures} failed`);
        if (cancelled > 0) parts.push(`${cancelled} cancelled`);
        formError = parts.join('. ') +
          (failures > 0 ? ' — see row errors below.' : '.');
        rows = rows.filter((r) => r.status !== 'imported');
      }

      await refresh();
      // c5-review 🟡-1: only dispatch when something actually landed.
      // A 0-success batch left the underlying pool list unchanged, so
      // the embedding page doesn't need to re-fetch.
      if (successes > 0) dispatch('changed');
    } catch (e) {
      // c3 mid-slice review 🟡-1: defensive parity with submit().
      formError = e instanceof ApiError ? e.message : String(e);
    } finally {
      submitting = false;
      abortController = null;
    }
  }

  // ---- Per-row Re-import (c4) ----
  let reimportingId: number | null = null;
  let reimportFile: File | null = null;
  let reimportPath = '';
  let reimporting = false;
  let reimportError: string | null = null;
  let reimportSuccessMessage: string | null = null;

  function startReimport(pool: Source) {
    reimportingId = pool.id;
    reimportFile = null;
    reimportPath = '';
    reimportError = null;
  }

  function cancelReimport() {
    reimportingId = null;
    reimportFile = null;
    reimportPath = '';
    reimportError = null;
  }

  function onReimportFileChange(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    reimportFile = input.files?.[0] ?? null;
  }

  const SOURCES_TABLE_COLS = 4;

  async function submitReimport(pool: Source) {
    reimportError = null;
    reimportSuccessMessage = null;
    if (mode === 'upload' && !reimportFile) {
      reimportError = 'Pick an iTunes XML file to upload.';
      return;
    }
    if (mode === 'path' && !reimportPath.trim()) {
      reimportError = 'Type the server-side path to the XML.';
      return;
    }
    const ok = await confirmDialog({
      title: `Replace pool '${pool.name}'?`,
      body:
        `Replaces all ${pool.track_count.toLocaleString()} track${pool.track_count === 1 ? '' : 's'} ` +
        `(role: ${pool.role}). This cannot be undone.\n\n` +
        `Generated playlists are unaffected — they reference tracks directly.`,
      confirmLabel: 'Replace',
      danger: true,
    });
    if (!ok) return;

    reimporting = true;
    try {
      if (mode === 'upload') {
        // c3-review 🟢-3 (parallel): narrow with a runtime check
        // instead of `!` — the function-entry validation guarantees
        // non-null here, but the explicit check is defensive.
        if (!reimportFile) {
          throw new Error('bug: reimport reached confirm with no file');
        }
        await uploadSource(pool.name, pool.role, reimportFile);
      } else {
        await importSourceByPath(pool.name, pool.role, reimportPath.trim());
      }
      reimportSuccessMessage = `Re-imported "${pool.name}".`;
      cancelReimport();
      await refresh();
      dispatch('changed');
    } catch (e) {
      reimportError = e instanceof ApiError ? e.message : String(e);
    } finally {
      reimporting = false;
    }
  }

  // ---- Delete (c4) ----
  let deleting: number | null = null;
  let deleteError: string | null = null;

  async function deletePool(pool: Source) {
    const ok = await confirmDialog({
      title: `Delete pool '${pool.name}'?`,
      body:
        `${pool.track_count.toLocaleString()} track${pool.track_count === 1 ? '' : 's'}. ` +
        `This cannot be undone.\n\n` +
        `Generated playlists are unaffected — they reference tracks directly, not pools.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    deleting = pool.id;
    deleteError = null;
    reimportSuccessMessage = null;
    if (reimportingId === pool.id) cancelReimport();
    try {
      await deleteSource(pool.id);
      await refresh();
      dispatch('changed');
    } catch (e) {
      deleteError = e instanceof ApiError ? e.message : String(e);
    } finally {
      deleting = null;
    }
  }
</script>

<div class="panel" data-testid="pool-import-panel">
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

    <div class="mode-toggle" role="tablist" aria-label="How to provide the XML">
      <button
        type="button"
        role="tab"
        class="mode-tab"
        class:active={mode === 'upload'}
        aria-selected={mode === 'upload'}
        data-testid="mode-toggle-upload"
        on:click={() => setMode('upload')}
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
        on:click={() => setMode('path')}
        disabled={submitting}
      >
        Advanced: server path
      </button>
    </div>

    {#if batchMode}
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

        <div class="submit-row">
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
          {#if submitting}
            <!-- §16.25.t2: Cancel sibling button while a batch is
                 in-flight. Aborts the current upload (rejects with
                 AbortError → row marked 'cancelled') and the
                 abortController.signal.aborted check at the top of
                 the loop iteration short-circuits the pending tail. -->
            <button
              type="button"
              class="button"
              data-testid="batch-cancel"
              on:click={cancelSubmission}
            >
              Cancel
            </button>
          {/if}
        </div>
      </form>
    {:else}
      <form
        class="add-pool-form"
        data-testid="add-pool-form"
        on:submit|preventDefault={submit}
      >
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

        <div class="submit-row">
          <button
            type="submit"
            class="button primary"
            data-testid="submit-button"
            disabled={submitting}
          >
            {submitting ? 'Importing…' : 'Import'}
          </button>
          {#if submitting}
            <!-- §16.25.t2: Cancel sibling button. Aborts the in-flight
                 single upload — fetch rejects with AbortError, the
                 outer submit() catch sets formError = "Upload
                 cancelled." Same primitive as batch mode (one
                 abortController per submit) just with one upload. -->
            <button
              type="button"
              class="button"
              data-testid="cancel-button"
              on:click={cancelSubmission}
            >
              Cancel
            </button>
          {/if}
        </div>
      </form>
    {/if}
  </section>

  <!-- ===== Imported pools list ===== -->
  <section class="list-section">
    <h2>{scope === undefined ? 'Imported pools' : 'Imported pools for this station'}</h2>

    {#if loading}
      <p>Loading…</p>
    {:else if listError}
      <p class="error" data-testid="list-error">Could not load pools: {listError}</p>
    {:else if visibleSources.length === 0}
      <p data-testid="empty-state">
        {scope === undefined
          ? 'No pools imported yet. Use the form above to import your first one.'
          : 'None of this station’s declared pools are imported yet. Use the form above to import them.'}
      </p>
    {:else}
      {#if reimportSuccessMessage}
        <p class="success" data-testid="reimport-success">{reimportSuccessMessage}</p>
      {/if}
      {#if deleteError}
        <p class="error" data-testid="delete-error">Could not delete: {deleteError}</p>
      {/if}
      <table class="sources-list" data-testid="sources-list">
        <thead>
          <tr>
            <th>Name</th>
            <th>Role</th>
            <th class="num">Tracks</th>
            <th class="actions-col"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {#each visibleSources as s (s.id)}
            <tr data-testid="pool-row" data-pool-name={s.name}>
              <td class="name">{s.name}</td>
              <td>
                <span class="role-tag role-{s.role}">{s.role}</span>
              </td>
              <td class="num">{s.track_count.toLocaleString()}</td>
              <td class="actions">
                {#if reimportingId === s.id}
                  <button
                    type="button"
                    class="row-button"
                    data-testid="pool-row-cancel-reimport"
                    on:click={cancelReimport}
                    disabled={reimporting}
                  >
                    Cancel
                  </button>
                {:else}
                  <button
                    type="button"
                    class="row-button"
                    data-testid="pool-row-reimport"
                    on:click={() => startReimport(s)}
                    disabled={deleting !== null || reimporting}
                  >
                    Re-import
                  </button>
                  <button
                    type="button"
                    class="row-button danger"
                    data-testid="pool-row-delete"
                    on:click={() => deletePool(s)}
                    disabled={deleting !== null || reimporting}
                  >
                    {deleting === s.id ? 'Deleting…' : 'Delete'}
                  </button>
                {/if}
              </td>
            </tr>
            {#if reimportingId === s.id}
              <tr class="reimport-row" data-testid="reimport-row" data-pool-name={s.name}>
                <td colspan={SOURCES_TABLE_COLS}>
                  <div class="reimport-form">
                    <p class="reimport-summary">
                      Re-import <b>{s.name}</b> — role <span class="role-tag role-{s.role}">{s.role}</span>
                      (read-only on re-import; role change is a separate slice).
                      Replaces all {s.track_count.toLocaleString()} existing track{s.track_count === 1 ? '' : 's'}.
                    </p>
                    {#if mode === 'upload'}
                      <input
                        type="file"
                        accept=".xml,application/xml,text/xml"
                        data-testid="reimport-file-input"
                        on:change={onReimportFileChange}
                        disabled={reimporting}
                      />
                    {:else}
                      <input
                        type="text"
                        class="reimport-path-input"
                        placeholder="/path/to/library.xml"
                        data-testid="reimport-path-input"
                        bind:value={reimportPath}
                        disabled={reimporting}
                      />
                    {/if}
                    {#if reimportError}
                      <p class="error" data-testid="reimport-error">{reimportError}</p>
                    {/if}
                    <button
                      type="button"
                      class="button primary"
                      data-testid="reimport-submit"
                      on:click={() => submitReimport(s)}
                      disabled={reimporting}
                    >
                      {reimporting ? 'Replacing…' : 'Replace'}
                    </button>
                  </div>
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
</div>

<style>
  /* The `.panel` wrapper is intentionally a styleless block — the
     embedding page owns layout (max-width, margins, padding). */
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
  /* §16.25.t2 — user-initiated cancel; muted grey, not error-red. */
  .batch-row[data-row-status='cancelled'] {
    background: #f9fafb;
    border-color: #d4d4d8;
    color: #6b7280;
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
  .status-cancelled {
    background: #e5e7eb;
    color: #4b5563;
  }
  /* §16.25.t2 — Cancel button sits next to Import / Import N pools
     during submission. `submit-row` lays them out as a flex row so
     the cancel doesn't push the submit to a new line. */
  .submit-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
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

  /* ===== c4 row actions + Re-import inline expansion ===== */
  .actions-col {
    width: 1%;
    white-space: nowrap;
  }
  .actions {
    text-align: right;
    white-space: nowrap;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    border: 0;
  }
  .row-button {
    font: inherit;
    font-size: 0.82rem;
    padding: 0.25rem 0.6rem;
    border: 1px solid #d4d4d8;
    background: #fff;
    border-radius: 4px;
    cursor: pointer;
    margin-left: 0.25rem;
  }
  .row-button:hover:not(:disabled) {
    background: #f3f4f6;
  }
  .row-button.danger {
    color: #991b1b;
    border-color: #fecaca;
  }
  .row-button.danger:hover:not(:disabled) {
    background: #fef2f2;
  }
  .row-button:disabled {
    color: #9ca3af;
    cursor: wait;
  }
  .reimport-row td {
    background: #fefce8;
    border-top: 1px solid #fef08a;
    padding: 0.75rem 0.75rem;
  }
  .reimport-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .reimport-summary {
    margin: 0;
    font-size: 0.9rem;
  }
  .reimport-path-input {
    padding: 0.4rem 0.5rem;
    border: 1px solid #c4c4c8;
    border-radius: 4px;
    font: inherit;
    max-width: 32rem;
  }
</style>
