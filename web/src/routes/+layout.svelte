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
    z-index: 10000;
  }
  /* `:focus-visible` keeps the reveal keyboard-only (the link
     is only reachable by keyboard anyway) and folds it into
     the same focus-ring contract as every other element. */
  .skip-link:focus-visible {
    outline: 3px solid #fff;
    outline-offset: 2px;
    top: 0.5rem;
  }
  /* §16.5 c1-review R4 — honour `prefers-reduced-motion`. The
     §16.5 DESIGN brief calls reduced-motion out-of-scope, so any
     animation we add must be opt-in via the no-preference media
     query rather than retroactively gated later. */
  @media (prefers-reduced-motion: no-preference) {
    .skip-link {
      transition: top 0.15s ease-out;
    }
  }

  /* Global high-contrast focus ring. :focus-visible fires only
     when the user navigates via keyboard (not pointer click), so
     it doesn't add noise to mouse interactions while giving
     keyboard users a clear indicator across every element.
     Contrast #0066cc on white = 5.0:1, AA-passing for non-text. */
  :global(*:focus-visible) {
    outline: 3px solid #0066cc;
    outline-offset: 2px;
  }
  /* §16.5 c1-review R1 — skip-link target `<main id="main-content">`
     gets `tabindex="-1"` so activation moves keyboard focus ONTO
     <main>, not just the scroll position (WCAG 2.4.1 'Bypass
     Blocks'). The synthetic focus shouldn't draw the big blue
     ring around the whole content region, so suppress :focus on
     [tabindex="-1"] main. */
  :global(main[tabindex="-1"]:focus) {
    outline: none;
  }

  /* §16.5 c4 — visually-hidden utility for SR-only labels
     (e.g. empty action-column <th>s, icon-button context). Uses
     the standard clip+1px-box pattern so the node stays in the
     a11y tree (announced by SR) while taking zero visual space.
     Distinct from the skip-link's "hidden-until-focused" pattern:
     this one never appears. */
  :global(.visually-hidden) {
    border: 0;
    clip: rect(0, 0, 0, 0);
    height: 1px;
    margin: -1px;
    overflow: hidden;
    padding: 0;
    position: absolute;
    white-space: nowrap;
    width: 1px;
  }
</style>
