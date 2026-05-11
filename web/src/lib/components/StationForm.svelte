<!--
  StationForm — the wizard's form, extracted as a standalone component
  so /stations/new (POST) and /stations/[slug]/edit (PUT) can both use
  it (DESIGN.md §16.7 D2).

  Responsibility split:
    - This component owns form state (let bindings) + the Svelte-specific
      wiring (reactives, bind, onMount). Pure-function logic — buildTemplate,
      unpack, defaults, preSubmitProblem — lives in $lib/template (§14.20).
    - The parent owns the API call (POST vs PUT), the navigation
      destination, and any post-success behaviour.

  Edit-mode wiring (mode='edit', `initial` populated) lands as a
  follow-on commit per §16.7 commit 2; this commit is a pure refactor
  of the create-mode wizard with no behaviour change.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { ApiError } from '$lib/api';
  import type { Meta } from '$lib/api';
  import { templateDiff, isClean } from '$lib/templateDiff';
  import LayoutFieldset from './LayoutFieldset.svelte';
  import {
    defaults,
    unpack,
    buildTemplate,
    preSubmitProblem,
  } from '$lib/template';
  import type {
    FormState,
    StationFormPayload,
    RowBlock,
    Source,
  } from '$lib/template';

  // Shared form rules for both this component and <LayoutFieldset>. See
  // _form-shared.css for the full rationale. The rules are scoped under
  // `.station-form`, so the wrapper div around the <form> is load-bearing.
  import './_form-shared.css';

  // ---- props ------------------------------------------------------------

  /** Drives slug-readonly + button labels. 'create' for /stations/new. */
  export let mode: 'create' | 'edit' = 'create';

  /** Pre-populates the form when mode='edit'. Ignored when mode='create'. */
  export let initial: StationFormPayload | null = null;

  /**
   * Caller's submit handler. Receives the validated payload; throws an
   * ApiError on backend failure (caught here and rendered inline).
   */
  export let onSubmit: (payload: StationFormPayload) => Promise<void>;

  /** Visible target on the cancel link. Sensible defaults per mode. */
  export let cancelHref: string = mode === 'edit' && initial
    ? `/stations/${encodeURIComponent(initial.slug)}`
    : '/';

  /**
   * Canonical option lists for the form's dropdowns. Comes from
   * `GET /api/meta` (DESIGN.md §14.19) so the dropdowns can't drift
   * from what `engine/template.py` accepts. Parent route is responsible
   * for fetching this (typically via `loadMeta()` from `$lib/meta`)
   * before mounting <StationForm>.
   *
   * The `Meta` type is imported from $lib/api so this file and api.ts
   * share one definition (post-§14.19 review N1 — was inline before).
   */
  export let meta: Meta;

  // Hydration sentinel for Playwright — `bind:value` only wires up after
  // onMount, so specs that fill fields before hydration see stale state.
  // The Playwright spec waits on this attribute (see §14.8 lessons).
  onMount(() => {
    document.documentElement.setAttribute('data-wizard-ready', '');
  });

  // ---- form state -------------------------------------------------------
  // For edit mode, populate from `initial.template` via the unpack helper
  // from $lib/template; for create mode, fall back to defaults. Initialiser
  // runs once at component mount; subsequent bindings come from the form
  // inputs themselves.

  // Initial state — `initial` is consumed once at mount; the form is the
  // source of truth thereafter.
  const _init = initial ? unpack(initial) : defaults();

  let slug = _init.slug;
  let name = _init.name;
  let cadence = _init.cadence;
  let rng = _init.rng;
  let seedStr = _init.seedStr;
  let sources: Source[] = _init.sources;
  let baseAdvancePerWeek = _init.baseAdvancePerWeek;
  let cycleWeeks = _init.cycleWeeks;
  let unit = _init.unit;
  let unitLabels = _init.unitLabels;
  let rowStartOffsets = _init.rowStartOffsets;
  let rowTemplate: RowBlock[] = _init.rowTemplate;

  // Only content sources can fill layout blocks (enforced server-side).
  $: contentSourceNames = sources
    .filter((s) => s.role === 'content' && s.name.trim() !== '')
    .map((s) => s.name.trim());

  let submitting = false;
  let error: string | null = null;

  // ---- list ops ---------------------------------------------------------

  function addSource() {
    sources = [...sources, { name: '', colour: '#888888', role: 'content' }];
  }
  function removeSource(i: number) {
    sources = sources.filter((_, idx) => idx !== i);
  }

  // ---- helpers bridging form variables → FormState for pure functions ---

  /** Snapshot the current form variables into a FormState struct. */
  function currentFormState(): FormState {
    return {
      slug, name, cadence, rng, seedStr, sources,
      baseAdvancePerWeek, cycleWeeks, unit,
      unitLabels, rowStartOffsets, rowTemplate,
    };
  }

  // ---- submit -----------------------------------------------------------

  async function handleSubmit() {
    error = null;
    const state = currentFormState();
    const p = preSubmitProblem(state);
    if (p) {
      error = p;
      return;
    }
    submitting = true;
    try {
      const template = buildTemplate(state);
      await onSubmit({ slug: slug.trim(), name: name.trim(), template });
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      submitting = false;
    }
  }

  // Button label respects mode but stays overridable. "Create station" /
  // "Save changes" are the natural defaults for the two consumers.
  $: submitLabel = mode === 'edit' ? 'Save changes' : 'Create station';
  $: submittingLabel = mode === 'edit' ? 'Saving…' : 'Creating…';

  // ---- Reactive FormState snapshot ------------------------------------
  // IMPORTANT: Svelte 4's $: dependency tracker introspects the AST of
  // the reactive block, NOT the bodies of functions it calls (see §14.15
  // trap-doc). We reference all 12 form variables inline in ONE IIFE so
  // the compiler sees every dependency. Downstream $: blocks that read
  // `formState` re-fire automatically — Svelte tracks reactive-to-reactive
  // deps without the IIFE trick, so the duplication stays at 1 copy.
  //
  // Review N2 (§14.20): previously 3 copies of the inline construction;
  // consolidated here.

  $: formState = (() => {
    const s: FormState = {
      slug, name, cadence, rng, seedStr, sources,
      baseAdvancePerWeek, cycleWeeks, unit,
      unitLabels, rowStartOffsets, rowTemplate,
    };
    return s;
  })();

  // ---- Live JSON preview (D6) ------------------------------------------
  // Re-derived on every form-state change; renders in a <details>
  // disclosure. Cheap — buildTemplate() is O(form-size).

  $: previewJSON = JSON.stringify(buildTemplate(formState), null, 2);

  // ---- Diff view (D7, edit-mode only) ----------------------------------
  // Compares the live form state against the originally loaded template.
  // The diff is structured (Sections × Changes); render iterates.

  $: liveStation = {
    slug: formState.slug.trim() || (initial?.slug ?? ''),
    name: formState.name.trim() || (initial?.name ?? ''),
    template: buildTemplate(formState),
  };
  $: diff = mode === 'edit' && initial ? templateDiff(initial, liveStation) : null;
  $: dirty = diff ? !isClean(diff) : false;

  // ---- Reset to saved (D8, edit-mode only) -----------------------------

  function resetToSaved() {
    if (!initial) return;
    if (dirty && !confirm('Discard changes and reset to the saved template?')) return;
    // structuredClone deep-copies — Object spread / Array map alone leaves
    // nested arrays/objects shared with `initial`, so subsequent edits
    // would mutate the original (caught me once during dev).
    const reset = unpack(structuredClone(initial));
    slug = reset.slug;
    name = reset.name;
    cadence = reset.cadence;
    rng = reset.rng;
    seedStr = reset.seedStr;
    sources = reset.sources;
    baseAdvancePerWeek = reset.baseAdvancePerWeek;
    cycleWeeks = reset.cycleWeeks;
    unit = reset.unit;
    unitLabels = reset.unitLabels;
    rowStartOffsets = reset.rowStartOffsets;
    rowTemplate = reset.rowTemplate;
    error = null;
  }
</script>

<!--
  Wrapper is load-bearing — _form-shared.css scopes every rule under
  `.station-form`, so removing this div would silently strip the form's
  styling. <LayoutFieldset> rendered inside this wrapper inherits the
  rules transitively (CSS doesn't care about Svelte's component tree).
-->
<div class="station-form">
  {#if error}
    <p class="error">{error}</p>
  {/if}

  <form on:submit|preventDefault={handleSubmit}>
    <!-- ---- identity ----------------------------------------------- -->
  <fieldset>
    <legend>Identity</legend>

    <label>
      <span>Slug</span>
      <input
        bind:value={slug}
        placeholder="rebirth-classics"
        autocomplete="off"
        readonly={mode === 'edit'}
        class:readonly={mode === 'edit'}
      />
      {#if mode === 'edit'}
        <small>
          Slug is immutable — create a new station to use a different one.
          (It's embedded in iTunes Persistent IDs and disk paths; changing it
          would either break your iTunes library or require coordinated migration.)
        </small>
      {:else}
        <small>lowercase, digits, dashes. Used in URLs and filenames.</small>
      {/if}
    </label>

    <label>
      <span>Display name</span>
      <input bind:value={name} placeholder="Rebirth Classics" autocomplete="off" />
    </label>

    <label>
      <span>Cadence</span>
      <select bind:value={cadence}>
        {#each meta.cadences as c}
          <option value={c}>{c}</option>
        {/each}
      </select>
    </label>

    <label>
      <span>RNG</span>
      <select bind:value={rng}>
        {#each meta.rng as r}
          <option value={r}>{r}</option>
        {/each}
      </select>
      <small>'off' = deterministic; 'on' = seeded random picks.</small>
    </label>

    {#if rng === 'on'}
      <label>
        <span>Seed (optional)</span>
        <input type="number" bind:value={seedStr} placeholder="leave blank for per-run seed" />
      </label>
    {/if}
  </fieldset>

  <!-- ---- sources ----------------------------------------------- -->
  <fieldset>
    <legend>Sources</legend>
    <p class="hint">
      Imported iTunes-XML pools this station draws from. 'content' pools feed
      layout blocks; 'reference' pools are available to the engine but
      aren't placed directly (e.g. the dedup <code>RG_MASTER</code> in Classics).
    </p>

    <table class="list">
      <thead>
        <tr><th>Name</th><th>Colour</th><th>Role</th><th></th></tr>
      </thead>
      <tbody>
        {#each sources as s, i}
          <tr>
            <td><input bind:value={s.name} placeholder="RG_RADIO" autocomplete="off" /></td>
            <td><input type="color" bind:value={s.colour} /></td>
            <td>
              <select bind:value={s.role}>
                {#each meta.roles as r}
                  <option value={r}>{r}</option>
                {/each}
              </select>
            </td>
            <td>
              <button type="button" class="ghost" on:click={() => removeSource(i)}
                      disabled={sources.length === 1}>remove</button>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
    <button type="button" class="ghost" on:click={addSource}>+ add source</button>
  </fieldset>

  <!-- ---- cursor ------------------------------------------------ -->
  <fieldset>
    <legend>Cursor</legend>
    <p class="hint">
      The engine advances <code>base_advance_per_week</code> positions into each content pool per week,
      wrapping every <code>cycle_weeks</code>. Classics uses 60 / 16.
    </p>
    <label>
      <span>base_advance_per_week</span>
      <input type="number" bind:value={baseAdvancePerWeek} min="0" />
    </label>
    <label>
      <span>cycle_weeks</span>
      <input type="number" bind:value={cycleWeeks} min="1" />
    </label>
  </fieldset>

  <!-- ---- layout ------------------------------------------------ -->
  <LayoutFieldset
    bind:unit
    bind:unitLabels
    bind:rowStartOffsets
    bind:rowTemplate
    {contentSourceNames}
    strategies={meta.strategies}
  />

  <!-- ---- diff (edit mode only) -------------------------------- -->
  {#if mode === 'edit' && diff}
    <details class="disclosure" data-testid="diff-disclosure">
      <summary>
        what's changing
        {#if dirty}
          <span class="dirty-pill">unsaved</span>
        {:else}
          <span class="clean-pill">no changes</span>
        {/if}
      </summary>
      <div class="disclosure-body">
        {#if !dirty}
          <p class="hint">No changes yet — everything matches the saved template.</p>
        {:else}
          {#each diff as section}
            <h4>{section.name}</h4>
            {#if section.changes.length === 0}
              <p class="unchanged">unchanged</p>
            {:else}
              <dl class="diff-list">
                {#each section.changes as c}
                  <dt>{c.label}</dt>
                  <dd>
                    <span class="before">{c.before}</span>
                    <span class="arrow">→</span>
                    <span class="after">{c.after}</span>
                  </dd>
                {/each}
              </dl>
            {/if}
          {/each}
        {/if}
      </div>
    </details>
  {/if}

  <!-- ---- live JSON preview (both modes) -------------------------- -->
  <details class="disclosure" data-testid="json-preview">
    <summary>JSON preview (what will be saved)</summary>
    <div class="disclosure-body">
      <pre class="json">{previewJSON}</pre>
    </div>
  </details>

  <div class="actions">
    <button type="submit" disabled={submitting}>
      {submitting ? submittingLabel : submitLabel}
    </button>
    {#if mode === 'edit'}
      <button
        type="button"
        class="ghost"
        data-testid="reset-to-saved"
        on:click={resetToSaved}
        disabled={submitting || !dirty}
      >
        Reset to saved
      </button>
    {/if}
    <a href={cancelHref} class="ghost-link">cancel</a>
  </div>
</form>
</div>

<style>
  /*
    Most form rules live in _form-shared.css under `.station-form` scoping
    so <LayoutFieldset> can reuse them without per-component duplication.
    Only the rules unique to <StationForm> (action area, submit button,
    error banner, disclosures, diff list, pills) live here. They're scoped
    via the global wrapper's class so they don't leak.
  */

  /* The submit button is only on <StationForm>, not in <LayoutFieldset>. */
  :global(.station-form button[type='submit']) {
    padding: 0.5rem 1.1rem;
    background: #2c3e50;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 0.95rem;
    cursor: pointer;
  }
  :global(.station-form button[type='submit']:disabled) {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-top: 1.5rem;
  }
  .ghost-link { color: #666; text-decoration: none; font-size: 0.9rem; }
  .ghost-link:hover { text-decoration: underline; }

  .error {
    color: crimson;
    background: #fff5f5;
    border: 1px solid #fed7d7;
    padding: 0.5rem 0.75rem;
    border-radius: 4px;
  }

  /* ---- disclosures (JSON preview + diff) ---------------------------- */
  .disclosure {
    border: 1px solid #e4e4e4;
    border-radius: 4px;
    margin: 1rem 0;
    background: #fafafa;
  }
  .disclosure summary {
    padding: 0.5rem 0.75rem;
    cursor: pointer;
    user-select: none;
    font-size: 0.9rem;
  }
  .disclosure summary:hover { background: #f0f0f0; }
  .disclosure-body {
    padding: 0.5rem 0.75rem;
    border-top: 1px solid #e4e4e4;
    background: white;
  }
  .disclosure-body h4 {
    margin: 0.75rem 0 0.25rem;
    font-size: 0.85rem;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .disclosure-body h4:first-child { margin-top: 0; }
  .disclosure-body .unchanged { color: #888; font-style: italic; margin: 0 0 0.5rem; }

  .json {
    margin: 0;
    padding: 0.75rem;
    background: #fafafa;
    border: 1px solid #e4e4e4;
    border-radius: 3px;
    font-family: ui-monospace, monospace;
    font-size: 0.8rem;
    white-space: pre;
    overflow-x: auto;
  }

  /* ---- diff list --------------------------------------------------- */
  .diff-list {
    display: grid;
    grid-template-columns: max-content 1fr;
    column-gap: 1rem;
    row-gap: 0.2rem;
    margin: 0 0 0.5rem;
    font-size: 0.85rem;
    font-family: ui-monospace, monospace;
  }
  .diff-list dt {
    color: #555;
    white-space: nowrap;
  }
  .diff-list dd {
    margin: 0;
    word-break: break-all;
  }
  .diff-list .before {
    color: #b71c1c;
    text-decoration: line-through;
  }
  .diff-list .arrow {
    color: #666;
    margin: 0 0.4rem;
  }
  .diff-list .after {
    color: #1b5e20;
  }

  /* ---- pills next to "what's changing" ----------------------------- */
  .dirty-pill, .clean-pill {
    display: inline-block;
    margin-left: 0.4rem;
    padding: 0.05em 0.5em;
    border-radius: 999px;
    font-size: 0.7rem;
    font-family: ui-monospace, monospace;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .dirty-pill {
    background: #fff5f5;
    color: #b71c1c;
    border: 1px solid #fed7d7;
  }
  .clean-pill {
    background: #f4f4f4;
    color: #888;
    border: 1px solid #e4e4e4;
  }
</style>
