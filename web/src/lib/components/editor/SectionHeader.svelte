<script lang="ts">
  /**
   * <SectionHeader> — §16.2.2 c3 + §16.2.5 c2.
   *
   * One header bar above a section's ChipFlow. Renders:
   *   - section name (with fallback to id when name is absent)
   *   - chip count
   *   - collapse / expand toggle button
   *   - §16.2.5 c2: rename (inline edit), Move up / Move down,
   *     and Delete affordances. Suppressed for the system
   *     `_unsorted` bin (operator can't rename / reorder /
   *     delete it; the leading underscore is the visual signal).
   *
   * Drag-drop: SectionHeader is the drop target for a chip
   * dragged out of its section's ChipFlow (D6). The parent
   * (Section / EditorCanvas) wires the dragover + drop handlers;
   * SectionHeader just exposes the section id via data attribute
   * so the drop handler can map back to the API call.
   */
  import { createEventDispatcher } from 'svelte';
  import { focusOnMount } from '$lib/actions';
  import {
    CHIP_DRAG_MIME, SECTION_DRAG_MIME,
    canDropOn, draggingSectionId, isSystemSection,
  } from '$lib/drag-drop';
  import type { SidecarSection } from '$lib/editor';

  export let section: SidecarSection;
  export let chipCount: number;
  export let expanded: boolean;
  /** §16.2.5 c2 — Move buttons disable at boundaries; parent
   *  decides based on layout_order position. */
  export let canMoveUp: boolean = false;
  export let canMoveDown: boolean = false;

  const dispatch = createEventDispatcher<{
    toggle: void;
    rename: { sectionId: string; name: string };
    delete: { sectionId: string };
    moveUp: { sectionId: string };
    moveDown: { sectionId: string };
    // §16.2.5 c2 drag-drop — fires when an atomic chip lands on
    // this header. Payload is the raw chip ref the source set via
    // dataTransfer; EditorCanvas converts to blockMove.
    chipDrop: { ref: unknown; targetSectionId: string };
    // §16.2.5.t1 — section-header drag-reorder. Fires when ANOTHER
    // section header is dropped on this one. The dropped section
    // moves to BEFORE this section in layout_order. EditorCanvas
    // converts to a sectionsReorder dispatch.
    sectionDrop: { sourceSectionId: string; beforeSectionId: string };
  }>();

  // Display name falls back to id when omitted (mid-slice review
  // 🟡-2 made section.name optional per the Python grammar).
  $: displayName = section.name ?? section.id;

  // System bin (`_unsorted`) — leading underscore reserved per
  // §16.2.5 D2; UI hides the operator-control affordances.
  // §16.2.5.t2 — routed through shared `isSystemSection` helper
  // so adding a 2nd system bin is one site to update.
  $: isSystemBin = isSystemSection(section.id);

  // §16.2.5 c2 — inline rename state.
  let renaming = false;
  let renameValue = '';
  // c2 review #4 fix: latch so the blur handler can't fire a second
  // dispatch after Enter committed. When Enter runs commitRename, we
  // set this flag; the blur handler short-circuits if it's true. The
  // flag resets on startRename so each rename cycle is independent.
  // Without this guard, real browsers fire blur on the unmounting
  // input (jsdom doesn't, so the §16.2.5 c2 vitest passed) → the
  // second commitRename dispatches a duplicate PATCH /sections/{id}.
  let committed = false;

  function startRename() {
    renameValue = displayName;
    committed = false;
    renaming = true;
  }
  function cancelRename() {
    committed = true;  // suppress the blur firing during unmount
    renaming = false;
  }
  function commitRename() {
    if (committed) return;
    const trimmed = renameValue.trim();
    if (trimmed === '' || trimmed === displayName) {
      committed = true;
      renaming = false;
      return;
    }
    committed = true;
    dispatch('rename', { sectionId: section.id, name: trimmed });
    renaming = false;
  }
  function onRenameKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitRename();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelRename();
    }
  }

  // §16.2.5 c2 D6 — HTML5 drag-drop target. The DataTransfer payload
  // carries the source chip's ref as JSON (set by Chip's dragstart).
  // We accept ONLY our custom MIME so unrelated drags (e.g. text,
  // links from outside the canvas) don't trigger a section drop.
  //
  // c2 review #3 fix #1: the system bin (`_unsorted`) is not a
  // valid manual-move target. `canDropOn` enforces this; without
  // it the drag path bypassed the dropdown's filter + let operators
  // accidentally route chips into the bin via drag.
  $: acceptsDrops = canDropOn(section);
  let dragOver = false;
  // Counter so nested dragenter/dragleave (the inner toggle button +
  // actions can fire enter/leave events as the cursor moves) don't
  // flicker the visual state. Increment on enter, decrement on
  // leave, dragOver is true while counter > 0.
  let dragEnterDepth = 0;

  function _hasChipPayload(e: DragEvent): boolean {
    if (!e.dataTransfer) return false;
    // .types is a DOMStringList; spec-compliant browsers expose
    // 'includes' via the iterable interface.
    return Array.from(e.dataTransfer.types).includes(CHIP_DRAG_MIME);
  }
  function _hasSectionPayload(e: DragEvent): boolean {
    if (!e.dataTransfer) return false;
    return Array.from(e.dataTransfer.types).includes(SECTION_DRAG_MIME);
  }
  function _hasAnyDragPayload(e: DragEvent): boolean {
    return _hasChipPayload(e) || _hasSectionPayload(e);
  }

  // §16.2.5.t1 — section-header drag SOURCE. Operator grabs the
  // header to reorder. System bin (`_unsorted`) is not draggable
  // (it stays at the end of layout_order). Rename mode disables
  // drag so the input doesn't fight the drag handler.
  $: isDraggableSource = !isSystemBin && !renaming;
  function onSectionDragStart(e: DragEvent) {
    if (!e.dataTransfer) return;
    e.dataTransfer.setData(SECTION_DRAG_MIME, section.id);
    e.dataTransfer.effectAllowed = 'move';
    // c3 review #1 finding #4: pin the dragged section's id into a
    // module-local Svelte store so the dragover same-section check
    // is reliable across browsers. `dataTransfer.getData(MIME)` is
    // blanked by Firefox/Safari during dragover (DataTransfer
    // 'protected mode') — only `types` is exposed pre-drop. Without
    // this store, the source's own header would flash the
    // drop-target ring during self-drag-over.
    draggingSectionId.set(section.id);
  }
  function onSectionDragEnd() {
    // c3 review #1 finding #5: clear the dragend state so a
    // cancelled drag (ESC) or source-removed-mid-drag doesn't leave
    // the next drag cycle thinking a section is still mid-flight.
    draggingSectionId.set(null);
    dragOver = false;
    dragEnterDepth = 0;
  }

  function onDragEnter(e: DragEvent) {
    if (!_hasAnyDragPayload(e) || !acceptsDrops) return;
    // c3 review #1 finding #4: skip the highlight if THIS header is
    // the dragging-source. The earlier same-section-check in
    // onDragOver was unreliable cross-browser; the store is set
    // synchronously inside dragstart on the source so it's safe
    // to read here.
    if (
      _hasSectionPayload(e) &&
      $draggingSectionId === section.id
    ) return;
    // c2 review #4 fix #7: removed dead `e.preventDefault()` —
    // only `dragover` needs preventDefault for drop-enablement.
    dragEnterDepth += 1;
    dragOver = true;
  }
  function onDragOver(e: DragEvent) {
    if (!_hasAnyDragPayload(e) || !acceptsDrops) return;
    // c3 review #1 finding #4: use the module store instead of
    // `dataTransfer.getData(SECTION_DRAG_MIME)` which is blanked
    // by Firefox/Safari during dragover. Same source-of-truth as
    // onDragEnter above.
    if (
      _hasSectionPayload(e) &&
      $draggingSectionId === section.id
    ) return;
    // preventDefault on dragover is what enables the drop event to
    // fire — without it the browser treats the target as "not a
    // valid drop target" and rejects the drop.
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  }
  function onDragLeave(e: DragEvent) {
    if (!_hasAnyDragPayload(e) || !acceptsDrops) return;
    dragEnterDepth = Math.max(0, dragEnterDepth - 1);
    if (dragEnterDepth === 0) dragOver = false;
  }
  function onDropEnd() {
    // c3 review #1 finding #5: window-scoped dragend cleanup mirror
    // for the target side. The drag source's `dragend` fires after
    // any drop or cancel — both clear the highlight ring.
    dragOver = false;
    dragEnterDepth = 0;
  }
  function onDrop(e: DragEvent) {
    if (!_hasAnyDragPayload(e) || !acceptsDrops) return;
    e.preventDefault();
    dragOver = false;
    dragEnterDepth = 0;
    // §16.2.5.t1 — section-reorder drop. SECTION_DRAG_MIME wins
    // over CHIP_DRAG_MIME because a SectionHeader can be dropped
    // on another SectionHeader directly; chip drops on a header
    // bar mean "append to this section" (handled below).
    if (_hasSectionPayload(e)) {
      const sourceSectionId = e.dataTransfer?.getData(SECTION_DRAG_MIME);
      if (!sourceSectionId) return;
      if (sourceSectionId === section.id) return;  // self-drop no-op
      dispatch('sectionDrop', {
        sourceSectionId,
        beforeSectionId: section.id,
      });
      return;
    }
    const raw = e.dataTransfer?.getData(CHIP_DRAG_MIME);
    if (!raw) return;
    let ref: unknown;
    try { ref = JSON.parse(raw); }
    catch (err) {
      // c2 review #4 fix #8: log the parse failure so operators
      // can debug a misbehaving drag source instead of silently
      // dropping. console.warn keeps it out of the operator's
      // face but visible in dev tools.
      console.warn('SectionHeader: invalid chip-drop JSON', err, raw);
      return;
    }
    dispatch('chipDrop', { ref, targetSectionId: section.id });
  }
</script>

<!-- svelte-ignore a11y-no-static-element-interactions -->
<header
  class="section-header"
  class:drag-over={dragOver}
  class:draggable-source={isDraggableSource}
  data-testid="section-header"
  data-section-id={section.id}
  data-expanded={expanded}
  data-drag-over={dragOver}
  role="group"
  aria-label="Section {displayName} header (drop target for chip moves; drag handle for section reorder)"
  draggable={isDraggableSource}
  on:dragstart={onSectionDragStart}
  on:dragenter={onDragEnter}
  on:dragover={onDragOver}
  on:dragleave={onDragLeave}
  on:dragend={onSectionDragEnd}
  on:drop={onDrop}
>
  <!-- c3 review #1 finding #3: `draggable="false"` on all inner
       interactive elements. Pre-fix, `<header draggable={true}>`
       made every child <button> + the rename <input> part of the
       drag handle — a mousedown-and-drift on Move-Up / Delete /
       Toggle could initiate a section-drag and cancel the click.
       Native HTML5 drag inheritance is opt-OUT, not opt-in. -->
  <button
    type="button"
    class="toggle"
    aria-expanded={expanded}
    aria-controls={`section-body-${section.id}`}
    data-testid="section-toggle"
    draggable="false"
    on:click={() => dispatch('toggle')}
    on:dragend={onDropEnd}
  >
    <span class="caret" aria-hidden="true">{expanded ? '▼' : '▶'}</span>
    {#if renaming}
      <!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
      <input
        type="text"
        class="rename-input"
        data-testid="section-rename-input"
        draggable="false"
        bind:value={renameValue}
        on:keydown={onRenameKey}
        on:blur={commitRename}
        on:click|stopPropagation
        use:focusOnMount
        aria-label="Rename section"
      />
    {:else}
      <span class="name" data-testid="section-name">{displayName}</span>
    {/if}
    <span class="count" data-testid="section-count">
      <!--
        "picks" reflects the engine-scale count — macros contribute
        `macro.repeat` to the total, matching the ×N badge on a
        single visible chip. Slice review 🟡-2 (`5 atomic + 1
        macro×10` reads "15 picks", with 6 visible chips). "picks"
        is the unit a hobbyist editing the template thinks in:
        "how many tracks does this section produce?"
      -->
      {chipCount} pick{chipCount === 1 ? '' : 's'}
    </span>
  </button>

  {#if !isSystemBin && !renaming}
    <div class="actions" data-testid="section-actions">
      <button
        type="button"
        class="act"
        data-testid="section-rename"
        title="Rename section"
        draggable="false"
        on:click={startRename}
      >✎</button>
      <button
        type="button"
        class="act"
        data-testid="section-move-up"
        title="Move section up"
        disabled={!canMoveUp}
        draggable="false"
        on:click={() => dispatch('moveUp', { sectionId: section.id })}
      >▲</button>
      <button
        type="button"
        class="act"
        data-testid="section-move-down"
        title="Move section down"
        disabled={!canMoveDown}
        draggable="false"
        on:click={() => dispatch('moveDown', { sectionId: section.id })}
      >▼</button>
      <button
        type="button"
        class="act delete"
        data-testid="section-delete"
        title="Delete section (non-empty sections move to Unsorted)"
        draggable="false"
        on:click={() => dispatch('delete', { sectionId: section.id })}
      >🗑</button>
    </div>
  {/if}
</header>

<style>
  .section-header {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin: 0.4rem 0 0.2rem;
    /* §16.2.5 c2 D6 — visible-ring transition when a chip drag is
       over this header. Subtle background + outlined border so
       the operator sees the drop target without it jumping. */
    border-radius: 4px;
    transition: background 80ms ease-out, box-shadow 80ms ease-out;
  }
  /* §16.2.5.t1 — `grab` cursor signals the header is a drag handle
     for section reorder. The toggle button inside keeps its own
     `pointer` cursor; the user-select disable prevents text-select
     from interfering with drag start. */
  .section-header.draggable-source {
    cursor: grab;
    user-select: none;
  }
  .section-header.draggable-source:active {
    cursor: grabbing;
  }
  .section-header.drag-over {
    background: color-mix(in srgb, var(--accent, #2c7a7b) 12%, transparent);
    box-shadow: 0 0 0 2px var(--accent, #2c7a7b);
  }
  .toggle {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex: 1;
    padding: 0.35rem 0.5rem;
    background: var(--bg-subtle, #fafafa);
    border: 1px solid var(--border, #ddd);
    border-radius: 4px;
    cursor: pointer;
    font-family: inherit;
    font-size: 0.9rem;
    text-align: left;
  }
  .toggle:hover {
    background: var(--bg-hover, #f0f0f0);
  }
  .caret {
    font-size: 0.7rem;
    color: var(--text-muted, #666);
    width: 1ch;
  }
  .name {
    font-weight: 600;
    flex: 1;
  }
  .rename-input {
    flex: 1;
    font: inherit;
    font-weight: 600;
    padding: 0.1rem 0.3rem;
    border: 1px solid var(--accent, #2c7a7b);
    border-radius: 3px;
  }
  .count {
    color: var(--text-muted, #666);
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
  }
  .actions {
    display: flex;
    gap: 0.2rem;
  }
  .act {
    background: transparent;
    border: 1px solid var(--border, #ddd);
    border-radius: 3px;
    width: 1.8rem;
    height: 1.8rem;
    cursor: pointer;
    font-size: 0.85rem;
    color: var(--text-muted, #555);
  }
  .act:hover:not([disabled]) {
    background: var(--bg-hover, #f0f0f0);
    color: black;
  }
  .act[disabled] {
    opacity: 0.35;
    cursor: not-allowed;
  }
  .act.delete:hover {
    color: var(--error, #c0392b);
  }
</style>
