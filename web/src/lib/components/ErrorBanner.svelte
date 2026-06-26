<script lang="ts">
  /**
   * §16.5 c1 — shared error banner for inline form / page errors.
   *
   * Centralises the three different inline-error stylings used
   * across the app (raw `<p class="error">`, `<details>`-nested,
   * <data-testid> sprinkled). One `role="alert"` + aria-live
   * region is announced to screen readers; `severity` controls
   * the announcement urgency.
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
   *   - `prefix` — optional label (e.g. 'Error', 'Warning') read
   *     before the message; defaults to 'Error'.
   *
   * Untagged §16.5 c0 backend errors (the `errors[*]` with
   * `field: null`) land here; tagged-field errors render inline
   * via `aria-describedby` on the offending input (c3).
   */
  export let message: string | null = null;
  export let severity: 'polite' | 'assertive' = 'polite';
  export let prefix: string = 'Error';
</script>

{#if message}
  <div
    class="error-banner"
    role="alert"
    aria-live={severity}
    data-testid="error-banner"
  >
    <strong>{prefix}:</strong> {message}
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
