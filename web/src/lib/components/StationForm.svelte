<!--
  StationForm — the wizard's form, extracted as a standalone component
  so /stations/new (POST) and /stations/[slug]/edit (PUT) can both use
  it (DESIGN.md §16.7 D2).

  Responsibility split:
    - This component owns form state + client-side validation + the
      build-template-from-state translation. It calls `onSubmit(payload)`
      after validation passes.
    - The parent owns the API call (POST vs PUT), the navigation
      destination, and any post-success behaviour.

  Edit-mode wiring (mode='edit', `initial` populated) lands as a
  follow-on commit per §16.7 commit 2; this commit is a pure refactor
  of the create-mode wizard with no behaviour change.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { ApiError } from '$lib/api';
  import { templateDiff, isClean } from '$lib/templateDiff';
  import type { Section as DiffSection } from '$lib/templateDiff';
  import LayoutFieldset from './LayoutFieldset.svelte';

  // Shared form rules for both this component and <LayoutFieldset>. See
  // _form-shared.css for the full rationale. The rules are scoped under
  // `.station-form`, so the wrapper div around the <form> is load-bearing.
  import './_form-shared.css';

  // ---- props ------------------------------------------------------------

  /**
   * The slug + name + template-blob shape used both for the prop input
   * (loaded via getStation) and the submit-handler output (built from
   * the form). Matches `StationCreatePayload` from $lib/api.
   *
   * Not exported — `export type` is rejected from Svelte 4's instance
   * <script>. Consumers get the shape via inference on `initial` /
   * `onSubmit`, which is enough for both call-sites.
   */
  type StationFormPayload = {
    slug: string;
    name: string;
    template: Record<string, unknown>;
  };

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
   * The `Meta` type lives in $lib/api; mirroring the prop type avoids
   * an import here that would only be needed for the type annotation.
   */
  export let meta: {
    strategies: string[];
    rng: string[];
    roles: string[];
    cadences: string[];
  };

  // Hydration sentinel for Playwright — `bind:value` only wires up after
  // onMount, so specs that fill fields before hydration see stale state.
  // The Playwright spec waits on this attribute (see §14.8 lessons).
  onMount(() => {
    document.documentElement.setAttribute('data-wizard-ready', '');
  });

  // Strategy / Role / Rng types are kept inline as string-literal unions
  // because Svelte 4 type-narrowing across <select bind:value> works
  // best with literal types rather than `string`. The runtime *values*
  // come from `meta` (server-validated), but the types stay tight.
  type Rng = 'off' | 'on';
  type Role = 'content' | 'reference';
  type Strategy = 'pick' | 'window';
  type RowBlock = { source: string; strategy: Strategy; size: number };
  type Source = { name: string; colour: string; role: Role };

  // ---- form state -------------------------------------------------------
  // For edit mode, populate from `initial.template` via the unpack helper
  // below; for create mode, fall back to defaults that match the original
  // wizard exactly. Initialiser runs once at component mount; subsequent
  // bindings come from the form inputs themselves.

  type FormState = {
    slug: string;
    name: string;
    // Cadence is open server-side (see §14.19 / SUGGESTED_CADENCES); the
    // dropdown's options come from `meta.cadences` at render time.
    cadence: string;
    rng: Rng;
    seedStr: string;
    sources: Source[];
    baseAdvancePerWeek: number;
    cycleWeeks: number;
    unit: string;
    unitLabelsStr: string;
    rowStartOffsetsStr: string;
    rowTemplate: RowBlock[];
  };

  function defaults(): FormState {
    return {
      slug: '',
      name: '',
      cadence: 'weekly',
      rng: 'off',
      seedStr: '',
      sources: [{ name: '', colour: '#c0392b', role: 'content' }],
      baseAdvancePerWeek: 0,
      cycleWeeks: 1,
      unit: 'day',
      unitLabelsStr: 'Mon, Tue, Wed, Thu, Fri, Sat, Sun',
      rowStartOffsetsStr: '0, 0, 0, 0, 0, 0, 0',
      rowTemplate: [{ source: '', strategy: 'pick', size: 1 }],
    };
  }

  /**
   * Convert a stored template (the `Record<string, unknown>` blob from the
   * API) back into the form-shaped FormState. The reverse of buildTemplate().
   * Defensive: any missing field falls back to its default so a malformed
   * stored template doesn't blank the whole form.
   */
  function unpack(initial: StationFormPayload): FormState {
    const t = initial.template as Record<string, unknown>;
    const cursor = (t.cursor as Record<string, unknown>) ?? {};
    const layout = (t.layout as Record<string, unknown>) ?? {};
    const tplSources = (t.sources as Source[] | undefined) ?? [];
    const tplRowTemplate = (layout.row_template as RowBlock[] | undefined) ?? [];
    const tplUnitLabels = (layout.unit_labels as string[] | undefined) ?? [];
    const tplOffsets = (layout.row_start_offsets as number[] | undefined) ?? [];

    const d = defaults();
    return {
      slug: initial.slug,
      name: initial.name,
      cadence: (t.cadence as FormState['cadence']) ?? d.cadence,
      rng: (t.rng as Rng) ?? d.rng,
      seedStr: t.seed != null ? String(t.seed) : '',
      sources: tplSources.length > 0
        ? tplSources.map((s) => ({ name: s.name, colour: s.colour, role: s.role }))
        : d.sources,
      baseAdvancePerWeek:
        Number(cursor.base_advance_per_week) || d.baseAdvancePerWeek,
      cycleWeeks: Number(cursor.cycle_weeks) || d.cycleWeeks,
      unit: (layout.unit as string) ?? d.unit,
      unitLabelsStr:
        tplUnitLabels.length > 0 ? tplUnitLabels.join(', ') : d.unitLabelsStr,
      rowStartOffsetsStr:
        tplOffsets.length > 0 ? tplOffsets.join(', ') : d.rowStartOffsetsStr,
      rowTemplate: tplRowTemplate.length > 0
        ? tplRowTemplate.map((b) => ({ source: b.source, strategy: b.strategy, size: b.size }))
        : d.rowTemplate,
    };
  }

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
  let unitLabelsStr = _init.unitLabelsStr;
  let rowStartOffsetsStr = _init.rowStartOffsetsStr;
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

  // ---- client-side sanity check -----------------------------------------
  // Server-side validation via engine.template.parse_template is the
  // source of truth. These are the few things we can catch pre-submit to
  // avoid an obviously-doomed round trip.

  function preSubmitProblem(): string | null {
    if (!slug.trim()) return 'slug is required';
    if (!/^[a-z0-9][a-z0-9-]*$/.test(slug.trim()))
      return 'slug must be lowercase, digits, or dashes';
    if (!name.trim()) return 'display name is required';
    if (sources.length === 0) return 'at least one source is required';
    if (sources.some((s) => !s.name.trim())) return 'all sources need a name';
    if (contentSourceNames.length === 0)
      return 'at least one content source is required';
    if (rowTemplate.length === 0)
      return 'at least one row-template block is required';
    if (rowTemplate.some((b) => !b.source))
      return 'each block must reference a source';
    return null;
  }

  // ---- submit -----------------------------------------------------------

  function parseIntList(s: string): number[] {
    return s
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean)
      .map((x) => Number(x))
      .filter((n) => Number.isFinite(n));
  }
  function parseStringList(s: string): string[] {
    return s
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);
  }

  function buildTemplate() {
    const t: Record<string, unknown> = {
      station: slug.trim(),
      name: name.trim(),
      cadence,
      rng,
      sources: sources.map((s) => ({
        name: s.name.trim(),
        colour: s.colour,
        role: s.role,
      })),
      cursor: {
        base_advance_per_week: baseAdvancePerWeek,
        cycle_weeks: cycleWeeks,
      },
      layout: {
        unit,
        unit_labels: parseStringList(unitLabelsStr),
        row_start_offsets: parseIntList(rowStartOffsetsStr),
        row_template: rowTemplate.map((b) => ({
          source: b.source,
          strategy: b.strategy,
          size: b.size,
        })),
      },
    };
    // Omit seed when RNG is off (cleaner JSON; engine tolerates either).
    if (rng === 'on' && seedStr.trim() !== '') {
      t['seed'] = Number(seedStr);
    }
    return t;
  }

  async function handleSubmit() {
    error = null;
    const p = preSubmitProblem();
    if (p) {
      error = p;
      return;
    }
    submitting = true;
    try {
      const template = buildTemplate();
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

  // ---- Live JSON preview (D6) ------------------------------------------
  // Re-derived on every form-state change; renders in a <details>
  // disclosure. Cheap — buildTemplate() is O(form-size).

  $: previewJSON = JSON.stringify(buildTemplate(), null, 2);

  // ---- Diff view (D7, edit-mode only) ----------------------------------
  // Compares the live form state against the originally loaded template.
  // The diff is structured (Sections × Changes); render iterates.

  $: liveStation = {
    slug: slug.trim() || (initial?.slug ?? ''),
    name: name.trim() || (initial?.name ?? ''),
    template: buildTemplate(),
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
    unitLabelsStr = reset.unitLabelsStr;
    rowStartOffsetsStr = reset.rowStartOffsetsStr;
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
    bind:unitLabelsStr
    bind:rowStartOffsetsStr
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
