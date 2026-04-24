<script lang="ts">
  import { goto } from '$app/navigation';
  import { createStation, ApiError } from '$lib/api';

  // ---- constants duplicated from engine/template.py ---------------------
  // Keep in sync. When these multiply we'll expose a GET /api/meta endpoint.
  const RNG_OPTIONS = ['off', 'on'] as const;
  const STRATEGY_OPTIONS = ['pick', 'window'] as const;
  const ROLE_OPTIONS = ['content', 'reference'] as const;
  // Engine currently only exercises 'weekly'. Listed for forward-compat.
  const CADENCE_OPTIONS = ['weekly'] as const;

  // ---- form state -------------------------------------------------------

  type Rng = (typeof RNG_OPTIONS)[number];
  type Strategy = (typeof STRATEGY_OPTIONS)[number];
  type Role = (typeof ROLE_OPTIONS)[number];

  let slug = '';
  let name = '';
  let cadence: (typeof CADENCE_OPTIONS)[number] = 'weekly';
  let rng: Rng = 'off';
  let seedStr = ''; // string so we can tell empty from 0

  // Sources — start with one empty content pool to make the "add" affordance
  // obvious.
  let sources: Array<{ name: string; colour: string; role: Role }> = [
    { name: '', colour: '#c0392b', role: 'content' },
  ];

  // Cursor — defaults sized for a simple weekly rotation. User overrides.
  let baseAdvancePerWeek = 0;
  let cycleWeeks = 1;

  // Layout. `unit_labels` and `row_start_offsets` are edited as
  // comma-separated strings because <input type="number"> doesn't do arrays
  // and writing a chip-input widget is way out of scope for a first slice.
  let unit = 'day';
  let unitLabelsStr = 'Mon, Tue, Wed, Thu, Fri, Sat, Sun';
  let rowStartOffsetsStr = '0, 0, 0, 0, 0, 0, 0';

  // Row-template blocks. Default to a single block referencing the first
  // source; the source selector updates as the sources list changes.
  let rowTemplate: Array<{ source: string; strategy: Strategy; size: number }> = [
    { source: '', strategy: 'pick', size: 1 },
  ];

  // ---- derived ----------------------------------------------------------

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
  function addBlock() {
    rowTemplate = [
      ...rowTemplate,
      { source: contentSourceNames[0] ?? '', strategy: 'pick', size: 1 },
    ];
  }
  function removeBlock(i: number) {
    rowTemplate = rowTemplate.filter((_, idx) => idx !== i);
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
      await createStation({ slug: slug.trim(), name: name.trim(), template });
      await goto(`/stations/${encodeURIComponent(slug.trim())}`);
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      submitting = false;
    }
  }
</script>

<svelte:head>
  <title>New station — Station Studio</title>
</svelte:head>

<main>
  <p class="crumbs"><a href="/">← stations</a></p>
  <h1>New station</h1>
  <p class="sub">
    Produces a template JSON identical to a checked-in <code>stations/*.json</code>.
    Validation runs server-side against <code>engine.template.parse_template</code>.
  </p>

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
    <fieldset>
      <legend>Layout</legend>
      <p class="hint">
        Each unit (e.g. a day) becomes one row. The row template is applied to
        each row, offset by the corresponding <code>row_start_offsets</code> value.
      </p>
      <label>
        <span>unit</span>
        <input bind:value={unit} placeholder="day" autocomplete="off" />
      </label>
      <label>
        <span>unit_labels</span>
        <input bind:value={unitLabelsStr} placeholder="Mon, Tue, Wed, Thu, Fri, Sat, Sun" autocomplete="off" />
        <small>comma-separated; length must match row_start_offsets.</small>
      </label>
      <label>
        <span>row_start_offsets</span>
        <input bind:value={rowStartOffsetsStr} placeholder="0, 10, 20, 30, 40, 50, 50" autocomplete="off" />
        <small>comma-separated integers. Duplicates intentionally repeat a row.</small>
      </label>

      <h3>Row template (blocks per row)</h3>
      <table class="list">
        <thead>
          <tr><th>Source</th><th>Strategy</th><th>Size</th><th></th></tr>
        </thead>
        <tbody>
          {#each rowTemplate as b, i}
            <tr>
              <td>
                <select bind:value={b.source}>
                  <option value="" disabled>— pick a source —</option>
                  {#each contentSourceNames as sname}
                    <option value={sname}>{sname}</option>
                  {/each}
                </select>
              </td>
              <td>
                <select bind:value={b.strategy}>
                  {#each STRATEGY_OPTIONS as s}
                    <option value={s}>{s}</option>
                  {/each}
                </select>
              </td>
              <td><input type="number" bind:value={b.size} min="1" /></td>
              <td>
                <button type="button" class="ghost" on:click={() => removeBlock(i)}
                        disabled={rowTemplate.length === 1}>remove</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
      <button type="button" class="ghost" on:click={addBlock}>+ add block</button>
    </fieldset>

    <div class="actions">
      <button type="submit" disabled={submitting}>
        {submitting ? 'Creating…' : 'Create station'}
      </button>
      <a href="/" class="ghost-link">cancel</a>
    </div>
  </form>
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 780px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .crumbs {
    font-size: 0.85rem;
    margin-bottom: 1rem;
    color: #666;
  }
  .crumbs a {
    color: #666;
    text-decoration: none;
  }
  .crumbs a:hover { text-decoration: underline; }
  h1 { margin: 0; }
  .sub { color: #666; }
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

  h3 { margin: 1rem 0 0.25rem; font-size: 1rem; }

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
