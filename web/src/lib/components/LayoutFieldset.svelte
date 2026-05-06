<!--
  LayoutFieldset — the wizard's "Layout" section, extracted as a standalone
  component so both /stations/new and /stations/[slug]/edit can render it
  without duplication (DESIGN.md §16.7 D10).

  This is also the §16.2 swap-point. When the visual block editor (drag-drop
  layout) lands, this whole component is replaced by <VisualBlockEditor>
  in <StationForm>; both create and edit pages get the new UX from one
  swap rather than touching every consumer.

  State convention: parent owns the values, this component renders + emits
  via two-way `bind:` props. Keeps state co-located with the rest of the
  StationForm payload-builder.
-->
<script lang="ts">
  type Strategy = 'pick' | 'window';
  type RowBlock = { source: string; strategy: Strategy; size: number };

  // Two-way bindings — parent owns the values.
  export let unit: string = 'day';
  export let unitLabelsStr: string = 'Mon, Tue, Wed, Thu, Fri, Sat, Sun';
  export let rowStartOffsetsStr: string = '0, 0, 0, 0, 0, 0, 0';
  export let rowTemplate: RowBlock[] = [
    { source: '', strategy: 'pick', size: 1 },
  ];

  // One-way: derived from the StationForm's `sources` list. Only content-
  // role pools can fill layout blocks (enforced server-side in
  // engine/template.py). Empty when no content sources exist yet.
  export let contentSourceNames: string[] = [];

  // STRATEGY_OPTIONS duplicated from engine/template.py — keep in sync.
  // When this list grows again we'll expose GET /api/meta.
  const STRATEGY_OPTIONS = ['pick', 'window'] as const;

  function addBlock() {
    rowTemplate = [
      ...rowTemplate,
      { source: contentSourceNames[0] ?? '', strategy: 'pick', size: 1 },
    ];
  }
  function removeBlock(i: number) {
    rowTemplate = rowTemplate.filter((_, idx) => idx !== i);
  }
</script>

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
            <select bind:value={b.source} data-testid="block-source">
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

<style>
  /*
    Styles are duplicated from StationForm's <style> block. Svelte's scoped
    styles add a per-component hash to selectors, so a `fieldset {...}` rule
    in StationForm wouldn't reach this component's <fieldset>. Copying the
    relevant rules here keeps the visual identical to the wizard pre-extraction.

    The rules are deliberately identical — if you tweak them in
    StationForm.svelte, mirror the change here too. (When the visual block
    editor lands per §16.2 D10 this whole component goes away anyway.)
  */
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
</style>
