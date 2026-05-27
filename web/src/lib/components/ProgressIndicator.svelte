<script lang="ts">
  /**
   * <ProgressIndicator> — §16.11 c4.
   *
   * Visible while a generation job is in flight. Reads a JobSnapshot
   * each poll (consumer drives `watchJob`) and renders:
   *
   *   * A progress bar [width = current / total].
   *   * A status label (job.label if present, else built from
   *     phase / current / total).
   *   * A Cancel button.
   *
   * States rendered:
   *   - status="running" + phase ∈ {"queued","start"} → indeterminate
   *     ("Starting…") because current=0/total may be unset before the
   *     engine emits its first picks event.
   *   - status="running" + phase="picks" → bar widthed by current/total.
   *   - status="done"/"error"/"cancelled" → consumer typically hides
   *     the indicator; we still render a final state row for one tick.
   *   - job=null → render nothing (no active job).
   *
   * The component is dumb: it owns no fetch / polling. The page wires
   * `watchJob` and passes snapshots through; cancel emits an event the
   * page handles (calls cancelJob + clears its local job reference).
   */
  import { createEventDispatcher } from 'svelte';
  import type { JobSnapshot } from '$lib/api';

  export let job: JobSnapshot | null = null;

  const dispatch = createEventDispatcher<{ cancel: void }>();

  // Indeterminate phases (mid-slice review 🟢-6) — the engine hasn't
  // started emitting `picks` events yet. The bar shows an animated
  // "indeterminate" stripe rather than a fixed 0% width that looks
  // stuck.
  $: indeterminate =
    job !== null &&
    job.status === 'running' &&
    (job.phase === 'queued' || job.phase === 'start' || job.total === 0);

  // % width for the determinate bar. Guard against total=0 (defensive,
  // shouldn't happen on phase=picks but the engine could be wrong).
  $: percent =
    job && job.total > 0
      ? Math.min(100, Math.max(0, (job.current / job.total) * 100))
      : 0;

  // Visible label: prefer the engine-provided one (e.g. "Generating
  // week 5"); fall back to a phase/current/total summary.
  $: label = (() => {
    if (!job) return '';
    if (job.label) return job.label;
    if (indeterminate) return 'Starting…';
    return `${job.phase}: ${job.current} / ${job.total}`;
  })();

  function onCancel() {
    dispatch('cancel');
  }
</script>

{#if job !== null}
  <div class="progress" data-testid="progress-indicator" data-status={job.status}>
    <div class="bar-track" role="progressbar"
         aria-valuemin="0" aria-valuemax="100"
         aria-valuenow={indeterminate ? undefined : Math.round(percent)}
         aria-label={label}>
      {#if indeterminate}
        <div class="bar bar-indeterminate" data-testid="bar-indeterminate"></div>
      {:else}
        <div class="bar bar-determinate"
             data-testid="bar-determinate"
             style="width: {percent}%"></div>
      {/if}
    </div>
    <span class="label" data-testid="progress-label">{label}</span>
    {#if job.status === 'running'}
      <button type="button" class="cancel" on:click={onCancel}
              data-testid="progress-cancel">
        Cancel
      </button>
    {/if}
    {#if job.status === 'error' && job.error}
      <span class="error" data-testid="progress-error">
        {job.error.kind}: {job.error.message}
      </span>
    {/if}
  </div>
{/if}

<style>
  .progress {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--border, #ddd);
    border-radius: 6px;
    background: var(--bg-subtle, #fafafa);
  }

  .bar-track {
    flex: 1;
    min-width: 8rem;
    height: 0.5rem;
    background: var(--bg-bar-track, #eee);
    border-radius: 3px;
    overflow: hidden;
    position: relative;
  }

  .bar {
    height: 100%;
    background: var(--accent, #4a90e2);
    border-radius: 3px;
    transition: width 200ms ease-out;
  }

  .bar-indeterminate {
    width: 33%;
    animation: indeterminate-slide 1.2s linear infinite;
  }

  @keyframes indeterminate-slide {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(300%); }
  }

  .label {
    font-size: 0.875rem;
    color: var(--text-muted, #555);
    white-space: nowrap;
  }

  .cancel {
    font-size: 0.85rem;
    padding: 0.25rem 0.6rem;
    background: transparent;
    border: 1px solid var(--border, #ccc);
    border-radius: 4px;
    cursor: pointer;
  }
  .cancel:hover {
    background: var(--bg-hover, #f0f0f0);
  }

  .error {
    color: var(--error, #c0392b);
    font-size: 0.85rem;
  }
</style>
