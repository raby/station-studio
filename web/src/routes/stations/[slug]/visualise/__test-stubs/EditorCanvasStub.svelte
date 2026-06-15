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
   *   expect(screen.getByTestId('staged-save')).toHaveTextContent('Save 1 edit');
   */
  import { createEventDispatcher, onMount } from 'svelte';
  const dispatch = createEventDispatcher();
  onMount(() => {
    // Expose to tests via the documented global.
    (window as unknown as Record<string, unknown>).__editorCanvasStubDispatch = dispatch;
  });
</script>

<!-- Uses the same `data-testid="editor-canvas"` as the real
     EditorCanvas component so existing visualise.test.ts cases
     (which look up the canvas by testid) keep working when the
     stub is mocked in. -->
<div data-testid="editor-canvas"></div>
