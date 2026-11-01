<script lang="ts">
  /**
   * §16.2.6 c3d — EditorCanvas test stub.
   *
   * Closes c3c review #1 finding #4: handler intercepts have no
   * page-level integration coverage because Svelte 4's
   * createEventDispatcher delivers component events through
   * component-instance callbacks (`$on`/`$dispatch`), NOT through
   * native DOM dispatch — so synthetic
   * `el.dispatchEvent(new CustomEvent('save'))` doesn't reach
   * `on:save={handleSave}` on the parent.
   *
   * This stub renders nothing visible. On mount it exposes its own
   * `dispatch` function globally so tests can drive any of the 9
   * canvas events (save / macroSave / sectionRename / sectionCreate
   * / sectionDelete / sectionsReorder / blockMove / retype /
   * structuralEdit) with arbitrary payloads. The stub stays out of
   * production builds because it's only imported via vi.mock in
   * test files.
   *
   * Usage in a test:
   *
   *   import { fireEditorCanvasEvent } from './__test-stubs/...';
   *   await act(async () => {
   *     fireEditorCanvasEvent('save', {ref, fields});
   *   });
   *   expect(screen.getByTestId('staged-save')).toHaveTextContent('Save 1 change');
   */
  import { createEventDispatcher, onDestroy, onMount } from 'svelte';
  const dispatch = createEventDispatcher();
  // Props the page passes the real canvas. The template is kept on
  // `window.__editorCanvasTemplate` for tests to read.
  export let panelTarget: HTMLElement | null = null;
  void panelTarget;
  export let template: unknown = null;
  export let sidecar: unknown = null;
  void sidecar;
  $: (window as unknown as Record<string, unknown>).__editorCanvasTemplate = template;
  /** The page opens a song's rule panel through this: tests read the
   *  refs it was asked to open from `__editorCanvasOpenRefCalls`. */
  export function openRef(ref: unknown): boolean {
    const w = window as unknown as Record<string, unknown[]>;
    (w.__editorCanvasOpenRefCalls ??= []).push(ref);
    return true;
  }
  /** Counted in `__editorCanvasCloseRuleCalls`. */
  export function closeRule(): void {
    const w = window as unknown as Record<string, number>;
    w.__editorCanvasCloseRuleCalls = (w.__editorCanvasCloseRuleCalls ?? 0) + 1;
  }
  onMount(() => {
    // Expose to tests via the documented global.
    (window as unknown as Record<string, unknown>).__editorCanvasStubDispatch = dispatch;
  });
  onDestroy(() => {
    // §16.2.6.t10 — clear the global so sequential tests don't share
    // a stale reference. Without this, the c3d sweep finding S6
    // scenario: test A's stub unmounts → global still points at A's
    // (now-destroyed) dispatcher → test B's helper, racing before B's
    // own mount completes, reads A's dispatcher and fires into a
    // dead component (Svelte 4's createEventDispatcher closes over
    // the component instance; the call no-ops silently and the
    // expected event never lands). Latent today (every render(Page)
    // re-mounts + overwrites before the helper runs), but activates
    // the moment someone writes a test that needs an explicit
    // "stub absent" assertion.
    delete (window as unknown as Record<string, unknown>).__editorCanvasStubDispatch;
    delete (window as unknown as Record<string, unknown>).__editorCanvasOpenRefCalls;
    delete (window as unknown as Record<string, unknown>).__editorCanvasCloseRuleCalls;
  });
</script>

<!-- Uses the same `data-testid="editor-canvas"` as the real
     EditorCanvas component so existing visualise.test.ts cases
     (which look up the canvas by testid) keep working when the
     stub is mocked in. -->
<div data-testid="editor-canvas"></div>
