<script lang="ts">
  /**
   * <Chip> — §16.2.2 c3.
   *
   * One rendered chip in the visual canvas. Two visual variants
   * driven by the `ref` discriminator:
   *
   *   - AtomicRef        → narrow chip, source-colored border,
   *                        compact strategy label inside.
   *   - MacroPlaceholder → wider chip with the macro's strategy +
   *                        `×N` count badge (where N = macro.repeat).
   *                        ONLY the first placeholder of a macro's
   *                        repeat span renders visibly — subsequent
   *                        placeholders fold into the count.
   *
   * Read-only: no click handlers, no editability. c4 adds the
   * click-detail panel + tooltip-vs-panel mode toggle. v1 uses
   * native `title=` for tooltip.
   *
   * The chip is a dumb display component. It reads its template /
   * sidecar / ref props + uses helpers from $lib/editor; no fetches
   * or stores of its own.
   */
  import {
    compactStrategyLabel,
    lookupEngineBlock,
    lookupMacro,
    sourceColour,
    verboseStrategyLabel,
    type ExpandedRef,
    type Sidecar,
    type StationTemplate,
  } from '$lib/editor';

  export let ref: ExpandedRef;
  export let template: StationTemplate;
  export let sidecar: Sidecar;

  // Macro chips collapse: only render the FIRST placeholder of each
  // macro's repeat span; subsequent ones are absorbed into the
  // count badge. Section/ChipFlow handles the filter — Chip itself
  // assumes any MacroPlaceholder passed in is intended to be drawn.

  // Resolve the visual fields per ref kind.
  $: data = (() => {
    if (ref.kind === 'atomic-ref') {
      const block = lookupEngineBlock(ref, template.layout);
      if (block === null) {
        // Defensive — a validated sidecar wouldn't produce this,
        // but render SOMETHING readable rather than crashing.
        return {
          kind: 'atomic' as const,
          label: '?',
          source: '?',
          colour: '#999',
          tooltip: 'engine block not found (out-of-bounds ref)',
          isMacro: false,
          repeatCount: 1,
        };
      }
      return {
        kind: 'atomic' as const,
        label: compactStrategyLabel(block),
        source: block.source,
        colour: sourceColour(template, block.source),
        tooltip: verboseStrategyLabel(block),
        isMacro: false,
        repeatCount: 1,
      };
    }
    // Macro placeholder — look up the macro for its spec + count.
    const macro = lookupMacro(sidecar, ref.macro_id);
    if (macro === null) {
      return {
        kind: 'macro' as const,
        label: '?',
        source: '?',
        colour: '#999',
        tooltip: `macro ${ref.macro_id} not found`,
        isMacro: true,
        repeatCount: 1,
      };
    }
    return {
      kind: 'macro' as const,
      label: compactStrategyLabel(macro.spec),
      source: macro.spec.source,
      colour: sourceColour(template, macro.spec.source),
      tooltip:
        `${macro.name ?? macro.id}\n` +
        `repeat: ${macro.repeat}\n` +
        verboseStrategyLabel(macro.spec),
      isMacro: true,
      repeatCount: macro.repeat,
    };
  })();
</script>

<span
  class="chip"
  class:macro={data.isMacro}
  style="--chip-color: {data.colour}"
  title={data.tooltip}
  data-testid="chip"
  data-chip-kind={data.kind}
  data-source={data.source}
>
  <span class="label">{data.label}</span>
  {#if data.isMacro && data.repeatCount > 1}
    <span class="count" data-testid="chip-count">×{data.repeatCount}</span>
  {/if}
</span>

<style>
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3em;
    padding: 0.1em 0.45em;
    margin: 0.15em;
    border: 1px solid var(--chip-color, #ccc);
    border-radius: 4px;
    background: color-mix(in srgb, var(--chip-color, #ccc) 12%, transparent);
    font-family: ui-monospace, monospace;
    font-size: 0.78rem;
    line-height: 1.4;
    white-space: nowrap;
    cursor: default;
  }
  .chip.macro {
    background: color-mix(in srgb, var(--chip-color, #ccc) 18%, transparent);
    border-width: 2px;
  }
  .label {
    font-weight: 500;
  }
  .count {
    color: var(--text-muted, #555);
    font-size: 0.72rem;
    font-weight: 600;
  }
</style>
