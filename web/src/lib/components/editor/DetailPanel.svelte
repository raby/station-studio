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
    compatibleSources,
    compatibleStrategies,
    editableFieldsForBlock,
    EDITABLE_FIELDS,
    lookupEngineBlock,
    lookupMacro,
    validateEditableField,
    type EditableFieldSpec,
    type EngineBlock,
    type ExpandedRef,
    type Sidecar,
    type SidecarSection,
    type StationTemplate,
    type Strategy,
  } from '$lib/editor';
  import ChipMultiInput from './ChipMultiInput.svelte';

  // c2 review #2: shared field-value union covering every editable
  // type the form can hold. `string | number | null` covers the
  // string + int fields; `string[]` covers string_list
  // (allowlist_sources today). Exported indirectly via the dispatch
  // event types so EditorCanvas + the route handlers can import the
  // same shape without re-declaring.
  type FieldValue = string | number | string[] | null;

  /** The selected ref (or null when nothing's selected — panel is
   *  hidden in that case). */
  export let selected: { ref: ExpandedRef; section: SidecarSection } | null = null;
  export let template: StationTemplate;
  export let sidecar: Sidecar;

  /**
   * §16.2.4 c2: dispatch contracts —
   *  - `save`: atomic block parameter edit (PATCH /blocks)
   *  - `retype`: atomic block strategy/source change (PATCH /blocks/retype).
   *      `newFields` carries any newly-required fields (e.g.
   *      `{position: 1}` when retyping to archive_ref). The route
   *      handler is responsible for the API call.
   *  - `macroSave`: macro-fanout edit (PATCH /macros/{macro_id})
   */
  // c2 review #2: the fields payload's value union now includes
  // `string[]` end-to-end so `allowlist_sources` (string_list) can
  // ride along without the `as unknown as string` cast that the
  // initial c2 implementation used. JSON.stringify serializes the
  // array shape happily; widening the type contract makes that
  // explicit + lets downstream consumers (api.ts, route handler)
  // type-check honestly.
  const dispatch = createEventDispatcher<{
    close: void;
    save: { ref: ExpandedRef; fields: Record<string, FieldValue> };
    retype: {
      ref: ExpandedRef;
      newStrategy: string | null;
      newSource: string | null;
      newFields: Record<string, FieldValue>;
    };
    macroSave: { macroId: string; fields: Record<string, FieldValue> };
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

  // ----- §16.2.3 c1 + §16.2.4 c2: edit-form state --------------------
  //
  // The same form structure now serves THREE write paths:
  //  - atomic-block parameter edit (§16.2.3, dispatches `save`)
  //  - macro spec edit (§16.2.4 D1, dispatches `macroSave`)
  //  - atomic-block retype (§16.2.4 D2, dispatches `retype`)

  // Editing a row_template block edits the SHARED row template — all
  // rows change. Decision B's required warning.
  $: isRowTemplateEdit =
    selected?.ref.kind === 'atomic-ref' && selected.ref.block_idx !== undefined;

  // §16.2.4 D1: macros use the SAME EDITABLE_FIELDS table as atomic
  // blocks — `detail.block` is the macro's spec for macros and the
  // engine-block dict for atomics, both with a `strategy` key. So
  // `editableFieldsForBlock` works for both, but each strategy's
  // EDITABLE_FIELDS entry is what drives the form.
  $: editFields =
    detail && detail.block
      ? editableFieldsForBlock(detail.block as unknown as EngineBlock)
      : ([] as EditableFieldSpec[]);

  // §16.2.4 c2: values type widened to include `string[]` so the
  // string_list (allowlist_sources) field round-trips natively
  // — see normStr's Array.isArray branch.
  type EditVal = string | number | string[] | null;

  // Reseed the form whenever the SELECTION changes (not on every
  // reactive tick — that would clobber the user's in-progress edits).
  //
  // Value type is string | number | string[] | null, NOT just string
  // (c1 review 🟡-3 + §16.2.4): Svelte's `bind:value` on
  // <input type="number"> coerces to a NUMBER (or null when cleared);
  // ChipMultiInput emits a string[]. The declared union forces every
  // read site to normalize.
  let editValues: Record<string, EditVal> = {};
  let seededFor: string | null = null;
  $: {
    // c2 review #1: the seededFor key MUST reflect any change to the
    // resolved block content, not just the selection ref. Without
    // this, a successful retype (which keeps `selected.ref` stable
    // and only mutates `detail.block` via refetch) would skip the
    // reseed → editValues stays for the OLD strategy's fields,
    // retypeStrategy / retypeSource hold the pre-retype values, and
    // the form renders blank inputs for the new strategy's fields.
    // JSON.stringify of selected.ref + the block's stable identity
    // fields (strategy + source) catches the retype case + leaves
    // parameter-only edits as no-ops (block.strategy/source unchanged
    // → same key → no clobber of in-progress edits).
    const blockKey = detail?.block
      ? `${detail.block.strategy}/${detail.block.source}`
      : 'null';
    const key = selected ? `${JSON.stringify(selected.ref)}#${blockKey}` : null;
    if (key !== seededFor) {
      seededFor = key;
      const next: Record<string, EditVal> = {};
      if (detail && detail.block) {
        for (const f of editFields) {
          const v = (detail.block as unknown as Record<string, unknown>)[f.name];
          if (f.type === 'string_list') {
            // §16.2.4 c2: native array binding. Empty list ↔ "no
            // constraint" (engine collapses to None at parse time).
            next[f.name] = Array.isArray(v) ? [...v] : [];
          } else {
            next[f.name] = v === undefined || v === null ? '' : String(v);
          }
        }
      }
      editValues = next;
      // Re-seed retype state on selection change too.
      retypeOpen = false;
      retypeStrategy = (detail?.block as { strategy?: Strategy } | null)?.strategy ?? null;
      retypeSource = (detail?.block as { source?: string } | null)?.source ?? null;
      retypeNewFields = {};
      retypeError = null;
    }
  }

  // ----- §16.2.4 c2: retype state (atomic blocks only) ---------------
  //
  // The retype panel is collapsed by default; toggled open by a
  // "Change strategy / source" disclosure. When the operator picks a
  // new strategy whose EDITABLE_FIELDS requires fields the current
  // block doesn't carry (e.g. `position` when retyping TO
  // archive_ref), an inline sub-form collects them; `newFields`
  // is sent in the retype payload.
  let retypeOpen = false;
  let retypeStrategy: Strategy | null = null;
  let retypeSource: string | null = null;
  let retypeNewFields: Record<string, string | number | null> = {};
  let retypeError: string | null = null;

  // §16.2.4 D4: bidirectional dropdown filtering. Strategy options
  // narrow by the CURRENT source's role; source options narrow by
  // the CURRENT (possibly newly-picked) strategy's role requirement.
  $: currentSourceRole = (() => {
    if (!detail?.block) return 'content';
    const srcName = (detail.block as { source?: string }).source;
    return template.sources.find((s) => s.name === srcName)?.role ?? 'content';
  })();
  $: pickedSourceRole = (() => {
    if (retypeSource === null) return currentSourceRole;
    return template.sources.find((s) => s.name === retypeSource)?.role
      ?? currentSourceRole;
  })();
  // Strategy list reflects whatever source is CURRENTLY picked in the
  // retype panel (so swapping source first re-narrows strategy).
  $: strategyChoices = compatibleStrategies(pickedSourceRole);
  // Source list reflects whatever strategy is CURRENTLY picked.
  $: sourceChoices = retypeStrategy
    ? compatibleSources(retypeStrategy, template.sources)
    : template.sources;

  // Which fields the NEW strategy would require that the current
  // block doesn't carry — these need values via `newFields` for the
  // retype to land. e.g. retyping to archive_ref → `position` required.
  $: retypeRequiredNew = (() => {
    if (!retypeStrategy || !detail?.block) return [] as EditableFieldSpec[];
    const newSpec = EDITABLE_FIELDS[retypeStrategy] ?? [];
    const block = detail.block as unknown as Record<string, unknown>;
    return newSpec.filter(
      (f) => f.required && (block[f.name] === undefined || block[f.name] === null),
    );
  })();

  $: retypeDirty =
    retypeStrategy !== null &&
    (retypeStrategy !== (detail?.block as { strategy?: string } | null)?.strategy ||
      retypeSource !== (detail?.block as { source?: string } | null)?.source);

  $: retypeNewFieldsValid = retypeRequiredNew.every((f) => {
    const v = retypeNewFields[f.name];
    return validateEditableField(f, v ?? undefined, template) === null;
  });

  // Normalise a raw form value (string | number | null) to the
  // canonical string used for dirty-comparison + emptiness checks.
  // Centralises the c1-review 🟡-3/🟡-4 fix: Svelte's number-input
  // binding makes int-field values numbers, so neither dirty nor the
  // save payload can assume strings.
  function normStr(v: string | number | string[] | null | undefined): string {
    if (v === undefined || v === null) return '';
    // §16.2.4 c1: string[] (allowlist_sources, type: 'string_list')
    // joined with a stable separator for dirty-compare. The c2
    // chip-editor renders + edits the array directly; this
    // normalised string is ONLY for dirty/equality math. Sorted
    // to make order-only changes not count as dirty (the engine's
    // semantics are union over the list — order is meaningless).
    if (Array.isArray(v)) return v.slice().sort().join('|');
    return String(v).trim();
  }

  // Per-field validation (mirrors engine validators via the helper).
  // c2 review #3: string_list dispatches to the same helper —
  // validateEditableField gained a string_list branch that respects
  // spec.required, so no field type is special-cased here anymore.
  $: fieldErrors = Object.fromEntries(
    editFields.map((f) => {
      const raw = editValues[f.name];
      if (f.type === 'string_list') {
        return [f.name, validateEditableField(f, Array.isArray(raw) ? raw : [], template)];
      }
      const s = normStr(raw);
      const val = f.type === 'int' ? (s === '' ? undefined : Number(s)) : s;
      return [f.name, validateEditableField(f, val, template)];
    }),
  ) as Record<string, string | null>;

  $: hasErrors = Object.values(fieldErrors).some((e) => e !== null);

  $: dirty =
    detail && detail.block
      ? editFields.some((f) => {
          // Compare normalised strings on BOTH sides so an int field
          // returned to its original value (number vs seeded string)
          // doesn't read as dirty (c1 review 🟡-4). For string_list,
          // normStr sorts + joins — order-only changes don't count as
          // dirty (engine semantics are union over the list).
          const currentStr = normStr(detail.block![f.name] as EditVal);
          return normStr(editValues[f.name]) !== currentStr;
        })
      : false;

  $: cursorIds = availableCursorIds(template);

  // Reference-role sources available for the allowlist_sources
  // autocomplete (§16.2.4 D3 — only reference pools are valid
  // allowlist sources). Filtered once per template-prop change.
  $: referenceSources = template.sources.filter((s) => s.role === 'reference');

  /** Coerce a possibly-mixed edit value into a string[] for
   *  ChipMultiInput's `values` prop. Svelte's template parser
   *  rejects inline `as string[]` casts; this helper keeps the
   *  cast in the <script> block where TS understands it. */
  function asStringArray(v: EditVal): string[] {
    return Array.isArray(v) ? v : [];
  }

  function close() {
    dispatch('close');
  }

  /** Build the fields payload from current editValues. Shared by
   *  the atomic-save and macro-save paths since both send the same
   *  parameter-only PATCH body. c2 review #2: now honestly typed
   *  with `FieldValue` — the string_list branch returns the actual
   *  string[] without the `as unknown as string` lie. */
  function buildFieldsPayload(): Record<string, FieldValue> {
    const fields: Record<string, FieldValue> = {};
    for (const f of editFields) {
      const raw = editValues[f.name];
      if (f.type === 'string_list') {
        // Empty array → null (clears key, matches the engine's
        // empty-list-collapses-to-None contract). Non-empty array
        // sent verbatim — the PATCH endpoint accepts string[] for
        // `allowlist_sources`.
        const arr = Array.isArray(raw) ? raw : [];
        fields[f.name] = arr.length === 0 ? null : arr;
      } else {
        const s = normStr(raw);
        if (s === '') {
          fields[f.name] = null;
        } else {
          fields[f.name] = f.type === 'int' ? Number(s) : s;
        }
      }
    }
    return fields;
  }

  function onSave() {
    if (
      selected === null ||
      detail === null ||
      !detail.block ||
      hasErrors ||
      !dirty
    ) {
      return;
    }
    const fields = buildFieldsPayload();
    if (detail.kind === 'macro') {
      // §16.2.4 D1: macro fan-out edit.
      const macroId = detail.macro?.id;
      if (macroId !== undefined) {
        dispatch('macroSave', { macroId, fields });
      }
      return;
    }
    dispatch('save', { ref: selected.ref, fields });
  }

  /** §16.2.4 D2: dispatch a retype. The route handler does the API
   *  call + refetch. Inline validation is light here — server
   *  enforces via parse_template's role + field-set checks. */
  function onRetype() {
    if (selected === null || detail === null || detail.kind !== 'atomic') return;
    if (!retypeDirty) return;
    if (!retypeNewFieldsValid) {
      retypeError = 'fill required fields for the new strategy';
      return;
    }
    const block = detail.block as { strategy?: string; source?: string } | null;
    const newStrategy =
      retypeStrategy !== null && retypeStrategy !== block?.strategy
        ? retypeStrategy
        : null;
    const newSource =
      retypeSource !== null && retypeSource !== block?.source
        ? retypeSource
        : null;
    // Coerce retypeNewFields by their EditableFieldSpec.type so int
    // fields don't end up as string in the JSON body. The inline
    // retype sub-form renders int + string inputs only (string_list
    // fields require the chip-multi editor, which lives in the main
    // form not here) — so newFields entries are always scalar.
    const newFields: Record<string, FieldValue> = {};
    for (const f of retypeRequiredNew) {
      const v = retypeNewFields[f.name];
      if (v === null || v === undefined || v === '') {
        newFields[f.name] = null;
      } else if (f.type === 'int') {
        newFields[f.name] = Number(v);
      } else {
        newFields[f.name] = String(v);
      }
    }
    retypeError = null;
    dispatch('retype', {
      ref: selected.ref,
      newStrategy,
      newSource,
      newFields,
    });
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
        <!-- Identity: read-only for macros + the static form. Atomic
             blocks gain a retype affordance below (size is structural
             → §16.2.5). -->
        <dt>Strategy</dt>
        <dd data-testid="detail-strategy">{detail.block.strategy}</dd>

        <dt>Source</dt>
        <dd data-testid="detail-source">{detail.block.source}</dd>

        <dt>Size</dt>
        <dd>{detail.block.size}</dd>

        <!-- §16.2.4 c2: §16.2.3 listed each engine block field
             inline in this dl for MACROS only; the §16.2.4 macro
             edit form below handles the same fields via the same
             EDITABLE_FIELDS table the atomic path uses, so we don't
             need the read-only display anymore. -->
      {:else}
        <dt>Status</dt>
        <dd class="warn">
          Engine block not found at this address — the sidecar may
          reference a slot the template no longer has.
        </dd>
      {/if}
    </dl>

    <!-- §16.2.3 c1 + §16.2.4 c2: edit form serves atomic blocks AND
         macros (macros: edit-once-applies-to-all per D1). -->
    {#if detail.block}
      <form class="edit-form" data-testid="edit-form" on:submit|preventDefault={onSave}>
        <h3>Parameters</h3>

        {#if detail.kind === 'macro'}
          <p
            class="shared-warning macro-warning"
            data-testid="macro-fanout-warning"
          >
            Saving here applies to <strong>all
            {detail.macro?.repeat ?? '?'} expanded blocks</strong>
            of this macro (edit-once-applies-to-all).
          </p>
        {:else if isRowTemplateEdit}
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
              {:else if f.type === 'string_list'}
                <!-- §16.2.4 D3: chip-multi for per-block
                     allowlist_sources (or any future string[] field).
                     Suggestions filter to the spec's named role
                     (today: reference). -->
                <ChipMultiInput
                  values={asStringArray(editValues[f.name])}
                  suggestions={f.sourceRoleFilter === 'reference'
                    ? referenceSources
                    : template.sources}
                  testIdPrefix="input-{f.name}"
                  inputLabel={f.label}
                  placeholder="Type a {f.sourceRoleFilter ?? 'source'} name + Enter"
                  on:change={(e) => (editValues[f.name] = e.detail)}
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

    <!-- §16.2.4 D2: retype panel — atomic blocks only. Collapsed by
         default; toggling reveals strategy + source dropdowns with
         D4 bidirectional role filtering. -->
    {#if detail.kind === 'atomic' && detail.block}
      <section class="retype" data-testid="retype-panel">
        <button
          type="button"
          class="retype-toggle"
          data-testid="retype-toggle"
          on:click={() => (retypeOpen = !retypeOpen)}
          aria-expanded={retypeOpen}
        >
          {retypeOpen ? '▾' : '▸'} Change strategy / source
        </button>

        {#if retypeOpen}
          <div class="retype-body">
            <label class="field" data-testid="retype-strategy-field">
              <span class="field-label">Strategy</span>
              <select
                data-testid="retype-strategy"
                bind:value={retypeStrategy}
              >
                {#each strategyChoices as s (s)}
                  <option value={s}>{s}</option>
                {/each}
              </select>
            </label>

            <label class="field" data-testid="retype-source-field">
              <span class="field-label">Source</span>
              <select
                data-testid="retype-source"
                bind:value={retypeSource}
              >
                {#each sourceChoices as src (src.name)}
                  <option value={src.name}>{src.name} ({src.role})</option>
                {/each}
              </select>
            </label>

            {#if retypeRequiredNew.length > 0}
              <p class="hint" data-testid="retype-required-hint">
                The new strategy requires:
              </p>
              {#each retypeRequiredNew as f (f.name)}
                <label class="field" data-testid="retype-newfield-{f.name}">
                  <span class="field-label">{f.label}</span>
                  {#if f.type === 'int'}
                    <input
                      type="number"
                      min={f.min ?? undefined}
                      data-testid="retype-input-{f.name}"
                      bind:value={retypeNewFields[f.name]}
                    />
                  {:else}
                    <input
                      type="text"
                      data-testid="retype-input-{f.name}"
                      bind:value={retypeNewFields[f.name]}
                    />
                  {/if}
                </label>
              {/each}
            {/if}

            {#if retypeError}
              <p class="field-error" data-testid="retype-error">{retypeError}</p>
            {/if}

            <button
              type="button"
              class="save"
              data-testid="retype-apply"
              disabled={!retypeDirty || !retypeNewFieldsValid}
              on:click={onRetype}
            >
              Retype
            </button>
          </div>
        {/if}
      </section>
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

  /* §16.2.4 c2 — retype panel */
  .retype {
    margin-top: 1rem;
    border-top: 1px solid var(--border, #eee);
    padding-top: 0.75rem;
  }
  .retype-toggle {
    background: transparent;
    border: none;
    cursor: pointer;
    color: var(--text-muted, #555);
    font-size: 0.88rem;
    padding: 0;
    font-weight: 500;
  }
  .retype-body {
    margin-top: 0.6rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .hint {
    color: var(--text-muted, #666);
    font-size: 0.78rem;
    margin: 0.2rem 0 0;
  }
  .macro-warning {
    background: #eaf6f6;
    border-color: #80cbc4;
  }
</style>
