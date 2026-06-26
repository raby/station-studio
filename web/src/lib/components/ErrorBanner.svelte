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
  export let prefix: string | null = null;
  /** Optional override for the `data-testid` on the rendered
   *  banner. Defaults to 'error-banner'; existing routes that
   *  already pin a more specific id (e.g. 'rollback-error',
   *  'disk-export-error') pass it here so their e2e/vitest
   *  selectors keep working post-migration. */
  export let testId: string = 'error-banner';

  $: resolvedPrefix = prefix ?? (severity === 'assertive' ? 'Error' : 'Notice');
  $: role = severity === 'assertive' ? 'alert' : 'status';
</script>

{#if message}
  <div
    class="error-banner"
    class:assertive={severity === 'assertive'}
    {role}
    aria-live={severity}
    data-testid={testId}
  >
    <strong>{resolvedPrefix}:</strong> {message}
  </div>
{/if}

<style>
  .error-banner {
    background: #fee;
    border: 1px solid #c00;
    border-radius: 4px;
    color: #800;
    margin: 0.75rem 0;
    padding: 0.5rem 0.75rem;
  }
  .error-banner strong {
    color: #800;
  }
</style>
