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
  // Shared form rules from _form-shared.css. <LayoutFieldset> is rendered
  // INSIDE <StationForm>'s `.station-form` wrapper so the rules apply to
  // this component's elements via CSS ancestor matching.
  import './_form-shared.css';

  import type { Meta } from '$lib/api';
  import type { Strategy, RowBlock } from '$lib/template';
  import { parseStringList, parseIntList } from '$lib/template';

  // Two-way bindings — parent owns the values as arrays.
  export let unit: string = 'day';
  /** Array of labels, one per row. */
  export let unitLabels: string[] = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  /** Array of per-row start offsets. */
  export let rowStartOffsets: number[] = [0, 0, 0, 0, 0, 0, 0];
  export let rowTemplate: RowBlock[] = [
    { source: '', strategy: 'pick', size: 1 },
  ];

  // ---- text ↔ array bridging ------------------------------------------
  // The parent binds arrays; the text inputs show comma-separated strings.
  // Two concerns compete:
  //   1. Typing: the user types "Mon, Tu" mid-word — we must NOT snap the
  //      input back to the join of parseStringList("Mon, Tu") because that
  //      would eat trailing commas / whitespace and make typing impossible.
  //   2. External reset: when the parent resets the array (e.g. "Reset to
  //      saved"), the text input must update.
  //
  // Solution: local `let` strings initialised from the array. on:input
  // handlers update BOTH the local string AND the exported array prop.
  // A reactive watches the array prop for external changes (where the
  // array identity changes but doesn't match our last-parsed value) and
  // re-syncs the local string. The identity check prevents the reactive
  // from firing on our own on:input writes.

  let unitLabelsStr = unitLabels.join(', ');
  let rowStartOffsetsStr = rowStartOffsets.join(', ');

  // Track the last array we produced from the text input, so we can
  // distinguish "parent reset the array" from "our own on:input fired".
  let lastParsedLabels = unitLabels;
  let lastParsedOffsets = rowStartOffsets;

  // External reset detection: if the array prop changed and it's NOT
  // the same reference we last produced, re-sync the string.
  $: if (unitLabels !== lastParsedLabels) {
    unitLabelsStr = unitLabels.join(', ');
    lastParsedLabels = unitLabels;
  }
  $: if (rowStartOffsets !== lastParsedOffsets) {
    rowStartOffsetsStr = rowStartOffsets.join(', ');
    lastParsedOffsets = rowStartOffsets;
  }

  function onUnitLabelsInput(e: Event) {
    unitLabelsStr = (e.target as HTMLInputElement).value;
    const parsed = parseStringList(unitLabelsStr);
    unitLabels = parsed;
    lastParsedLabels = parsed;
  }
  function onRowStartOffsetsInput(e: Event) {
    rowStartOffsetsStr = (e.target as HTMLInputElement).value;
    const parsed = parseIntList(rowStartOffsetsStr);
    rowStartOffsets = parsed;
    lastParsedOffsets = parsed;
  }

  // One-way: derived from the StationForm's `sources` list. Only content-
  // role pools can fill layout blocks (enforced server-side in
  // engine/template.py). Empty when no content sources exist yet.
  export let contentSourceNames: string[] = [];

  // Strategy options come from `GET /api/meta` (DESIGN.md §14.19) via
  // <StationForm>'s `meta` prop. The string-literal type stays inline
  // for tight bind-narrowing; the *runtime values* come from this prop.
  // Typed via `Meta['strategies']` rather than `string[]` so a future
  // shape change to Meta surfaces here as a TS error, not a silent
  // mismatch (post-§14.19 review N1+N6). Default falls back to current
  // engine support so the component still renders if a parent forgets
  // to wire `strategies` through.
  export let strategies: Meta['strategies'] = ['pick', 'window'];

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
    <input value={unitLabelsStr} on:input={onUnitLabelsInput} placeholder="Mon, Tue, Wed, Thu, Fri, Sat, Sun" autocomplete="off" />
    <small>comma-separated; length must match row_start_offsets.</small>
  </label>
  <label>
    <span>row_start_offsets</span>
    <input value={rowStartOffsetsStr} on:input={onRowStartOffsetsInput} placeholder="0, 10, 20, 30, 40, 50, 50" autocomplete="off" />
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
          <!-- §16.26 — aria-label per input/select. Same pattern as
               <StationForm>'s sources table: the column <th>s above
               are visual labels, not programmatic, so axe's `label`
               and `select-name` rules fire. -->
          <td>
            <select bind:value={b.source} data-testid="block-source"
                    aria-label="Block {i + 1} source">
              <option value="" disabled>— pick a source —</option>
              {#each contentSourceNames as sname}
                <option value={sname}>{sname}</option>
              {/each}
            </select>
          </td>
          <td>
            <select bind:value={b.strategy} aria-label="Block {i + 1} strategy">
              {#each strategies as s}
                <option value={s}>{s}</option>
              {/each}
            </select>
          </td>
          <td><input type="number" bind:value={b.size} min="1"
                     aria-label="Block {i + 1} size" /></td>
          <td>
            <button type="button" class="ghost" on:click={() => removeBlock(i)}
                    disabled={rowTemplate.length === 1}
                    aria-label="Remove block {i + 1}">remove</button>
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
  <button type="button" class="ghost" on:click={addBlock}>+ add block</button>
</fieldset>

<!--
  No <style> block — every rule that styled this fieldset previously
  was duplicated from <StationForm>'s <style>. They now live in
  _form-shared.css under `.station-form` scoping (the wrapper div in
  <StationForm>) and apply to this component's elements transitively.
  See the import in <script> above.

  If you ever render <LayoutFieldset> outside <StationForm>, you'll
  need either to add the wrapper class manually or restore the rules
  here — the shared CSS only fires inside `.station-form`.
-->
