<script lang="ts">
  /**
   * <ViewModeToggle> — §16.2.2 c4.
   *
   * Three rows of toggle pills bound to a `ViewModeState`:
   *
   *   - Label mode:   compact | verbose
   *   - Colour by:    source  | strategy
   *   - Macro visual: wider-chip | stacked | count-badge
   *
   * Two-way bound to the parent via `bind:value={viewMode}`. The
   * parent owns persistence (calls writeViewMode after each change);
   * this component is pure UI.
   *
   * Default values are set by the parent reading `readViewMode()` —
   * which falls back to the DESIGN.md defaults (compact + by-source
   * + wider-chip).
   */
  import type {
    ColorBy,
    LabelMode,
    MacroVisual,
    ViewModeState,
  } from '$lib/editor';

  export let value: ViewModeState;

  function setLabelMode(m: LabelMode) {
    value = { ...value, labelMode: m };
  }
  function setColorBy(c: ColorBy) {
    value = { ...value, colorBy: c };
  }
  function setMacroVisual(m: MacroVisual) {
    value = { ...value, macroVisual: m };
  }
</script>

<div class="view-mode" data-testid="view-mode-toggle">
  <div class="row">
    <span class="row-label">Labels</span>
    <div class="pills" role="group" aria-label="Label mode">
      <button
        type="button"
        class="pill"
        class:active={value.labelMode === 'compact'}
        aria-pressed={value.labelMode === 'compact'}
        data-testid="vm-label-compact"
        on:click={() => setLabelMode('compact')}
      >compact</button>
      <button
        type="button"
        class="pill"
        class:active={value.labelMode === 'verbose'}
        aria-pressed={value.labelMode === 'verbose'}
        data-testid="vm-label-verbose"
        on:click={() => setLabelMode('verbose')}
      >verbose</button>
    </div>
  </div>

  <div class="row">
    <span class="row-label">Colour by</span>
    <div class="pills" role="group" aria-label="Colour-by mode">
      <button
        type="button"
        class="pill"
        class:active={value.colorBy === 'source'}
        aria-pressed={value.colorBy === 'source'}
        data-testid="vm-color-source"
        on:click={() => setColorBy('source')}
      >source</button>
      <button
        type="button"
        class="pill"
        class:active={value.colorBy === 'strategy'}
        aria-pressed={value.colorBy === 'strategy'}
        data-testid="vm-color-strategy"
        on:click={() => setColorBy('strategy')}
      >strategy</button>
    </div>
  </div>

  <div class="row">
    <span class="row-label">Macros</span>
    <div class="pills" role="group" aria-label="Macro visual mode">
      <button
        type="button"
        class="pill"
        class:active={value.macroVisual === 'wider-chip'}
        aria-pressed={value.macroVisual === 'wider-chip'}
        data-testid="vm-macro-wider"
        on:click={() => setMacroVisual('wider-chip')}
      >wider chip</button>
      <button
        type="button"
        class="pill"
        class:active={value.macroVisual === 'stacked'}
        aria-pressed={value.macroVisual === 'stacked'}
        data-testid="vm-macro-stacked"
        on:click={() => setMacroVisual('stacked')}
      >stacked</button>
      <button
        type="button"
        class="pill"
        class:active={value.macroVisual === 'count-badge'}
        aria-pressed={value.macroVisual === 'count-badge'}
        data-testid="vm-macro-badge"
        on:click={() => setMacroVisual('count-badge')}
      >badge only</button>
    </div>
  </div>
</div>

<style>
  .view-mode {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    padding: 0.5rem 0.75rem;
    background: var(--bg-subtle, #fafafa);
    border: 1px solid var(--border, #ddd);
    border-radius: 4px;
    margin-bottom: 0.75rem;
    font-size: 0.85rem;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }
  .row-label {
    color: var(--text-muted, #666);
    width: 5rem;
    flex-shrink: 0;
  }
  .pills {
    display: flex;
    gap: 0.25rem;
    flex-wrap: wrap;
  }
  .pill {
    padding: 0.15rem 0.55rem;
    background: var(--panel);
    border: 1px solid var(--border, #ccc);
    border-radius: 999px;
    cursor: pointer;
    font: inherit;
    font-size: 0.8rem;
    color: var(--text-muted, #555);
  }
  .pill:hover {
    background: var(--bg-hover, #f0f0f0);
  }
  .pill.active {
    background: var(--accent, #2c7a7b);
    color: var(--accent-text);
    border-color: var(--accent, #2c7a7b);
  }
  .pill:focus-visible {
    outline: 2px solid var(--focus, #2c5282);
    outline-offset: 1px;
  }
</style>
