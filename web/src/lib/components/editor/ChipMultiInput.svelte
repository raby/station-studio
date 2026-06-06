<script lang="ts">
  /**
   * <ChipMultiInput> — §16.2.4 c2 (D3).
   *
   * A focused chip-list editor: render the current values as removable
   * chips, accept new entries via free-form text + Enter, autocomplete
   * suggestions from a parent-supplied source list.
   *
   * Unlike <ListRuleRow> (§16.14.t11) this component owns NO server
   * state — it's purely a controlled input: parent passes `values`,
   * we dispatch `change` with the new array on every edit. The
   * DetailPanel uses it for `allowlist_sources` (the per-block
   * positive filter); the same shape could power any string[] field
   * in future field specs.
   *
   * Suggestions are FILTERED + ALPHABETIZED locally:
   *  - exclude entries already in `values`
   *  - narrow by case-insensitive substring on `inputText`
   *  - cap at 8 (parent supplies the full list; we don't paginate)
   *
   * Free-form input still works when a typed name isn't in the
   * suggestions list — the engine cross-checks at parse_template
   * time (mirrors §16.14.t11's behavior).
   *
   * §16.2.4 c2 review #7 — convergence note: <ListRuleRow>
   * (§16.14.t11) implements a similar chip+input UI but with
   * server-state semantics (Save/Reset + suggestion fetch +
   * suggestion metadata rendering). A third-tier `<Chips>`
   * primitive that both compose around is the planned convergence
   * point; see ListRuleRow's docstring for the surface-area
   * mismatch (suggestion metadata slot) that blocks an immediate
   * merge. Deferred to the §16.2.4 slice-review sweep.
   */
  import { createEventDispatcher } from 'svelte';

  export let values: string[];
  /** Source candidates for autocomplete. Parent filters by role
   *  before passing — this component doesn't know about roles. */
  export let suggestions: Array<{ name: string }> = [];
  /** Test/accessibility id prefix so multiple instances coexist. */
  export let testIdPrefix: string = 'chip-multi';
  /** Accessible label for the input — required for a11y. */
  export let inputLabel: string = 'Add value';
  /** Placeholder text for the empty input. */
  export let placeholder: string = 'Type a name + Enter';
  /** Disable all interactions (e.g. while parent is saving). */
  export let disabled: boolean = false;

  const dispatch = createEventDispatcher<{ change: string[] }>();

  let inputText = '';
  let error: string | null = null;

  $: filtered = suggestions
    .filter((s) => !values.includes(s.name))
    .filter(
      (s) =>
        inputText.length === 0 ||
        s.name.toLowerCase().includes(inputText.toLowerCase()),
    )
    .slice(0, 8);

  function addChip(name: string) {
    const trimmed = name.trim();
    if (trimmed === '') return;
    // Mirror ListRuleRow's comma guard — list-style on-disk formats
    // historically use comma as a separator; rejecting at input
    // time saves a server round-trip.
    if (trimmed.includes(',')) {
      error = "Names can't contain commas.";
      return;
    }
    if (values.includes(trimmed)) {
      inputText = '';
      return;
    }
    error = null;
    dispatch('change', [...values, trimmed]);
    inputText = '';
  }

  function removeChip(name: string) {
    dispatch('change', values.filter((s) => s !== name));
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      addChip(inputText);
    } else if (e.key === 'Backspace' && inputText === '' && values.length > 0) {
      e.preventDefault();
      dispatch('change', values.slice(0, -1));
    }
  }
</script>

<div class="chip-multi" data-testid={testIdPrefix}>
  {#if values.length > 0}
    <ul class="chips" aria-label="{inputLabel} (current)">
      {#each values as v (v)}
        <li class="chip" data-testid="{testIdPrefix}-chip">
          <span class="chip-label">{v}</span>
          <button
            type="button"
            class="chip-remove"
            data-testid="{testIdPrefix}-chip-remove"
            aria-label="Remove {v}"
            on:click={() => removeChip(v)}
            {disabled}
          >×</button>
        </li>
      {/each}
    </ul>
  {/if}

  <div class="add-row">
    <label class="add-input">
      <span class="visually-hidden">{inputLabel}</span>
      <input
        type="text"
        data-testid="{testIdPrefix}-input"
        placeholder={placeholder}
        bind:value={inputText}
        on:keydown={onKey}
        {disabled}
        autocomplete="off"
      />
    </label>
    <button
      type="button"
      class="add-button"
      data-testid="{testIdPrefix}-add"
      disabled={inputText.trim() === '' || disabled}
      on:click={() => addChip(inputText)}
    >Add</button>
  </div>

  {#if filtered.length > 0 && inputText.length > 0}
    <ul class="suggestions" data-testid="{testIdPrefix}-suggestions">
      {#each filtered as s (s.name)}
        <li>
          <button
            type="button"
            class="suggestion"
            data-testid="{testIdPrefix}-suggestion"
            {disabled}
            on:click={() => addChip(s.name)}
          >{s.name}</button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if error}
    <p class="chip-error" data-testid="{testIdPrefix}-error">{error}</p>
  {/if}
</div>

<style>
  .chip-multi { display: flex; flex-direction: column; gap: 0.3rem; }
  .chips {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    background: var(--chip-bg, #eef2f7);
    border: 1px solid var(--border, #d4dae3);
    border-radius: 12px;
    padding: 0.15rem 0.55rem;
    font-size: 0.82rem;
  }
  .chip-remove {
    background: transparent;
    border: none;
    cursor: pointer;
    font-size: 0.9rem;
    line-height: 1;
    color: var(--text-muted, #666);
    padding: 0 0.1rem;
  }
  .chip-remove:hover:not([disabled]) { color: black; }
  .chip-remove[disabled] { cursor: not-allowed; opacity: 0.5; }
  .add-row {
    display: flex;
    gap: 0.3rem;
    align-items: center;
  }
  .add-input { flex: 1; }
  .add-input input {
    width: 100%;
    padding: 0.3rem;
    border: 1px solid var(--border, #ccc);
    border-radius: 4px;
    font: inherit;
    font-size: 0.85rem;
  }
  .add-button {
    padding: 0.3rem 0.75rem;
    border: 1px solid var(--border, #ccc);
    background: white;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .add-button:disabled { cursor: not-allowed; opacity: 0.5; }
  .suggestions {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--border, #ddd);
    border-radius: 4px;
    max-height: 180px;
    overflow-y: auto;
    background: white;
  }
  .suggestion {
    width: 100%;
    text-align: left;
    background: transparent;
    border: none;
    padding: 0.3rem 0.5rem;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .suggestion:hover:not([disabled]) { background: var(--chip-bg, #eef2f7); }
  .chip-error {
    color: var(--error, #c0392b);
    font-size: 0.78rem;
    margin: 0;
  }
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }
</style>
