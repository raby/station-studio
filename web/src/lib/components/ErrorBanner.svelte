<script lang="ts">
  /**
   * §16.5 c1 — shared error banner for inline form / page errors.
   *
   * Centralises the three different inline-error stylings used
   * across the app (raw `<p class="error">`, `<details>`-nested,
   * `<data-testid>` sprinkled). One live region is announced to
   * screen readers; `severity` controls both the role (status vs
   * alert) and the announcement urgency.
   *
   * Renders nothing when `message` is null/empty so the live
   * region only fires when the announcement matters.
   *
   * Props:
   *   - `message` — the human-readable error string, or null to
   *     hide the banner.
   *   - `severity` — 'polite' (default; queued) for recoverable
   *     errors. 'assertive' interrupts the screen reader and is
   *     for hard failures that need immediate attention.
   *   - `prefix` — optional label read before the message;
   *     defaults from severity ('Notice' for polite, 'Error' for
   *     assertive) so a polite informational banner doesn't read
   *     aloud as "Error: ...". Override explicitly when neither
   *     fits ("Warning", "Saved", ...).
   *
   * §16.5 c1-review R2 — `role="status"` + `aria-live="polite"`
   * for non-urgent announcements; `role="alert"` + `aria-live=
   * "assertive"` for urgent. Mixing `role="alert"` with
   * `aria-live="polite"` is contradictory ARIA (alert implies
   * assertive); browsers + AT pairs resolve the conflict
   * differently. Picking the matched pair removes the ambiguity.
   *
   * Untagged §16.5 c0 backend errors (the `errors[*]` with
   * `field: null`) land here; tagged-field errors render inline
   * via `aria-describedby` on the offending input (c3).
   */
  export let message: string | null = null;
  export let severity: 'polite' | 'assertive' = 'polite';
  /** Pass the empty string to suppress the prefix entirely (e.g.
   *  inline-form errors where the surrounding context already
   *  tells the operator what action failed). Passing null falls
   *  back to the severity-derived default. */
  export let prefix: string | null = null;
  /** Optional override for the `data-testid` on the rendered
   *  banner. Defaults to 'error-banner'; existing routes that
   *  already pin a more specific id (e.g. 'rollback-error',
   *  'disk-export-error') pass it here so their e2e/vitest
   *  selectors keep working post-migration.
   *
   *  **When rendering more than one banner on the same page,
   *  always pass a unique `testId`.** Two unset defaults
   *  produce duplicate `data-testid="error-banner"` elements;
   *  vitest's `getByTestId` throws and Playwright's silently
   *  returns the first match. */
  export let testId: string = 'error-banner';
  /** §16.5 c4-review R1 — compact mode drops the bordered-box
   *  background/border/margin in favour of a single inline-text
   *  line. Use inside tight forms where the bordered banner is
   *  visually too heavy (e.g. the §16.5 c4 fork-error sat
   *  inside a `.fork-form` flex column with `gap: 0.5rem`; the
   *  default banner's `margin: 1rem` bottom doubled that gap
   *  and pushed the submit/cancel buttons down on every retry). */
  export let compact: boolean = false;

  $: resolvedPrefix =
    prefix === ''
      ? null
      : prefix ?? (severity === 'assertive' ? 'Error' : 'Notice');
  $: role = severity === 'assertive' ? 'alert' : 'status';
</script>

{#if message}
  <div
    class="error-banner"
    class:assertive={severity === 'assertive'}
    class:compact
    {role}
    aria-live={severity}
    data-testid={testId}
  >
    {#if resolvedPrefix}<strong>{resolvedPrefix}:</strong> {/if}{message}
  </div>
{/if}

<style>
  /* §16.5 c2-review R1 — sized to match the pre-migration inline
     error rules (`.rollback-error`, `.tags-error`, `.disk-status
     .error`) so migrating routes don't punch a visibly larger
     box than the `<p class="error">` they replace. Sighted
     users perceive the migration as no-change; SR users get
     the new aria-live region. */
  .error-banner {
    background: #fee;
    border: 1px solid #ffcdd2;
    border-radius: 3px;
    color: #b71c1c;
    font-size: 0.85rem;
    margin: 0.25rem 0 1rem;
    padding: 0.4rem 0.6rem;
  }
  .error-banner strong {
    color: #b71c1c;
  }

  /* §16.5 c4-review R1 — compact mode strips the bordered box
     and lets the message sit inline at the prior `.fork-error`
     weight (color + small font, no margin). The aria-live region
     stays — only the visual chrome changes. */
  .error-banner.compact {
    background: transparent;
    border: 0;
    border-radius: 0;
    margin: 0.25rem 0;
    padding: 0;
  }
</style>
