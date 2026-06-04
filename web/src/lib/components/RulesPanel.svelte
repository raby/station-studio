<script lang="ts">
  /**
   * <RulesPanel> — §16.10.
   *
   * Per-station rule editor. Lists the engine's rule registry
   * (artist_dedup_window, mix_exclusion_weeks, cursor_advance_step,
   * cursor_reset_cycle) with each rule's effective value, the engine
   * default, and an override badge. Inline-edit each value; Save
   * PUTs the override, Reset clears it (revert to default).
   *
   * Self-contained: owns its own fetch + per-row save/reset state, so
   * a future sidebar shell (§16.2) can embed it as one tab with just
   * `<RulesPanel {slug} />`. Validation mirrors the backend
   * (engine.rules): value must be a non-negative integer, ≤ max_value
   * when the rule sets one. The backend re-validates + is the source
   * of truth; client-side checks just give instant feedback.
   */
  import {
    ApiError,
    getStationRules,
    setStationRule,
    clearStationRule,
    type Rule,
  } from '$lib/api';
  import { onMount } from 'svelte';

  export let slug: string;

  let rules: Rule[] = [];
  let loading = true;
  let loadError: string | null = null;

  // Per-key local edit state: the in-progress numeric value the user
  // typed (keyed by rule.key). Seeded from the fetched value; diverges
  // as the user edits. A row is "dirty" when edited !== rule.value —
  // the Save button's `disabled` inlines that comparison (rather than
  // calling an isDirty() helper) so Svelte tracks `edited` as a direct
  // template dependency; a function call would hide the dependency and
  // leave the button stuck disabled after the first edit.
  //
  // §16.14.t10: int rules only. List rules render a read-only
  // placeholder (no edit affordance) until §16.14.t11 ships the
  // chip-based <ListRuleRow> component, so they never enter `edited`.
  let edited: Record<string, number> = {};
  // Per-key in-flight + error state so one row saving/erroring doesn't
  // blank the others.
  let saving: Record<string, boolean> = {};
  let rowError: Record<string, string | null> = {};

  onMount(load);

  async function load() {
    loading = true;
    loadError = null;
    try {
      const res = await getStationRules(slug);
      rules = res.rules;
      // §16.14.t10: only int rules get an editable shadow value;
      // list rules render as a read-only placeholder so we skip
      // them. Narrows the value type for TS too (kind === 'int'
      // → value is number).
      edited = Object.fromEntries(
        rules
          .filter((r) => r.kind === 'int')
          .map((r) => [r.key, r.value as number]),
      );
    } catch (e) {
      loadError = e instanceof ApiError ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  // Humanise "artist_dedup_window" → "Artist dedup window".
  function humanise(key: string): string {
    const spaced = key.replace(/_/g, ' ');
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
  }

  /** §16.14.t10: format a list-rule value for the read-only
   *  placeholder. Handles both `string[]` (the list-rule shape)
   *  and number (defensive — if a list-row came through with the
   *  wrong shape we still render something rather than crash). */
  function formatListValue(v: number | string[]): string {
    if (!Array.isArray(v)) return String(v);
    return v.length > 0 ? v.join(', ') : '(empty)';
  }

  /** Client-side validation mirroring engine.rules: non-negative
   *  integer, ≤ max_value when set. Returns an error string or null. */
  function validate(r: Rule, value: number): string | null {
    if (!Number.isInteger(value)) return 'must be a whole number';
    if (value < 0) return 'must be ≥ 0';
    if (r.max_value !== null && value > r.max_value) {
      return `must be ≤ ${r.max_value}`;
    }
    return null;
  }

  async function save(r: Rule) {
    // §16.14.t10: list-rule rows are read-only placeholders today;
    // §16.14.t11 will add the chip-based editor. Defensive bail
    // here covers a future code path that calls save() on a list
    // rule by mistake.
    if (r.kind !== 'int') return;
    const value = edited[r.key];
    const err = validate(r, value);
    if (err !== null) {
      rowError = { ...rowError, [r.key]: err };
      return;
    }
    saving = { ...saving, [r.key]: true };
    rowError = { ...rowError, [r.key]: null };
    try {
      const updated = await setStationRule(slug, r.key, value);
      // Replace the row in place with the canonical post-write state.
      rules = rules.map((x) => (x.key === r.key ? updated : x));
      edited = { ...edited, [r.key]: updated.value as number };
    } catch (e) {
      rowError = {
        ...rowError,
        [r.key]: e instanceof ApiError ? e.message : String(e),
      };
    } finally {
      saving = { ...saving, [r.key]: false };
    }
  }

  async function reset(r: Rule) {
    saving = { ...saving, [r.key]: true };
    rowError = { ...rowError, [r.key]: null };
    try {
      await clearStationRule(slug, r.key);
      // The DELETE returns 204 (no body); refetch THIS row's canonical
      // state by reloading the list. Cheap (4 rules) + keeps every
      // row's is_override flag accurate.
      await load();
    } catch (e) {
      rowError = {
        ...rowError,
        [r.key]: e instanceof ApiError ? e.message : String(e),
      };
    } finally {
      // Clear in a `finally` — NOT just the catch branch (c1+c2 review
      // 🟡-1). On the success path the stuck `saving=true` would
      // disable the Save button after a later edit (disabled =
      // edited===value || saving), trapping the user until reload.
      saving = { ...saving, [r.key]: false };
    }
  }
</script>

<section class="rules-panel" data-testid="rules-panel">
  <h2>Rules</h2>
  <p class="hint">
    Per-station overrides for the engine's generation rules. Unset
    rules use the engine default (shown greyed).
  </p>

  {#if loading}
    <p>Loading…</p>
  {:else if loadError}
    <p class="error" data-testid="rules-error">{loadError}</p>
  {:else}
    <ul class="rule-list">
      {#each rules as r (r.key)}
        <li class="rule-row" data-testid="rule-row" data-key={r.key}>
          <div class="rule-head">
            <span class="rule-name">{humanise(r.key)}</span>
            {#if r.is_override}
              <span class="badge override" data-testid="override-badge">
                overridden
              </span>
            {:else}
              <span class="badge default" data-testid="default-badge">
                default
              </span>
            {/if}
          </div>
          <p class="rule-desc">{r.description}</p>
          {#if r.kind === 'int'}
            <div class="rule-edit">
              <input
                type="number"
                min="0"
                max={r.max_value ?? undefined}
                data-testid="rule-input"
                aria-label="{humanise(r.key)} value"
                bind:value={edited[r.key]}
              />
              <span class="rule-default-hint">
                default: {r.default_value}{r.max_value !== null
                  ? ` · max: ${r.max_value}`
                  : ''}
              </span>
              <button
                type="button"
                class="save"
                data-testid="rule-save"
                disabled={edited[r.key] === r.value || saving[r.key]}
                on:click={() => save(r)}
              >
                {saving[r.key] ? 'Saving…' : 'Save'}
              </button>
              {#if r.is_override}
                <button
                  type="button"
                  class="reset"
                  data-testid="rule-reset"
                  disabled={saving[r.key]}
                  on:click={() => reset(r)}
                >
                  Reset to default
                </button>
              {/if}
            </div>
            {#if rowError[r.key]}
              <p class="error row-error" data-testid="rule-row-error">
                {rowError[r.key]}
              </p>
            {/if}
          {:else}
            <!--
              §16.14.t10 D7: read-only placeholder for list rules.
              §16.14.t11 replaces this with the <ListRuleRow>
              chip-based editor. Until then, list-rule values
              display as a JSON-shaped read-only line. The
              `data-testid` distinguishes from the int row so
              tests can target each kind separately.
            -->
            <div class="rule-edit list-rule-placeholder" data-testid="rule-list-placeholder">
              <span class="list-value">
                {formatListValue(r.value)}
              </span>
              <span class="rule-default-hint">
                default: {formatListValue(r.default_value)}
              </span>
              <span class="placeholder-hint">
                Edit via API until §16.14.t11 ships the list-rule UI.
              </span>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .rules-panel {
    font-family: system-ui, sans-serif;
  }
  h2 {
    margin: 0 0 0.25rem;
  }
  .hint {
    color: #666;
    font-size: 0.9rem;
    margin-top: 0;
  }
  .rule-list {
    list-style: none;
    padding: 0;
  }
  .rule-row {
    padding: 0.75rem 0;
    border-bottom: 1px solid #eee;
  }
  .rule-head {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }
  .rule-name {
    font-weight: 600;
  }
  .badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.45rem;
    border-radius: 999px;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .badge.override {
    background: #e8f5e9;
    color: #2e7d32;
    border: 1px solid #a5d6a7;
  }
  .badge.default {
    background: #f4f4f4;
    color: #888;
    border: 1px solid #ddd;
  }
  .rule-desc {
    color: #555;
    font-size: 0.85rem;
    margin: 0.25rem 0 0.5rem;
  }
  .rule-edit {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex-wrap: wrap;
  }
  input[type='number'] {
    width: 7rem;
    padding: 0.35rem;
    border: 1px solid #ccc;
    border-radius: 4px;
    font-variant-numeric: tabular-nums;
  }
  .rule-default-hint {
    color: #999;
    font-size: 0.8rem;
  }
  .save {
    padding: 0.35rem 0.9rem;
    background: #2c7a7b;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .save:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .reset {
    padding: 0.35rem 0.7rem;
    background: white;
    border: 1px solid #ccc;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .reset:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .error {
    color: crimson;
  }
  .row-error {
    font-size: 0.85rem;
    margin: 0.35rem 0 0;
  }
  /* §16.14.t10: read-only placeholder for list rules. */
  .list-rule-placeholder {
    background: #fafafa;
    border: 1px dashed #ddd;
    border-radius: 4px;
    padding: 0.5rem 0.75rem;
  }
  .list-value {
    font-family: ui-monospace, monospace;
    font-size: 0.85rem;
  }
  .placeholder-hint {
    color: #888;
    font-size: 0.78rem;
    font-style: italic;
    margin-left: auto;
  }
</style>
