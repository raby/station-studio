<script lang="ts">
  /**
   * <Chip> — §16.2.2 c3 + c4.
   *
   * One rendered chip in the visual canvas. Two visual variants
   * driven by the `ref` discriminator:
   *
   *   - AtomicRef        → narrow chip, source/strategy-coloured
   *                        border, compact OR verbose label inside.
   *   - MacroPlaceholder → macro chip per `macroVisual` view mode:
   *                          - wider-chip:   wider box + ×N badge
   *                          - count-badge:  same width + corner ×N
   *                          - stacked:      offset duplicate behind
   *
   * c4 changes (§16.2.2.t6 chip keyboard a11y promote):
   *   - Chips are now <button>s — natively focusable + Enter/Space
   *     activatable. Click + keypress both dispatch `select`.
   *   - Label mode pulled from the view-mode prop (compact ↔ verbose).
   *   - Colour-by mode (source vs strategy) drives the border colour.
   */
  import { createEventDispatcher } from 'svelte';
  import {
    chipColour,
    compactStrategyLabel,
    lookupEngineBlock,
    lookupMacro,
    refKey,
    verboseStrategyLabel,
    type ExpandedRef,
    type Sidecar,
    type StationTemplate,
    type ViewModeState,
  } from '$lib/editor';
  import { CHIP_DRAG_MIME } from '$lib/drag-drop';

  export let ref: ExpandedRef;
  export let template: StationTemplate;
  export let sidecar: Sidecar;
  export let viewMode: ViewModeState;

  const dispatch = createEventDispatcher<{ select: { ref: ExpandedRef } }>();

  // §16.2.5 c2 drag-drop: only atomic chips are draggable. Macro
  // chips show a grab cursor on hover but native drag is disabled —
  // moving a macro between sections breaks the position math (see
  // c1 review #2 docstring on BlockMoveRequest). The drop targets
  // (SectionHeaders) read the JSON payload via dataTransfer.
  //
  // c2 review #4 fix #9: macros have `draggable={false}` on the
  // element so the browser never fires dragstart for them — the
  // `!isDraggable` guard below is unreachable for macros. The
  // remaining `!e.dataTransfer` guard is the real defensive branch
  // (rare browser quirk where DataTransfer is null on synthetic
  // events).
  $: isDraggable = ref.kind === 'atomic-ref';
  function onDragStart(e: DragEvent) {
    if (!e.dataTransfer) return;
    e.dataTransfer.setData(CHIP_DRAG_MIME, JSON.stringify(ref));
    e.dataTransfer.effectAllowed = 'move';
  }

  // Resolve the visual fields per ref kind.
  $: data = (() => {
    if (ref.kind === 'atomic-ref') {
      const block = lookupEngineBlock(ref, template.layout);
      if (block === null) {
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
        label: viewMode.labelMode === 'verbose'
          ? `${block.strategy} (${block.source})`
          : compactStrategyLabel(block),
        source: block.source,
        colour: chipColour(block, template, viewMode.colorBy),
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
      label: viewMode.labelMode === 'verbose'
        ? `${macro.name ?? macro.id} (${macro.spec.strategy})`
        : compactStrategyLabel(macro.spec),
      source: macro.spec.source,
      colour: chipColour(macro.spec, template, viewMode.colorBy),
      tooltip:
        `${macro.name ?? macro.id}\n` +
        `repeat: ${macro.repeat}\n` +
        verboseStrategyLabel(macro.spec),
      isMacro: true,
      repeatCount: macro.repeat,
    };
  })();

  function activate() {
    dispatch('select', { ref });
  }
</script>

<button
  type="button"
  class="chip"
  class:macro={data.isMacro}
  class:macro-wider={data.isMacro && viewMode.macroVisual === 'wider-chip'}
  class:macro-stacked={data.isMacro && viewMode.macroVisual === 'stacked'}
  class:macro-badge={data.isMacro && viewMode.macroVisual === 'count-badge'}
  style="--chip-color: {data.colour}"
  title={data.tooltip}
  data-testid="chip"
  data-chip-kind={data.kind}
  data-source={data.source}
  data-ref-key={refKey(ref)}
  draggable={isDraggable}
  on:click={activate}
  on:dragstart={onDragStart}
>
  <span class="label">{data.label}</span>
  {#if data.isMacro && data.repeatCount > 1}
    <span class="count" data-testid="chip-count">×{data.repeatCount}</span>
  {/if}
</button>

<style>
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3em;
    /* Touch-target bump (slice review 🟡-1) — chips are now <button>s
       (c4 keyboard a11y), so the click area matters for trackpad-
       precision and reduced-motor-control users. Previous padding
       produced ~16px tall chips; 0.3em/0.55em + line-height 1.5 lifts
       to ~26px, comfortably above the 24px effective bar most editors
       hit. Still well below WCAG AAA 44px (which would crowd the
       canvas); §16.5 a11y pass revisits. */
    padding: 0.3em 0.55em;
    margin: 0.15em;
    border: 1px solid var(--chip-color, #ccc);
    border-radius: 4px;
    background: color-mix(in srgb, var(--chip-color, #ccc) 12%, transparent);
    font-family: ui-monospace, monospace;
    font-size: 0.78rem;
    line-height: 1.5;
    white-space: nowrap;
    cursor: pointer;
    color: inherit;
  }
  .chip:hover {
    background: color-mix(in srgb, var(--chip-color, #ccc) 22%, transparent);
  }
  .chip:focus-visible {
    outline: 2px solid var(--focus, #2c5282);
    outline-offset: 1px;
  }
  .chip.macro {
    background: color-mix(in srgb, var(--chip-color, #ccc) 18%, transparent);
    border-width: 2px;
  }
  .chip.macro-wider {
    padding-left: 0.6em;
    padding-right: 0.6em;
  }
  .chip.macro-stacked {
    box-shadow:
      2px 2px 0 -0.5px color-mix(in srgb, var(--chip-color, #ccc) 40%, white),
      4px 4px 0 -1px color-mix(in srgb, var(--chip-color, #ccc) 25%, white);
  }
  .label {
    font-weight: 500;
  }
  .count {
    color: var(--text-muted, #555);
    font-size: 0.72rem;
    font-weight: 600;
  }
  .chip.macro-badge .count {
    position: absolute;
    transform: translate(0.6em, -0.7em);
    background: var(--chip-color, #ccc);
    color: white;
    padding: 0 0.3em;
    border-radius: 8px;
  }
</style>
