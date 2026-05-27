import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import ViewModeToggle from './ViewModeToggle.svelte';
import { DEFAULT_VIEW_MODE, type ViewModeState } from '$lib/editor';


describe('<ViewModeToggle>', () => {
  beforeEach(() => cleanup());

  it('renders the active pill per dimension', () => {
    render(ViewModeToggle, { value: DEFAULT_VIEW_MODE });
    // Defaults: compact + source + wider-chip.
    expect(screen.getByTestId('vm-label-compact')).toHaveAttribute(
      'aria-pressed', 'true',
    );
    expect(screen.getByTestId('vm-color-source')).toHaveAttribute(
      'aria-pressed', 'true',
    );
    expect(screen.getByTestId('vm-macro-wider')).toHaveAttribute(
      'aria-pressed', 'true',
    );
    // Inactive pills:
    expect(screen.getByTestId('vm-label-verbose')).toHaveAttribute(
      'aria-pressed', 'false',
    );
  });

  it('clicking a pill toggles the active aria-pressed flip', async () => {
    // Svelte 4 props aren't directly readable without accessors; we
    // assert the visible UI change instead (which is the contract
    // any user-of-the-component would observe via the bound value
    // anyway — aria-pressed mirrors `value.labelMode`).
    render(ViewModeToggle, { value: { ...DEFAULT_VIEW_MODE } });
    expect(screen.getByTestId('vm-label-compact')).toHaveAttribute(
      'aria-pressed', 'true',
    );
    await fireEvent.click(screen.getByTestId('vm-label-verbose'));
    expect(screen.getByTestId('vm-label-verbose')).toHaveAttribute(
      'aria-pressed', 'true',
    );
    expect(screen.getByTestId('vm-label-compact')).toHaveAttribute(
      'aria-pressed', 'false',
    );
  });

  it('three rows are present', () => {
    render(ViewModeToggle, { value: DEFAULT_VIEW_MODE });
    expect(screen.getByTestId('view-mode-toggle')).toBeInTheDocument();
    // Three pill groups, one per row.
    expect(screen.getAllByRole('group')).toHaveLength(3);
  });

  it('all three macro-visual options render', () => {
    render(ViewModeToggle, { value: DEFAULT_VIEW_MODE });
    expect(screen.getByTestId('vm-macro-wider')).toBeInTheDocument();
    expect(screen.getByTestId('vm-macro-stacked')).toBeInTheDocument();
    expect(screen.getByTestId('vm-macro-badge')).toBeInTheDocument();
  });
});
