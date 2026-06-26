<script lang="ts">
  /**
   * Root layout — mounts the single shared `<ConfirmDialog>` host
   * (§16.25.t4) so any route can call `confirmDialog(...)` and have
   * the modal render on top of the current page.
   *
   * §16.5 c1 — also owns the cross-route a11y chrome:
   *   - "Skip to main content" link, the first focusable element on
   *     every page; jumps keyboard / screen-reader users past the
   *     page's nav crumbs straight to the main landmark.
   *   - Global high-contrast focus-visible ring (only renders on
   *     keyboard focus, not pointer click) so tabbing through the
   *     UI is legible across browsers' default styling.
   *
   * Page-level `<main>` elements own `id="main-content"` so the
   * skip-link target lands at the start of the page's content. The
   * id is required for the skip-link href to work; routes that
   * don't carry it just degrade to the no-op (skip-link does
   * nothing, no layout regression).
   */
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
</script>

<a href="#main-content" class="skip-link">Skip to main content</a>

<slot />

<ConfirmDialog />

<style>
  /* §16.5 c1 — skip-link: visually hidden until focused, then
     pinned top-left. Uses the standard "visually-hidden but
     focus-restorable" pattern (clip: rect(0,0,0,0) rather than
     `display: none`) so the element stays in the tab order. */
  .skip-link {
    background: #000;
    border-radius: 4px;
    color: #fff;
    font-weight: 600;
    left: 0.5rem;
    padding: 0.5rem 0.75rem;
    position: absolute;
    text-decoration: none;
    top: -3rem;
    transition: top 0.15s ease-out;
    z-index: 10000;
  }
  .skip-link:focus {
    outline: 3px solid #fff;
    outline-offset: 2px;
    top: 0.5rem;
  }

  /* §16.5 c1 — global focus-visible ring. :focus-visible fires
     only when the user navigates via keyboard (not pointer
     click), so this doesn't add noise to mouse interactions but
     gives keyboard users a clear focus indicator across every
     element. High contrast (4.5:1) hits WCAG AA. */
  :global(*:focus-visible) {
    outline: 3px solid #0066cc;
    outline-offset: 2px;
  }
</style>
