<script lang="ts">
  /**
   * §16.20 c1 — render a "wider screen required" panel below
   * `--mobile-breakpoint` (40rem / 640px). Used by surfaces
   * that are fundamentally desktop-only — primarily the
   * `/stations/[slug]/visualise` drag-drop editor (§16.20 D2
   * lock) but designed to be reusable for any future
   * desktop-only surface.
   *
   * Pattern: render both the notice AND the desktop content;
   * CSS media queries hide one or the other based on viewport
   * width. The desktop content's JS still runs below 640px
   * (mount cost paid even when invisible) — acceptable for
   * c1; if a future profile shows it matters, add an
   * `if (window.matchMedia('(max-width: 40rem)').matches)
   * return` early in the heavy onMount and a corresponding
   * resize listener.
   *
   * Container is a `<section aria-labelledby>` — a labelled
   * <section> is implicitly a region landmark, so SR users hear
   * it as a landmark with the heading as its accessible name.
   * The back-link is a plain `<a>` styled as a button; click
   * target is large enough (44x44 minimum) per WCAG 2.5.5.
   *
   * Props mirror an info banner — caller supplies the heading
   * text, the body explanation, and the back-link href +
   * label. Defaults aim at the visualise editor (the only c1
   * consumer) but are overridable.
   */
  export let heading: string = 'This view needs a wider screen';
  export let body: string =
    'The visual block editor uses drag-and-drop affordances that don’t translate to narrow viewports. Open this page on a desktop or tablet (640px wide or larger).';
  export let backHref: string;
  export let backLabel: string = '← Back';
  /** Optional override for the `data-testid` so an e2e/vitest
   *  selector can pin the notice on a specific surface
   *  (`visualise-narrow-notice`, etc). */
  export let testId: string = 'narrow-screen-notice';

  // Generate a stable id for the heading so the section's
  // `aria-labelledby` resolves without callers having to thread
  // a unique id when multiple notices stack on the same page.
  // crypto.randomUUID is available in every modern browser and
  // in jsdom; the slice doesn't run in older UAs.
  const headingId = `narrow-notice-${crypto.randomUUID()}`;
</script>

<section
  class="narrow-screen-notice"
  aria-labelledby={headingId}
  data-testid={testId}
>
  <h2 id={headingId}>{heading}</h2>
  <p>{body}</p>
  <a class="back-link" href={backHref}>{backLabel}</a>
</section>

<style>
  /* §16.20 c1 — only visible below the mobile breakpoint.
     Desktop renders nothing; mobile gets the full panel. The
     companion desktop content (caller-side) inverts the
     pattern. Single 40rem breakpoint per §16.20 D1. */
  .narrow-screen-notice {
    display: none;
  }
  @media (max-width: 40rem) {
    .narrow-screen-notice {
      display: block;
      background: #f5f7fa;
      border: 1px solid #d4dae3;
      border-radius: 6px;
      color: #3a3f4b;
      margin: 2rem auto;
      max-width: 90%;
      padding: 1.25rem 1rem;
      text-align: center;
    }
  }
  .narrow-screen-notice h2 {
    font-size: 1.1rem;
    margin: 0 0 0.5rem;
  }
  .narrow-screen-notice p {
    font-size: 0.95rem;
    line-height: 1.4;
    margin: 0 0 1rem;
  }
  /* 44x44 minimum target per WCAG 2.5.5 — `min-height/width`
     guarantees the hit area even if the visible text is short. */
  .back-link {
    background: #2c3e50;
    border-radius: 4px;
    color: #fff;
    display: inline-block;
    font-size: 0.95rem;
    min-height: 44px;
    min-width: 44px;
    padding: 0.6rem 1rem;
    text-decoration: none;
  }
  .back-link:hover {
    background: #1a252f;
  }
</style>
