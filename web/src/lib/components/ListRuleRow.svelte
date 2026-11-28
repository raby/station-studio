<script lang="ts">
  /**
   * <ListRuleRow> — §16.14.t11.
   *
   * Chip-based editor for one list-rule row (today: only
   * `cross_pool_dedup_sources`; the registry will grow). Replaces
   * the read-only placeholder §16.14.t10 c1 shipped as a stop-gap.
   *
   * Design pass D1: extracted as a child component; the int rows
   * stay inline in <RulesPanel>. D3: explicit Save + local staging
   * + dirty tracking (consistent with int rows). D8: free-form
   * text input + autocomplete from the GLOBAL Sources list (NOT
   * per-station — the design pass D5 said per-station, but sources
   * are global in this app; corrected here). D5 still applies in
   * spirit: filter to `role === 'reference'`.
   *
   * Owns its own staged-state + saving + error so a row's
   * in-flight save doesn't blank the rest of the panel. Emits a
   * single `updated` event when the row's canonical state changes
   * (after Save or Reset succeeds) so the parent can refresh its
   * rules array.
   *
   * §16.2.4 c2 review #7 — convergence note: the chip + input +
   * Add-button cluster here overlaps with the §16.2.4 c2
   * <ChipMultiInput> component (web/src/lib/components/editor/).
   * ChipMultiInput is a focused controlled input (no server
   * state); ListRuleRow owns server state (Save/Reset + global
   * `/api/sources` fetch + suggestion metadata). A clean refactor
   * would extract a third-tier `<Chips>` primitive that both
   * compose around — but ListRuleRow's suggestion items show
   * `name · role · N tracks` metadata that ChipMultiInput
   * currently doesn't render, so the merge needs a slot/callback
   * for the suggestion row. Deferred to the §16.2.4 slice-review
   * sweep (or a separate cleanup slice) so c2 review #7 doesn't
   * grow into a cross-cutting test-surface change.
   */
  import {
    ApiError,
    clearStationRule,
    listSources,
    setStationRule,
    type Rule,
    type Source,
  } from '$lib/api';
  import { createEventDispatcher, onMount } from 'svelte';

  export let slug: string;
  export let rule: Rule;

  const dispatch = createEventDispatcher<{ updated: Rule }>();

  /** Local edit state: the in-progress chip list. Initialised once
   * from `rule.value`; updated manually after Save/Reset succeed.
   * NOT reactively bound to `rule.value` — a reactive link would
   * clobber unsaved edits whenever the parent passes a new rule
   * prop (e.g. on full panel reload). */
  let staged: string[] = Array.isArray(rule.value) ? [...rule.value] : [];

  /** What the user has typed in the chip input but not yet added. */
  let inputText = '';

  /** Autocomplete data: reference-role source pools, fetched once
   * on mount. Cached for the component's lifetime — D5. */
  let suggestions: Source[] = [];
  let suggestionsLoading = false;

  let saving = false;
  let error: string | null = null;

  /** Strictly the SAVED state (rule.value vs staged) drives the
   * Save button's enabled state. NOT inputText — typing something
   * doesn't dirty the row until it's added as a chip. */
  $: ruleValue = Array.isArray(rule.value) ? (rule.value as string[]) : [];
  $: isDirty = !arraysEqual(staged, ruleValue);

  /** Hide Reset when the value is already the station's default
   * (D10 — match int-row pattern): `is_override` is false then, and
   * Reset would no-op. */
  $: showReset = rule.is_override;
  $: defaultList = Array.isArray(rule.default_value) ? (rule.default_value as string[]) : [];

  /** Autocomplete suggestions visible to the user: filter to
   * reference-role pools, exclude already-chipped names, narrow
   * by current input text. */
  $: filteredSuggestions = suggestions
    .filter((s) => s.role === 'reference')
    .filter((s) => !staged.includes(s.name))
    .filter(
      (s) =>
        inputText.length === 0 ||
        s.name.toLowerCase().includes(inputText.toLowerCase()),
    );

  onMount(loadSuggestions);

  async function loadSuggestions() {
    suggestionsLoading = true;
    try {
      // Sources are global, not per-station; the global
      // `/api/sources` endpoint is what's wired today.
      suggestions = await listSources();
    } catch (e) {
      // Soft-fail: autocomplete becomes unavailable, but typing-
      // and-Enter still works (the free-form input path, D8).
      console.warn('ListRuleRow: failed to load source suggestions', e);
      suggestions = [];
    } finally {
      suggestionsLoading = false;
    }
  }

  /** Push `name` onto the staged chip list. Idempotent: a chip
   * already present is a no-op.
   *
   * c1 review F5 — mirror the backend's comma rejection at
   * input time. The on-disk format is comma-separated; entries
   * with commas would 400 on Save. Catching it here gives the
   * operator instant feedback instead of "type → Save → error
   * → fix → re-Save." */
  function addChip(name: string) {
    const trimmed = name.trim();
    if (trimmed === '') return;
    if (trimmed.includes(',')) {
      error = `Source names can't contain commas (the on-disk separator). Rename the pool.`;
      return;
    }
    if (staged.includes(trimmed)) {
      inputText = '';
      return;
    }
    error = null;
    staged = [...staged, trimmed];
    inputText = '';
  }

  function removeChip(name: string) {
    staged = staged.filter((s) => s !== name);
  }

  /** Enter → add chip from inputText. Backspace on empty input →
   * remove the last chip (chip-UI convention). */
  function onInputKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      addChip(inputText);
    } else if (e.key === 'Backspace' && inputText === '' && staged.length > 0) {
      e.preventDefault();
      staged = staged.slice(0, -1);
    }
  }

  async function save() {
    if (!isDirty) return;
    saving = true;
    error = null;
    try {
      const updated = await setStationRule(slug, rule.key, staged);
      // Mirror the server's canonical post-write state into the
      // local staged so isDirty flips back to false.
      staged = Array.isArray(updated.value) ? [...updated.value] : [];
      dispatch('updated', updated);
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  /** Reset = DELETE the Rule row (D2), which puts the station's
   * default back: its template's value when it declares one (the
   * starter's `cross_pool_dedup_sources` is ["REFERENCE_ARCHIVE"]),
   * else the registry's. `default_value` is that same default (the
   * rules API reads it from the template), so the row can show it
   * without a refetch.
   *
   * (c1 review F1 found the trap this used to have: DELETE gave the
   * registry's [] and the template's value never came back in the
   * app. The API now writes the template's value straight back.) */
  async function reset() {
    saving = true;
    error = null;
    try {
      await clearStationRule(slug, rule.key);
      // After DELETE, the effective value is the station's default,
      // which `default_value` already carries. Emit a synthetic
      // updated rule so the parent's `rules` array stays correct
      // without a refetch.
      const defaultValue = Array.isArray(rule.default_value)
        ? (rule.default_value as string[])
        : [];
      const restored: Rule = {
        ...rule,
        value: defaultValue,
        is_override: false,
      };
      staged = [...defaultValue];
      dispatch('updated', restored);
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  function arraysEqual(a: string[], b: string[]): boolean {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (a[i] !== b[i]) return false;
    }
    return true;
  }
</script>

<div class="list-rule-row" data-testid="list-rule-row" data-key={rule.key}>
  {#if staged.length > 0}
    <ul class="chips" aria-label="{rule.key} sources">
      {#each staged as chip (chip)}
        <li class="chip" data-testid="list-rule-chip">
          <span class="chip-label">{chip}</span>
          <button
            type="button"
            class="chip-remove"
            data-testid="list-rule-chip-remove"
            aria-label="Remove {chip}"
            on:click={() => removeChip(chip)}
            disabled={saving}
          >×</button>
        </li>
      {/each}
    </ul>
  {:else}
    <p class="empty" data-testid="list-rule-empty">
      <em>No sources excluded. Add one below to start excluding tracks.</em>
    </p>
  {/if}

  <div class="add-row">
    <label class="add-input">
      <span class="visually-hidden">Add source for {rule.key}</span>
      <input
        type="text"
        data-testid="list-rule-input"
        placeholder={suggestionsLoading
          ? 'Loading sources…'
          : 'Type a source name + Enter (or pick below)'}
        bind:value={inputText}
        on:keydown={onInputKeydown}
        disabled={saving}
        aria-describedby="list-rule-input-hint-{rule.key}"
        autocomplete="off"
      />
    </label>
    <button
      type="button"
      class="add-button btn-primary"
      data-testid="list-rule-add"
      disabled={inputText.trim() === '' || saving}
      on:click={() => addChip(inputText)}
    >Add</button>
  </div>

  {#if filteredSuggestions.length > 0 && inputText.length > 0}
    <ul class="suggestions" data-testid="list-rule-suggestions">
      {#each filteredSuggestions.slice(0, 8) as s (s.id)}
        <li>
          <button
            type="button"
            class="suggestion"
            data-testid="list-rule-suggestion"
            disabled={saving}
            on:click={() => addChip(s.name)}
          >
            {s.name}
            <span class="suggestion-meta">reference · {s.track_count} tracks</span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  <p id="list-rule-input-hint-{rule.key}" class="hint">
    Typing a name not in the suggestions still works — the engine
    cross-checks at generation time.
  </p>

  <div class="actions">
    <button
      type="button"
      class="save btn-primary"
      data-testid="list-rule-save"
      disabled={!isDirty || saving}
      on:click={save}
    >
      {saving ? 'Saving…' : 'Save'}
    </button>
    {#if showReset}
      <button
        type="button"
        class="reset"
        data-testid="list-rule-reset"
        disabled={saving}
        on:click={reset}
        aria-describedby="list-rule-reset-hint-{rule.key}"
      >
        Reset to default
      </button>
    {/if}
  </div>

  {#if showReset}
    <!-- Says what Reset will put back: the station's own default. -->
    <p id="list-rule-reset-hint-{rule.key}" class="reset-hint">
      Puts back this station's default:
      {defaultList.length ? defaultList.join(', ') : 'none'}.
    </p>
  {/if}

  {#if error}
    <p class="error" data-testid="list-rule-error">{error}</p>
  {/if}
</div>

<style>
  .list-rule-row {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .chips {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    background: var(--panel-2);
    border: 1px solid var(--accent);
    border-radius: 999px;
    padding: 0.2rem 0.55rem;
    font-size: 0.85rem;
    font-family: ui-monospace, monospace;
  }
  .chip-remove {
    border: none;
    background: transparent;
    cursor: pointer;
    color: var(--muted);
    font-size: 1rem;
    line-height: 1;
    padding: 0 0.1rem;
  }
  .chip-remove:hover:not(:disabled) {
    color: var(--danger);
  }
  .chip-remove:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .empty {
    font-size: 0.85rem;
    color: var(--text-muted);
    margin: 0;
  }
  .add-row {
    display: flex;
    gap: 0.4rem;
  }
  .add-input {
    flex: 1;
    display: flex;
  }
  .add-input input {
    flex: 1;
    padding: 0.35rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 0.9rem;
  }
  .add-button {
    font-size: 0.85rem;
  }
  .suggestions {
    list-style: none;
    padding: 0;
    margin: 0;
    border: 1px solid var(--border);
    border-radius: 4px;
    max-height: 12rem;
    overflow-y: auto;
    background: var(--panel);
  }
  .suggestions li + li {
    border-top: 1px solid var(--border);
  }
  .suggestion {
    width: 100%;
    text-align: left;
    background: transparent;
    border: none;
    padding: 0.4rem 0.6rem;
    cursor: pointer;
    font-size: 0.85rem;
    display: flex;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .suggestion:hover:not(:disabled) {
    background: var(--panel);
  }
  .suggestion:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .suggestion-meta {
    color: var(--text-muted);
    font-size: 0.78rem;
  }
  .hint {
    color: var(--text-muted);
    font-size: 0.78rem;
    font-style: italic;
    margin: 0;
  }
  .actions {
    display: flex;
    gap: 0.5rem;
  }
  .error {
    color: var(--danger);
    font-size: 0.85rem;
    margin: 0;
  }
  /* §16.14.t11 c1 review F1: hint named near the Clear-override
   * button so the operator sees the registry-vs-template trap. */
  .reset-hint {
    color: var(--text-muted);
    font-size: 0.78rem;
    font-style: italic;
    margin: 0;
  }
  /* §16.5 c4-review R5 — local `.visually-hidden` removed in favour
     of the global utility in +layout.svelte (which adds the
     clip-path fallback + matches the rest of the codebase). */
</style>
