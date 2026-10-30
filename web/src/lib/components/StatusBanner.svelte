<script lang="ts">
  /**
   * §16.5.t2 — sibling of `<ErrorBanner>` for success / info
   * announcements (e.g. "Wrote to /abs/.../cls/001.xml (847 KB)"
   * on the disk-export success path).
   *
   * Renders `role="status"` + `aria-live="polite"`. There is no
   * `assertive` variant — a successful completion never warrants
   * interrupting SR speech (per the §16.5 c5 codebase severity
   * policy: assertive is reserved for page-level hard stops
   * only). Callers wanting an urgent informational announcement
   * should use ErrorBanner with the "Warning" prefix instead.
   *
   * Renders nothing when `message` is null/empty so the live
   * region only fires when the announcement matters.
   *
   * Props mirror ErrorBanner exactly so the migration is
   * mechanical: same `message` / `prefix` / `testId` / `compact`
   * shape, no `severity` (always polite).
   */
  export let message: string | null = null;
  /** Pass the empty string to suppress the prefix entirely.
   *  Defaults to 'Success'. */
  export let prefix: string | null = null;
  /** Optional override for `data-testid`. Defaults to
   *  'status-banner'. Existing routes pinning a more specific id
   *  (e.g. 'disk-export-status') pass it here so e2e/vitest
   *  selectors keep working post-migration. */
  export let testId: string = 'status-banner';
  /** Compact mode strips the bordered-box background/border/margin
   *  in favour of a single inline-text line. Matches ErrorBanner's
   *  compact prop — use inside tight forms where the bordered
   *  banner is visually too heavy. */
  export let compact: boolean = false;

  $: resolvedPrefix = prefix === '' ? null : (prefix ?? 'Success');
</script>

{#if message}
  <div
    class="status-banner"
    class:compact
    role="status"
    aria-live="polite"
    data-testid={testId}
  >
    <!-- {' '}: Svelte 5 trims a plain space at the end of a block. -->
    {#if resolvedPrefix}<strong>{resolvedPrefix}:</strong>{' '}{/if}{message}
  </div>
{/if}

<style>
  /* Sized to match `<ErrorBanner>`'s default box so the two
     siblings read as a matched pair when one of them flips
     truthy on the same surface. Colour palette is the success
     green from §16.6 (`.disk-status.success`) so the migration
     of disk-export-status is byte-equivalent for sighted users. */
  .status-banner {
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--ok);
    font-size: 0.85rem;
    margin: 0.25rem 0 1rem;
    padding: 0.4rem 0.6rem;
  }
  .status-banner strong {
    color: var(--ok);
  }

  /* Compact: strip the box, keep the colour + aria-live. */
  .status-banner.compact {
    background: transparent;
    border: 0;
    border-radius: 0;
    margin: 0.25rem 0;
    padding: 0;
  }
</style>
