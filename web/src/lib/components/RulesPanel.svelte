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
  import ListRuleRow from './ListRuleRow.svelte';

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
  // §16.14.t10: int rules only. List rules render via the
  // <ListRuleRow> chip editor (§16.14.t11) which owns its OWN
  // staged-value state, so they never enter `edited` here.
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
      // §16.14.t10: only int rules get an editable shadow value
      // in THIS panel; list rules render via <ListRuleRow>
      // (§16.14.t11) which owns its own staged-value state. Skip
      // them here. Narrows the value type for TS too (kind ===
      // 'int' → value is number).
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

  /** §16.14.t11: splice the rule emitted by <ListRuleRow> into the
   *  panel's `rules` array so the badge + descriptions stay in
   *  sync with the canonical post-write state. The child component
   *  owns its own staged state, so we don't touch that here. */
  function onListRuleUpdated(updated: Rule) {
    rules = rules.map((x) => (x.key === updated.key ? updated : x));
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
    // §16.14.t10: list-rule rows route through <ListRuleRow>
    // (§16.14.t11) which calls its own setStationRule on
    // dirty-save. Defensive bail here covers a future code path
    // that calls THIS panel's save() on a list rule by mistake.
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
              §16.14.t11: chip-based editor for list rules. Owns
              its own staged + saving + error state; emits
              `updated` when the canonical rule changes (Save or
              Clear-override succeeded), and the parent splices
              the new rule into its `rules` array so the badge +
              label stay in sync. Pre-t11 list rules rendered as
              a read-only placeholder one-liner; t11 retired it.
            -->
            <ListRuleRow
              {slug}
              rule={r}
              on:updated={(e) => onListRuleUpdated(e.detail)}
            />
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
    color: var(--muted);
    font-size: 0.9rem;
    margin-top: 0;
  }
  .rule-list {
    list-style: none;
    padding: 0;
  }
  .rule-row {
    padding: 0.75rem 0;
    border-bottom: 1px solid var(--border);
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
    background: var(--panel-2);
    color: var(--ok);
    border: 1px solid var(--ok);
  }
  .badge.default {
    background: var(--panel-2);
    color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .rule-desc {
    color: var(--muted);
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
    border: 1px solid var(--border);
    border-radius: 4px;
    font-variant-numeric: tabular-nums;
  }
  .rule-default-hint {
    color: var(--text-muted);
    font-size: 0.8rem;
  }
  .save {
    padding: 0.35rem 0.9rem;
    background: var(--accent);
    color: var(--accent-text);
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  .save:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  /* Own hover colour: the app-wide button:hover (panel grey) would
     otherwise win and leave white text on light grey. */
  .save:hover:not(:disabled) {
    background: var(--accent-strong);
  }
  .reset {
    padding: 0.35rem 0.7rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .reset:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .error {
    color: var(--danger);
  }
  .row-error {
    font-size: 0.85rem;
    margin: 0.35rem 0 0;
  }
</style>
