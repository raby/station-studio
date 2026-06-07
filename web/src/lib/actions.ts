/**
 * Shared Svelte actions (use:directive helpers).
 *
 * §16.2.5 c2 review #7 — `focusOnMount` replaces the unreliable
 * `autofocus` HTML attribute for inputs inserted via reactive
 * `{#if}` blocks. Firefox + Safari ignore `autofocus` on
 * dynamically-mounted inputs; the action calls `node.focus()`
 * imperatively on mount, which works uniformly across browsers
 * + closes the svelte-check a11y-no-autofocus warning the
 * attribute triggers.
 *
 * Usage:
 *   <input use:focusOnMount />
 *
 * Or pass `false` to disable conditionally:
 *   <input use:focusOnMount={shouldFocus} />
 */
export function focusOnMount(node: HTMLElement, enabled: boolean = true) {
  if (enabled) {
    // Defer one microtask so the element is actually in the DOM +
    // can receive focus. Calling node.focus() synchronously during
    // the mount tick is a no-op in some browsers (the focus call
    // happens before the element is "current" in the focus queue).
    queueMicrotask(() => node.focus());
  }
  return {
    update(newEnabled: boolean) {
      if (newEnabled && document.activeElement !== node) {
        queueMicrotask(() => node.focus());
      }
    },
  };
}
