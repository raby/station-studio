<script lang="ts">
  /**
   * <DetailPanel> — §16.2.2 c4.
   *
   * Slides in from the right when the user clicks a chip. Shows the
   * full spec for that chip:
   *
   *   - atomic: every BlockSpec field (strategy, source, size,
   *     cursor_id, window, exclusion_window, genre, position,
   *     fallback_genre — whichever are set).
   *   - macro: the macro's name, repeat count, and the same
   *     fields for its spec.
   *
   * Plus context (section name / id, and for macros, the macro id).
   *
   * Read-only v1; §16.2.3 (atomic edit) + §16.2.4 (macro edit) grow
   * this with edit forms.
   */
  import { createEventDispatcher } from 'svelte';
  import {
    lookupEngineBlock,
    lookupMacro,
    type ExpandedRef,
    type Sidecar,
    type SidecarSection,
    type StationTemplate,
  } from '$lib/editor';

  /** The selected ref (or null when nothing's selected — panel is
   *  hidden in that case). */
  export let selected: { ref: ExpandedRef; section: SidecarSection } | null = null;
  export let template: StationTemplate;
  export let sidecar: Sidecar;

  const dispatch = createEventDispatcher<{ close: void }>();

  // Resolve the spec under the selection.
  $: detail = (() => {
    if (selected === null) return null;
    const { ref, section } = selected;
    if (ref.kind === 'atomic-ref') {
      const block = lookupEngineBlock(ref, template.layout);
      return {
        kind: 'atomic' as const,
        section,
        block,
        macro: null,
        address: ref.slot_index !== undefined
          ? `slot_index=${ref.slot_index}`
          : `row_idx=${ref.row_idx}, block_idx=${ref.block_idx}`,
      };
    }
    const macro = lookupMacro(sidecar, ref.macro_id);
    return {
      kind: 'macro' as const,
      section,
      block: macro?.spec ?? null,
      macro,
      address: `macro="${ref.macro_id}"`,
    };
  })();

  function close() {
    dispatch('close');
  }

  // Esc to close.
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && selected !== null) close();
  }
</script>

<svelte:window on:keydown={onKey} />

{#if detail !== null}
  <aside
    class="detail-panel"
    role="dialog"
    aria-label="Chip details"
    data-testid="detail-panel"
    data-kind={detail.kind}
  >
    <header>
      <h2>
        {#if detail.kind === 'atomic'}
          Atomic block
        {:else}
          Macro: {detail.macro?.name ?? detail.macro?.id ?? '?'}
        {/if}
      </h2>
      <button
        type="button"
        class="close"
        aria-label="Close details"
        data-testid="detail-close"
        on:click={close}
      >×</button>
    </header>

    <dl>
      <dt>Section</dt>
      <dd data-testid="detail-section-name">
        {detail.section.name ?? detail.section.id}
      </dd>

      <dt>Address</dt>
      <dd class="mono" data-testid="detail-address">{detail.address}</dd>

      {#if detail.kind === 'macro' && detail.macro}
        <dt>Repeat</dt>
        <dd data-testid="detail-repeat">{detail.macro.repeat}</dd>
      {/if}

      {#if detail.block}
        <dt>Strategy</dt>
        <dd data-testid="detail-strategy">{detail.block.strategy}</dd>

        <dt>Source</dt>
        <dd data-testid="detail-source">{detail.block.source}</dd>

        <dt>Size</dt>
        <dd>{detail.block.size}</dd>

        {#if detail.block.cursor_id !== undefined}
          <dt>cursor_id</dt><dd class="mono">{detail.block.cursor_id}</dd>
        {/if}
        {#if detail.block.window !== undefined}
          <dt>window</dt><dd>{detail.block.window}</dd>
        {/if}
        {#if detail.block.exclusion_window !== undefined}
          <dt>exclusion_window</dt><dd>{detail.block.exclusion_window}</dd>
        {/if}
        {#if detail.block.genre !== undefined}
          <dt>genre</dt><dd class="mono">{detail.block.genre}</dd>
        {/if}
        {#if detail.block.position !== undefined}
          <dt>position</dt><dd>{detail.block.position}</dd>
        {/if}
        {#if detail.block.fallback_genre !== undefined}
          <dt>fallback_genre</dt><dd class="mono">{detail.block.fallback_genre}</dd>
        {/if}
      {:else}
        <dt>Status</dt>
        <dd class="warn">
          Engine block not found at this address — the sidecar may
          reference a slot the template no longer has.
        </dd>
      {/if}
    </dl>
  </aside>
{/if}

<style>
  .detail-panel {
    position: fixed;
    top: 0;
    right: 0;
    height: 100vh;
    width: 320px;
    background: white;
    border-left: 1px solid var(--border, #ddd);
    box-shadow: -2px 0 10px rgba(0, 0, 0, 0.05);
    padding: 1rem;
    overflow-y: auto;
    z-index: 10;
  }
  header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    border-bottom: 1px solid var(--border, #eee);
    padding-bottom: 0.5rem;
    margin-bottom: 0.75rem;
  }
  h2 {
    margin: 0;
    flex: 1;
    font-size: 1rem;
  }
  .close {
    background: transparent;
    border: none;
    font-size: 1.4rem;
    cursor: pointer;
    color: var(--text-muted, #666);
    line-height: 1;
    padding: 0 0.4rem;
  }
  .close:hover {
    color: black;
  }
  dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 0.3rem 0.75rem;
    margin: 0;
    font-size: 0.88rem;
  }
  dt {
    color: var(--text-muted, #666);
    font-weight: 500;
  }
  dd {
    margin: 0;
  }
  .mono {
    font-family: ui-monospace, monospace;
    font-size: 0.85rem;
  }
  .warn {
    color: var(--error, #c0392b);
    font-style: italic;
  }
</style>
