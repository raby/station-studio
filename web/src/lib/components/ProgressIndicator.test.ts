import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import ProgressIndicator from './ProgressIndicator.svelte';
import type { JobSnapshot } from '$lib/api';

/**
 * Pin <ProgressIndicator>'s rendering against each JobSnapshot state
 * and the cancel-event contract:
 *   - null job → nothing renders
 *   - status=running + phase ∈ {queued, start} → indeterminate bar
 *   - status=running + phase=picks → determinate bar widthed by
 *     current/total
 *   - status=done → label "Generating week N (10/10)" or default,
 *     no cancel button
 *   - status=error → error message visible
 *   - clicking Cancel dispatches the `cancel` event
 */

function makeSnap(over: Partial<JobSnapshot> = {}): JobSnapshot {
  return {
    job_id: 'j-1',
    slug: 'ts',
    kind: 'generate',
    status: 'running',
    phase: 'queued',
    current: 0,
    total: 10,
    label: null,
    result: null,
    error: null,
    started_at: '2026-05-27T10:00:00Z',
    finished_at: null,
    ...over,
  };
}


describe('<ProgressIndicator>', () => {
  beforeEach(() => {
    cleanup();
  });

  it('renders nothing when job is null', () => {
    const { container } = render(ProgressIndicator, { job: null });
    expect(container.querySelector('[data-testid="progress-indicator"]')).toBeNull();
  });

  it('renders an indeterminate bar when phase=queued', () => {
    render(ProgressIndicator, { job: makeSnap({ phase: 'queued' }) });
    expect(screen.getByTestId('bar-indeterminate')).toBeInTheDocument();
    expect(screen.queryByTestId('bar-determinate')).toBeNull();
    // Falls back to "Starting…" when label is null + phase is
    // indeterminate.
    expect(screen.getByTestId('progress-label')).toHaveTextContent('Starting…');
  });

  it('renders an indeterminate bar when phase=start (engine just emitted first event)', () => {
    render(ProgressIndicator, {
      job: makeSnap({
        phase: 'start',
        current: 0,
        total: 50,
        label: 'Generating week 1',
      }),
    });
    expect(screen.getByTestId('bar-indeterminate')).toBeInTheDocument();
    // Label from the engine wins over fallback.
    expect(screen.getByTestId('progress-label')).toHaveTextContent(
      'Generating week 1',
    );
  });

  it('renders a determinate bar widthed by current/total on phase=picks', () => {
    render(ProgressIndicator, {
      job: makeSnap({ phase: 'picks', current: 30, total: 100 }),
    });
    const bar = screen.getByTestId('bar-determinate');
    expect(bar).toBeInTheDocument();
    // Inline style includes the percent width.
    expect(bar.getAttribute('style')).toMatch(/width:\s*30%/);
    expect(screen.queryByTestId('bar-indeterminate')).toBeNull();
  });

  it('clamps percent to 100 when current > total (defensive)', () => {
    render(ProgressIndicator, {
      job: makeSnap({ phase: 'picks', current: 200, total: 100 }),
    });
    const bar = screen.getByTestId('bar-determinate');
    expect(bar.getAttribute('style')).toMatch(/width:\s*100%/);
  });

  it('falls back to phase/current/total when label is null', () => {
    render(ProgressIndicator, {
      job: makeSnap({ phase: 'picks', current: 5, total: 10, label: null }),
    });
    expect(screen.getByTestId('progress-label')).toHaveTextContent(
      'picks: 5 / 10',
    );
  });

  it('renders the cancel button while running', () => {
    render(ProgressIndicator, { job: makeSnap({ status: 'running' }) });
    expect(screen.getByTestId('progress-cancel')).toBeInTheDocument();
  });

  it('hides the cancel button on terminal statuses', () => {
    render(ProgressIndicator, {
      job: makeSnap({ status: 'done' }),
    });
    expect(screen.queryByTestId('progress-cancel')).toBeNull();
  });

  it('dispatches a `cancel` event on click', async () => {
    const onCancel = vi.fn();
    const { component } = render(ProgressIndicator, {
      job: makeSnap({ status: 'running' }),
    });
    component.$on('cancel', onCancel);
    await fireEvent.click(screen.getByTestId('progress-cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('shows error.kind + error.message when status=error', () => {
    render(ProgressIndicator, {
      job: makeSnap({
        status: 'error',
        error: { kind: 'RuntimeError', message: 'engine crashed' },
      }),
    });
    const err = screen.getByTestId('progress-error');
    expect(err).toHaveTextContent('RuntimeError');
    expect(err).toHaveTextContent('engine crashed');
  });

  it('sets aria-valuenow when determinate (a11y)', () => {
    render(ProgressIndicator, {
      job: makeSnap({ phase: 'picks', current: 25, total: 100 }),
    });
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '25');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
  });

  it('omits aria-valuenow when indeterminate (a11y)', () => {
    render(ProgressIndicator, {
      job: makeSnap({ phase: 'queued', current: 0, total: 0 }),
    });
    const bar = screen.getByRole('progressbar');
    expect(bar.getAttribute('aria-valuenow')).toBeNull();
  });

  it('exposes status as a data-attribute for outer-page styling hooks', () => {
    render(ProgressIndicator, { job: makeSnap({ status: 'running' }) });
    expect(screen.getByTestId('progress-indicator')).toHaveAttribute(
      'data-status',
      'running',
    );
  });
});
