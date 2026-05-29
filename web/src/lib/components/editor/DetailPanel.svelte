<script lang="ts">
  /**
   * <DetailPanel> — §16.2.2 c4 + §16.2.3 c1 (atomic edit form).
   *
   * Slides in from the right when the user clicks a chip. Shows the
   * full spec for that chip:
   *
   *   - atomic: identity (strategy/source/size, read-only) + an
   *     EDIT FORM for the strategy's parameter fields (§16.2.3 c1).
   *   - macro: read-only spec + repeat (macro edit is §16.2.4).
   *
   * §16.2.3 c1 scope: the form edits PARAMETER fields only (Decision
   * C — cursor_id / genre / window / exclusion_window / position /
   * fallback_genre). strategy / source / size are read-only here.
   * Save dispatches a `save` event with `{ ref, fields }`; the route
   * wires persistence (the PATCH endpoint) in c2 — this component
   * does no fetching.
   *
   * row_template caveat (Decision B): editing a row_template block
   * edits the SHARED template block → every row changes. The form
   * shows a warning in that case.
   */
  import { createEventDispatcher } from 'svelte';
  import {
    availableCursorIds,
    editableFieldsForBlock,
    lookupEngineBlock,
    lookupMacro,
    validateEditableField,
    type EditableFieldSpec,
    type ExpandedRef,
    type Sidecar,
    type SidecarSection,
    type StationTemplate,
  } from '$lib/editor';

  /** The selected ref (or null when nothing's selected — panel is
   *  hidden in that case). */
  export let selected: { ref: ExpandedRef; section: SidecarSection } | null = null;
  export let template: StationTemplate;
  export let sidecar: Sidecar;

  const dispatch = createEventDispatcher<{
    close: void;
    save: { ref: ExpandedRef; fields: Record<string, string | number | null> };
  }>();

  // Resolve the spec under the selection.
  $: detail = (() => {
    if (selected === null) return null;
    const { ref, section } = selected;
    if (ref.kind === 'atomic-ref') {
      const block = lookupEngineBlock(ref, template.layout);
      return {
        kind: 'atomic' as const,
        section,
        block,
        macro: null,
        address: ref.slot_index !== undefined
          ? `slot_index=${ref.slot_index}`
          : `row_idx=${ref.row_idx}, block_idx=${ref.block_idx}`,
      };
    }
    const macro = lookupMacro(sidecar, ref.macro_id);
    return {
      kind: 'macro' as const,
      section,
      block: macro?.spec ?? null,
      macro,
      address: `macro="${ref.macro_id}"`,
    };
  })();

  // ----- §16.2.3 c1: edit-form state ---------------------------------
  //
  // Editable only for atomic blocks (macros = §16.2.4). The form binds
  // string values (number inputs give strings); int conversion happens
  // at validate + save time.

  // Editing a row_template block edits the SHARED row template — all
  // rows change. Decision B's required warning.
  $: isRowTemplateEdit =
    selected?.ref.kind === 'atomic-ref' && selected.ref.block_idx !== undefined;

  $: editFields =
    detail && detail.kind === 'atomic' && detail.block
      ? editableFieldsForBlock(detail.block)
      : ([] as EditableFieldSpec[]);

  // Reseed the form whenever the SELECTION changes (not on every
  // reactive tick — that would clobber the user's in-progress edits).
  let editValues: Record<string, string> = {};
  let seededFor: string | null = null;
  $: {
    const key = selected ? JSON.stringify(selected.ref) : null;
    if (key !== seededFor) {
      seededFor = key;
      const next: Record<string, string> = {};
      if (detail && detail.kind === 'atomic' && detail.block) {
        for (const f of editableFieldsForBlock(detail.block)) {
          const v = detail.block[f.name];
          next[f.name] = v === undefined || v === null ? '' : String(v);
        }
      }
      editValues = next;
    }
  }

  // Per-field validation (mirrors engine validators via the helper).
  $: fieldErrors = Object.fromEntries(
    editFields.map((f) => {
      const raw = editValues[f.name] ?? '';
      const val = f.type === 'int' ? (raw === '' ? undefined : Number(raw)) : raw;
      return [f.name, validateEditableField(f, val, template)];
    }),
  ) as Record<string, string | null>;

  $: hasErrors = Object.values(fieldErrors).some((e) => e !== null);

  $: dirty =
    detail && detail.kind === 'atomic' && detail.block
      ? editFields.some((f) => {
          const current = detail.block![f.name];
          const currentStr =
            current === undefined || current === null ? '' : String(current);
          return (editValues[f.name] ?? '') !== currentStr;
        })
      : false;

  $: cursorIds = availableCursorIds(template);

  function close() {
    dispatch('close');
  }

  function onSave() {
    if (
      selected === null ||
      detail === null ||
      detail.kind !== 'atomic' ||
      !detail.block ||
      hasErrors ||
      !dirty
    ) {
      return;
    }
    // Build the fields payload: each editable field's value, with an
    // emptied OPTIONAL field sent as null (→ the PATCH endpoint clears
    // the key). Required fields can't be empty (validation blocks save).
    const fields: Record<string, string | number | null> = {};
    for (const f of editFields) {
      const s = (editValues[f.name] ?? '').trim();
      if (s === '') {
        fields[f.name] = null;
      } else {
        fields[f.name] = f.type === 'int' ? Number(s) : s;
      }
    }
    dispatch('save', { ref: selected.ref, fields });
  }

  // Esc to close.
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && selected !== null) close();
  }
</script>

<svelte:window on:keydown={onKey} />

{#if detail !== null}
  <aside
    class="detail-panel"
    role="dialog"
    aria-label="Chip details"
    data-testid="detail-panel"
    data-kind={detail.kind}
  >
    <header>
      <h2>
        {#if detail.kind === 'atomic'}
          Atomic block
        {:else}
          Macro: {detail.macro?.name ?? detail.macro?.id ?? '?'}
        {/if}
      </h2>
      <button
        type="button"
        class="close"
        aria-label="Close details"
        data-testid="detail-close"
        on:click={close}
      >×</button>
    </header>

    <dl>
      <dt>Section</dt>
      <dd data-testid="detail-section-name">
        {detail.section.name ?? detail.section.id}
      </dd>

      <dt>Address</dt>
      <dd class="mono" data-testid="detail-address">{detail.address}</dd>

      {#if detail.kind === 'macro' && detail.macro}
        <dt>Repeat</dt>
        <dd data-testid="detail-repeat">{detail.macro.repeat}</dd>
      {/if}

      {#if detail.block}
        <!-- Identity — read-only (strategy/source/size are structural;
             §16.2.4/§16.2.5 own those edits). -->
        <dt>Strategy</dt>
        <dd data-testid="detail-strategy">{detail.block.strategy}</dd>

        <dt>Source</dt>
        <dd data-testid="detail-source">{detail.block.source}</dd>

        <dt>Size</dt>
        <dd>{detail.block.size}</dd>

        {#if detail.kind === 'macro'}
          <!-- Macros are read-only here (macro edit = §16.2.4): show
               the spec's parameter fields inline. Atomic blocks edit
               these via the form below instead. -->
          {#if detail.block.cursor_id !== undefined}
            <dt>cursor_id</dt><dd class="mono">{detail.block.cursor_id}</dd>
          {/if}
          {#if detail.block.window !== undefined}
            <dt>window</dt><dd>{detail.block.window}</dd>
          {/if}
          {#if detail.block.exclusion_window !== undefined}
            <dt>exclusion_window</dt><dd>{detail.block.exclusion_window}</dd>
          {/if}
          {#if detail.block.genre !== undefined}
            <dt>genre</dt><dd class="mono">{detail.block.genre}</dd>
          {/if}
          {#if detail.block.position !== undefined}
            <dt>position</dt><dd>{detail.block.position}</dd>
          {/if}
          {#if detail.block.fallback_genre !== undefined}
            <dt>fallback_genre</dt><dd class="mono">{detail.block.fallback_genre}</dd>
          {/if}
        {/if}
      {:else}
        <dt>Status</dt>
        <dd class="warn">
          Engine block not found at this address — the sidecar may
          reference a slot the template no longer has.
        </dd>
      {/if}
    </dl>

    <!-- §16.2.3 c1: edit form for atomic blocks only (macros = §16.2.4). -->
    {#if detail.kind === 'atomic' && detail.block}
      <form class="edit-form" data-testid="edit-form" on:submit|preventDefault={onSave}>
        <h3>Parameters</h3>

        {#if isRowTemplateEdit}
          <p class="shared-warning" data-testid="shared-block-warning">
            This block is part of the shared <strong>row template</strong> —
            editing it changes <strong>every row</strong> that uses it.
          </p>
        {/if}

        {#if editFields.length === 0}
          <p class="no-params" data-testid="no-params">
            The <code>{detail.block.strategy}</code> strategy has no
            editable parameters.
          </p>
        {:else}
          {#each editFields as f (f.name)}
            <label class="field" data-testid="field-{f.name}">
              <span class="field-label">
                {f.label}{f.required ? '' : ' (optional)'}
              </span>
              {#if f.enumFromCursors}
                <select
                  data-testid="input-{f.name}"
                  bind:value={editValues[f.name]}
                >
                  {#if !f.required}
                    <option value="">(none)</option>
                  {/if}
                  {#each cursorIds as cid (cid)}
                    <option value={cid}>{cid}</option>
                  {/each}
                </select>
              {:else if f.type === 'int'}
                <input
                  type="number"
                  min={f.min ?? undefined}
                  data-testid="input-{f.name}"
                  bind:value={editValues[f.name]}
                />
              {:else}
                <input
                  type="text"
                  data-testid="input-{f.name}"
                  bind:value={editValues[f.name]}
                />
              {/if}
              {#if fieldErrors[f.name]}
                <span class="field-error" data-testid="error-{f.name}">
                  {fieldErrors[f.name]}
                </span>
              {/if}
            </label>
          {/each}

          <button
            type="submit"
            class="save"
            data-testid="detail-save"
            disabled={!dirty || hasErrors}
          >
            Save
          </button>
        {/if}
      </form>
    {/if}
  </aside>
{/if}

<style>
  .detail-panel {
    position: fixed;
    top: 0;
    right: 0;
    height: 100vh;
    width: 320px;
    background: white;
    border-left: 1px solid var(--border, #ddd);
    box-shadow: -2px 0 10px rgba(0, 0, 0, 0.05);
    padding: 1rem;
    overflow-y: auto;
    /* Custom property (slice review 🟢-5) so a future stacking
       conflict (modal over panel) can override the layer without
       editing this component. Default 10 keeps it above the
       canvas chips' :focus outline + the static layout. */
    z-index: var(--detail-panel-z, 10);
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    border-bottom: 1px solid var(--border, #eee);
    padding-bottom: 0.5rem;
    margin-bottom: 0.75rem;
  }
  h2 {
    margin: 0;
    flex: 1;
    font-size: 1rem;
  }
  .close {
    background: transparent;
    border: none;
    font-size: 1.4rem;
    cursor: pointer;
    color: var(--text-muted, #666);
    line-height: 1;
    padding: 0 0.4rem;
  }
  .close:hover {
    color: black;
  }
  dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 0.3rem 0.75rem;
    margin: 0;
    font-size: 0.88rem;
  }
  dt {
    color: var(--text-muted, #666);
    font-weight: 500;
  }
  dd {
    margin: 0;
  }
  .mono {
    font-family: ui-monospace, monospace;
    font-size: 0.85rem;
  }
  .warn {
    color: var(--error, #c0392b);
    font-style: italic;
  }

  /* §16.2.3 c1 — edit form */
  .edit-form {
    margin-top: 1rem;
    border-top: 1px solid var(--border, #eee);
    padding-top: 0.75rem;
  }
  .edit-form h3 {
    margin: 0 0 0.5rem;
    font-size: 0.9rem;
    color: var(--text-muted, #555);
  }
  .shared-warning {
    background: #fff8e1;
    border: 1px solid #f0c36d;
    border-radius: 4px;
    padding: 0.5rem 0.6rem;
    font-size: 0.82rem;
    margin: 0 0 0.75rem;
  }
  .no-params {
    color: var(--text-muted, #888);
    font-size: 0.85rem;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    margin-bottom: 0.6rem;
  }
  .field-label {
    font-size: 0.82rem;
    color: var(--text-muted, #555);
  }
  .field input,
  .field select {
    padding: 0.35rem;
    border: 1px solid var(--border, #ccc);
    border-radius: 4px;
    font: inherit;
    font-size: 0.88rem;
  }
  .field-error {
    color: var(--error, #c0392b);
    font-size: 0.78rem;
  }
  .save {
    margin-top: 0.25rem;
    padding: 0.4rem 1rem;
    background: var(--accent, #2c7a7b);
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .save:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
