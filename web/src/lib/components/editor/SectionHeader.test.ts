/**
 * §16.2.5 c2 — <SectionHeader> tests.
 *
 * c1 §16.2.2 ships the toggle + name + count. c2 §16.2.5 adds
 * inline rename, Move up / Move down, and Delete affordances —
 * with the system `_unsorted` bin suppressing all four.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import SectionHeader from './SectionHeader.svelte';
import type { SidecarSection } from '$lib/editor';

afterEach(() => cleanup());

const SEC: SidecarSection = { id: 'lead', name: 'Lead', blocks: [] };

describe('<SectionHeader> — §16.2.5 c2 affordances', () => {
  it('shows the actions row for normal sections', () => {
    render(SectionHeader, {
      section: SEC, chipCount: 3, expanded: true,
      canMoveUp: true, canMoveDown: true,
    });
    expect(screen.getByTestId('section-rename')).toBeInTheDocument();
    expect(screen.getByTestId('section-move-up')).toBeInTheDocument();
    expect(screen.getByTestId('section-move-down')).toBeInTheDocument();
    expect(screen.getByTestId('section-delete')).toBeInTheDocument();
  });

  it('hides the actions row for the system `_unsorted` bin', () => {
    const bin: SidecarSection = { id: '_unsorted', name: 'Unsorted', blocks: [] };
    render(SectionHeader, {
      section: bin, chipCount: 3, expanded: true,
    });
    expect(screen.queryByTestId('section-rename')).toBeNull();
    expect(screen.queryByTestId('section-delete')).toBeNull();
  });

  it('disables Move up at the top of layout_order', () => {
    render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
      canMoveUp: false, canMoveDown: true,
    });
    expect(screen.getByTestId('section-move-up')).toBeDisabled();
    expect(screen.getByTestId('section-move-down')).toBeEnabled();
  });

  it('disables Move down at the bottom of layout_order', () => {
    render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
      canMoveUp: true, canMoveDown: false,
    });
    expect(screen.getByTestId('section-move-up')).toBeEnabled();
    expect(screen.getByTestId('section-move-down')).toBeDisabled();
  });

  it('clicking ✎ swaps the name for an inline input', async () => {
    render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
      canMoveUp: false, canMoveDown: false,
    });
    await fireEvent.click(screen.getByTestId('section-rename'));
    const input = screen.getByTestId('section-rename-input') as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe('Lead');
    // Name span is HIDDEN while editing.
    expect(screen.queryByTestId('section-name')).toBeNull();
  });

  it('Enter commits the rename + dispatches a `rename` event', async () => {
    const handler = vi.fn();
    const { component } = render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    component.$on('rename', handler);
    await fireEvent.click(screen.getByTestId('section-rename'));
    const input = screen.getByTestId('section-rename-input');
    await fireEvent.input(input, { target: { value: 'Opening' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({
      sectionId: 'lead', name: 'Opening',
    });
  });

  it('Escape cancels the rename without dispatching', async () => {
    const handler = vi.fn();
    const { component } = render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    component.$on('rename', handler);
    await fireEvent.click(screen.getByTestId('section-rename'));
    const input = screen.getByTestId('section-rename-input');
    await fireEvent.input(input, { target: { value: 'Discarded' } });
    await fireEvent.keyDown(input, { key: 'Escape' });
    expect(handler).not.toHaveBeenCalled();
    expect(screen.getByTestId('section-name')).toBeInTheDocument();
  });

  it('blank rename is a no-op (not a dispatch of empty string)', async () => {
    const handler = vi.fn();
    const { component } = render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    component.$on('rename', handler);
    await fireEvent.click(screen.getByTestId('section-rename'));
    const input = screen.getByTestId('section-rename-input');
    await fireEvent.input(input, { target: { value: '   ' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(handler).not.toHaveBeenCalled();
  });

  it('clicking ▲ dispatches moveUp with the section id', async () => {
    const handler = vi.fn();
    const { component } = render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
      canMoveUp: true, canMoveDown: true,
    });
    component.$on('moveUp', handler);
    await fireEvent.click(screen.getByTestId('section-move-up'));
    expect(handler.mock.calls[0][0].detail).toEqual({ sectionId: 'lead' });
  });

  it('clicking 🗑 dispatches delete', async () => {
    const handler = vi.fn();
    const { component } = render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    component.$on('delete', handler);
    await fireEvent.click(screen.getByTestId('section-delete'));
    expect(handler.mock.calls[0][0].detail).toEqual({ sectionId: 'lead' });
  });
});
