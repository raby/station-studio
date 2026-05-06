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
  import LayoutFieldset from './LayoutFieldset.svelte';

  // ---- props ------------------------------------------------------------

  /** Drives slug-readonly + button labels. 'create' for /stations/new. */
  export let mode: 'create' | 'edit' = 'create';

  /** Pre-populates the form when mode='edit'. Ignored when mode='create'. */
  export let initial: {
    slug: string;
    name: string;
    template: Record<string, unknown>;
  } | null = null;

  /**
   * Caller's submit handler. Receives the validated payload; throws an
   * ApiError on backend failure (caught here and rendered inline).
   */
  export let onSubmit: (payload: {
    slug: string;
    name: string;
    template: Record<string, unknown>;
  }) => Promise<void>;

  /** Visible target on the cancel link. Sensible defaults per mode. */
  export let cancelHref: string = mode === 'edit' && initial
    ? `/stations/${encodeURIComponent(initial.slug)}`
    : '/';

  // Hydration sentinel for Playwright — `bind:value` only wires up after
  // onMount, so specs that fill fields before hydration see stale state.
  // The Playwright spec waits on this attribute (see §14.8 lessons).
  onMount(() => {
    document.documentElement.setAttribute('data-wizard-ready', '');
  });

  // ---- constants duplicated from engine/template.py ---------------------
  // Keep in sync. When these multiply we'll expose a GET /api/meta endpoint.
  const RNG_OPTIONS = ['off', 'on'] as const;
  const ROLE_OPTIONS = ['content', 'reference'] as const;
  // Engine currently only exercises 'weekly'. Listed for forward-compat.
  const CADENCE_OPTIONS = ['weekly'] as const;

  type Rng = (typeof RNG_OPTIONS)[number];
  type Role = (typeof ROLE_OPTIONS)[number];
  type Strategy = 'pick' | 'window';
  type RowBlock = { source: string; strategy: Strategy; size: number };
  type Source = { name: string; colour: string; role: Role };

  // ---- form state -------------------------------------------------------
  // Initialised from `initial` if edit mode supplies it (commit 2 of §16.7);
  // otherwise the create-mode defaults match the original wizard exactly.

  let slug = '';
  let name = '';
  let cadence: (typeof CADENCE_OPTIONS)[number] = 'weekly';
  let rng: Rng = 'off';
  let seedStr = ''; // string so we can tell empty from 0

  let sources: Source[] = [
    { name: '', colour: '#c0392b', role: 'content' },
  ];

  let baseAdvancePerWeek = 0;
  let cycleWeeks = 1;

  let unit = 'day';
  let unitLabelsStr = 'Mon, Tue, Wed, Thu, Fri, Sat, Sun';
  let rowStartOffsetsStr = '0, 0, 0, 0, 0, 0, 0';

  let rowTemplate: RowBlock[] = [
    { source: '', strategy: 'pick', size: 1 },
  ];

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
</script>

{#if error}
  <p class="error">{error}</p>
{/if}

<form on:submit|preventDefault={handleSubmit}>
  <!-- ---- identity ----------------------------------------------- -->
  <fieldset>
    <legend>Identity</legend>

    <label>
      <span>Slug</span>
      <input bind:value={slug} placeholder="rebirth-classics" autocomplete="off" />
      <small>lowercase, digits, dashes. Used in URLs and filenames.</small>
    </label>

    <label>
      <span>Display name</span>
      <input bind:value={name} placeholder="Rebirth Classics" autocomplete="off" />
    </label>

    <label>
      <span>Cadence</span>
      <select bind:value={cadence}>
        {#each CADENCE_OPTIONS as c}
          <option value={c}>{c}</option>
        {/each}
      </select>
    </label>

    <label>
      <span>RNG</span>
      <select bind:value={rng}>
        {#each RNG_OPTIONS as r}
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
                {#each ROLE_OPTIONS as r}
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
  />

  <div class="actions">
    <button type="submit" disabled={submitting}>
      {submitting ? submittingLabel : submitLabel}
    </button>
    <a href={cancelHref} class="ghost-link">cancel</a>
  </div>
</form>

<style>
  .hint { color: #666; font-size: 0.85rem; margin-top: 0; }
  code {
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
    font-size: 0.85em;
  }

  fieldset {
    border: 1px solid #e4e4e4;
    border-radius: 4px;
    padding: 1rem 1.25rem;
    margin: 1rem 0;
  }
  legend {
    font-weight: 600;
    padding: 0 0.35rem;
  }

  label {
    display: block;
    margin: 0.6rem 0;
  }
  label > span {
    display: inline-block;
    min-width: 12rem;
    font-size: 0.9rem;
    color: #333;
    vertical-align: middle;
  }
  label small {
    display: block;
    color: #888;
    font-size: 0.8rem;
    margin-top: 0.15rem;
    margin-left: 12rem;
  }
  input:not([type]), input[type='number'], select {
    padding: 0.3rem 0.5rem;
    font: inherit;
    border: 1px solid #ccc;
    border-radius: 3px;
    min-width: 18rem;
  }
  input[type='color'] {
    width: 3rem;
    height: 1.8rem;
    padding: 0;
    border: 1px solid #ccc;
    border-radius: 3px;
    vertical-align: middle;
  }

  table.list {
    border-collapse: collapse;
    margin: 0.5rem 0;
    width: 100%;
  }
  table.list th, table.list td {
    padding: 0.3rem 0.5rem;
    text-align: left;
    border-bottom: 1px solid #f0f0f0;
    vertical-align: middle;
  }
  table.list th {
    font-size: 0.8rem;
    color: #666;
    font-weight: 600;
  }
  table.list input,
  table.list select {
    min-width: 0;
    width: 100%;
  }
  table.list td:last-child {
    width: 6rem;
    text-align: right;
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-top: 1.5rem;
  }
  button[type='submit'] {
    padding: 0.5rem 1.1rem;
    background: #2c3e50;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 0.95rem;
    cursor: pointer;
  }
  button[type='submit']:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  button.ghost {
    padding: 0.25rem 0.55rem;
    background: none;
    border: 1px solid #ccc;
    color: #333;
    border-radius: 3px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  button.ghost:hover:not(:disabled) { background: #f4f4f4; }
  button.ghost:disabled { opacity: 0.5; cursor: not-allowed; }
  .ghost-link { color: #666; text-decoration: none; font-size: 0.9rem; }
  .ghost-link:hover { text-decoration: underline; }

  .error {
    color: crimson;
    background: #fff5f5;
    border: 1px solid #fed7d7;
    padding: 0.5rem 0.75rem;
    border-radius: 4px;
  }
</style>
