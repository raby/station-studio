<script lang="ts">
  /**
   * §16.2.2 c2 — visualise route scaffold.
   *
   * Fetches GET /api/stations/{slug}/sidecar and renders a minimal
   * placeholder. The real canvas (Section / ChipFlow / Chip / etc.)
   * lands in c3. This file's job: prove the load path + types are
   * wired before c3 starts composing components.
   *
   * Three render states:
   *   - loading       → "Loading…"
   *   - error (404)   → station-not-found message + back link
   *   - loaded        → counts summary + sidecar_error warning (if any)
   *
   * Discoverability link from /stations/[slug] lands in c4 (per the
   * design's "first-impression" treatment); c2 is reachable only by
   * direct URL or test.
   */
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import {
    BlockPatchConflict,
    createSection,
    deleteSection,
    exportStationTemplate,
    getStationSidecar,
    moveBlock,
    patchBlock,
    patchMacro,
    renameSection,
    reorderSections,
    retypeBlock,
    structuralBlockEdit,
    type BlockAddress,
    type StructuralBlockEdit,
  } from '$lib/api';
  import type { ExpandedRef, Sidecar, StationTemplate } from '$lib/editor';
  import EditorCanvas from '$lib/components/editor/EditorCanvas.svelte';

  let template: StationTemplate | null = null;
  let sidecar: Sidecar | null = null;
  let sidecarError: string | null = null;
  let loadError: string | null = null;
  let loading = true;

  // §16.2.3 c2 — optimistic-concurrency token + edit/export status.
  let updatedAt: string | null = null;
  let saveError: string | null = null;
  let conflict = false;
  let exportMsg: string | null = null;

  $: slug = $page.params.slug ?? '';

  async function loadSidecar() {
    const r = await getStationSidecar(slug);
    template = r.template;
    sidecar = r.sidecar;
    sidecarError = r.sidecar_error;
    updatedAt = r.updated_at;
  }

  onMount(async () => {
    if (!slug) {
      loadError = 'missing slug';
      loading = false;
      return;
    }
    try {
      // §16.2.2.t1 — getStationSidecar validates the response shape
      // before return. Both ApiError (network/4xx) and
      // EditorMetadataError (shape) carry `.message`.
      await loadSidecar();
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  // Map a chip's ExpandedRef to the storage BlockAddress. row_idx is
  // dropped — a row_template edit targets the shared block_idx
  // (Decision B).
  function refToAddress(ref: ExpandedRef): BlockAddress | null {
    if (ref.kind !== 'atomic-ref') return null;
    if (ref.slot_index !== undefined) return { slot_index: ref.slot_index };
    if (ref.block_idx !== undefined) return { block_idx: ref.block_idx };
    return null;
  }

  // c2 review #5: extract the PATCH-then-refetch lifecycle that
  // every write-path handler shares. Resets the three banner
  // variables, runs the supplied PATCH (catching 409 via the
  // shared BlockPatchConflict contract — Mid-slice review 🟡-1: do
  // NOT adopt the server's token; force the operator to Reload
  // before retrying so concurrent changes aren't silently
  // overwritten), then refetches the canvas. Refetch failures
  // surface with a distinct `refetchVerbPrefix` so the operator
  // can tell "save itself failed" from "save landed but the view
  // didn't refresh."
  async function withSaveLifecycle(
    doPatch: () => Promise<unknown>,
    refetchVerbPrefix: string,
  ): Promise<void> {
    saveError = null;
    conflict = false;
    exportMsg = null;
    try {
      await doPatch();
    } catch (err) {
      if (err instanceof BlockPatchConflict) {
        conflict = true;
      } else {
        saveError = err instanceof Error ? err.message : String(err);
      }
      return;
    }
    // PATCH landed — refresh canvas + optimistic token. Distinct
    // (mid-slice review 🟢-3) so a refetch blip after a successful
    // save doesn't masquerade as a save failure.
    try {
      await loadSidecar();
    } catch (err) {
      saveError =
        `${refetchVerbPrefix}, but failed to refresh the view: ` +
        (err instanceof Error ? err.message : String(err));
    }
  }

  // §16.2.3 c2 — DetailPanel save → PATCH → refetch. On 409 (someone
  // else changed the station) show a reload prompt; on other errors
  // show the message inline.
  async function handleSave(
    e: CustomEvent<{ ref: ExpandedRef; fields: Record<string, string | number | string[] | null> }>,
  ) {
    const address = refToAddress(e.detail.ref);
    if (address === null) {
      saveError = 'cannot edit this block (no storage address)';
      conflict = false;
      exportMsg = null;
      return;
    }
    await withSaveLifecycle(
      () => patchBlock(slug, address, e.detail.fields, updatedAt),
      'saved',
    );
  }

  async function handleExport() {
    saveError = null;
    exportMsg = null;
    try {
      const r = await exportStationTemplate(slug);
      exportMsg = `Exported to ${r.path} (${r.bytes} bytes).`;
    } catch (err) {
      saveError = err instanceof Error ? err.message : String(err);
    }
  }

  // §16.2.4 c2 — macro fan-out edit (PATCH /macros/{macro_id}).
  async function handleMacroSave(
    e: CustomEvent<{ macroId: string; fields: Record<string, string | number | string[] | null> }>,
  ) {
    await withSaveLifecycle(
      () => patchMacro(slug, e.detail.macroId, e.detail.fields, updatedAt),
      'saved',
    );
  }

  // §16.2.4 c2 — atomic block retype (PATCH /blocks/retype).
  async function handleRetype(
    e: CustomEvent<{
      ref: ExpandedRef;
      newStrategy: string | null;
      newSource: string | null;
      newFields: Record<string, string | number | string[] | null>;
    }>,
  ) {
    const address = refToAddress(e.detail.ref);
    if (address === null) {
      saveError = 'cannot retype this block (no storage address)';
      conflict = false;
      exportMsg = null;
      return;
    }
    await withSaveLifecycle(
      () => retypeBlock(
        slug,
        address,
        e.detail.newStrategy,
        e.detail.newSource,
        e.detail.newFields,
        updatedAt,
      ),
      'retype landed',
    );
  }

  // §16.2.5 c2 — section CRUD + structural-edit + move handlers.
  // Each is a thin wrapper over `withSaveLifecycle` (extracted in
  // §16.2.4 c2 review #5) for uniform 409 / refetch / banner flow.

  async function handleSectionCreate(
    e: CustomEvent<{ id: string; name: string | null }>,
  ) {
    await withSaveLifecycle(
      () => createSection(slug, e.detail.id, e.detail.name, updatedAt),
      'created',
    );
  }

  async function handleSectionRename(
    e: CustomEvent<{ sectionId: string; name: string }>,
  ) {
    await withSaveLifecycle(
      () => renameSection(slug, e.detail.sectionId, e.detail.name, updatedAt),
      'renamed',
    );
  }

  async function handleSectionDelete(
    e: CustomEvent<{ sectionId: string }>,
  ) {
    await withSaveLifecycle(
      () => deleteSection(slug, e.detail.sectionId, updatedAt),
      'deleted',
    );
  }

  async function handleSectionsReorder(
    e: CustomEvent<{ layoutOrder: string[] }>,
  ) {
    await withSaveLifecycle(
      () => reorderSections(slug, e.detail.layoutOrder, updatedAt),
      'reordered',
    );
  }

  async function handleBlockMove(
    e: CustomEvent<{
      ref: ExpandedRef;
      targetSectionId: string;
      targetIndex: number | null;
    }>,
  ) {
    const address = refToAddress(e.detail.ref);
    if (address === null) {
      saveError = 'cannot move this block (no storage address)';
      conflict = false;
      exportMsg = null;
      return;
    }
    await withSaveLifecycle(
      () => moveBlock(
        slug, address, e.detail.targetSectionId,
        e.detail.targetIndex, updatedAt,
      ),
      'moved',
    );
  }

  async function handleStructuralEdit(
    e: CustomEvent<{ edit: StructuralBlockEdit }>,
  ) {
    // c2 review #10: name the op in the refetch-failure prefix so a
    // refresh blip after a successful structural edit tells the
    // operator WHICH mutation landed ('resized slot 3' vs
    // 'removed slot 5' vs 'added slot 2'). The generic prior
    // 'applied' was indistinct + the recovery path depends on op.
    const verb = ({
      size: `resized slot ${e.detail.edit.slot_index}`,
      add: `added slot ${e.detail.edit.slot_index}`,
      remove: `removed slot ${e.detail.edit.slot_index}`,
    } as Record<string, string>)[e.detail.edit.op] ?? 'applied structural edit';
    await withSaveLifecycle(
      () => structuralBlockEdit(slug, e.detail.edit, updatedAt),
      verb,
    );
  }

  async function reloadAfterConflict() {
    conflict = false;
    try {
      await loadSidecar();
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    }
  }
</script>

<svelte:head>
  <title>Visualise — {template?.name ?? slug} — Station Studio</title>
</svelte:head>

<main>
  <p class="crumbs">
    <a href="/stations/{encodeURIComponent(slug)}">← {slug}</a>
  </p>

  <h1>Visualise template</h1>

  {#if loading}
    <p>Loading…</p>
  {:else if loadError}
    <p class="error" data-testid="visualise-error">{loadError}</p>
  {:else if template && sidecar}
    <p class="slug">{template.station} — {template.name}</p>

    {#if sidecarError}
      <!--
        Decision E warning banner. The committed sidecar failed
        validation; server fell back to the synthesized trivial one
        + populated sidecar_error. Operator can keep working while
        figuring out what broke.
      -->
      <div class="sidecar-warning" data-testid="sidecar-warning">
        <strong>Warning:</strong> the committed
        <code>stations/{slug}.editor.json</code> is malformed —
        falling back to the trivial view.
        <pre>{sidecarError}</pre>
        Fix the file or regenerate it with
        <code>make derive-sidecar STATION={slug}</code>.
      </div>
    {/if}

    <!-- §16.2.3 c2 — edit affordances: export-to-disk + edit status. -->
    <div class="editor-actions">
      <button
        type="button"
        class="export"
        data-testid="export-template"
        on:click={handleExport}
      >
        Export to disk
      </button>
      {#if exportMsg}
        <span class="export-msg" data-testid="export-msg">{exportMsg}</span>
      {/if}
    </div>

    {#if conflict}
      <div class="conflict" data-testid="conflict-banner">
        This station changed elsewhere (another tab or the edit route).
        <button type="button" data-testid="conflict-reload" on:click={reloadAfterConflict}>
          Reload latest
        </button>
      </div>
    {/if}
    {#if saveError}
      <p class="error" data-testid="save-error">{saveError}</p>
    {/if}

    <!--
      §16.2.3 — editable visual block canvas. The DetailPanel's `save`
      event bubbles up here (via EditorCanvas) to the PATCH call.
    -->
    <EditorCanvas
      {template}
      {sidecar}
      on:save={handleSave}
      on:macroSave={handleMacroSave}
      on:retype={handleRetype}
      on:sectionCreate={handleSectionCreate}
      on:sectionRename={handleSectionRename}
      on:sectionDelete={handleSectionDelete}
      on:sectionsReorder={handleSectionsReorder}
      on:blockMove={handleBlockMove}
      on:structuralEdit={handleStructuralEdit}
    />
  {/if}
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 900px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .editor-actions {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin: 0.75rem 0;
  }
  .export {
    padding: 0.4rem 0.9rem;
    background: #2c3e50;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .export:hover {
    background: #1a252f;
  }
  .export-msg {
    color: #2e7d32;
    font-size: 0.85rem;
  }
  .conflict {
    background: #fff8e1;
    border: 1px solid #f0c36d;
    border-radius: 4px;
    padding: 0.6rem 0.9rem;
    margin: 0.5rem 0;
    font-size: 0.9rem;
  }
  .conflict button {
    margin-left: 0.5rem;
    padding: 0.2rem 0.6rem;
    cursor: pointer;
  }
  .crumbs {
    font-size: 0.85rem;
    margin-bottom: 1rem;
  }
  .crumbs a {
    color: #666;
    text-decoration: none;
  }
  .crumbs a:hover {
    text-decoration: underline;
  }
  h1 {
    margin: 0;
  }
  .slug {
    color: #888;
    font-family: ui-monospace, monospace;
    margin-top: 0.1rem;
  }
  .sidecar-warning {
    background: #fff8e1;
    border: 1px solid #f0c36d;
    border-radius: 4px;
    padding: 0.75rem 1rem;
    margin: 1rem 0;
    font-size: 0.9rem;
  }
  .sidecar-warning pre {
    background: #fffaf0;
    padding: 0.4rem 0.6rem;
    border-radius: 3px;
    overflow-x: auto;
    font-size: 0.85rem;
    margin: 0.5rem 0;
  }
  .error {
    color: crimson;
  }
  code {
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
  }
</style>
