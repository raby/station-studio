/**
 * §16.2.5 c2 — <SectionHeader> tests.
 *
 * c1 §16.2.2 ships the toggle + name + count. c2 §16.2.5 adds
 * inline rename, Move up / Move down, and Delete affordances —
 * with the system `_unsorted` bin suppressing all four.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { tick } from 'svelte';
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { renderWithEvents } from '$lib/renderWithEvents';
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
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    on('rename', handler);
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
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    on('rename', handler);
    await fireEvent.click(screen.getByTestId('section-rename'));
    const input = screen.getByTestId('section-rename-input');
    await fireEvent.input(input, { target: { value: 'Discarded' } });
    await fireEvent.keyDown(input, { key: 'Escape' });
    expect(handler).not.toHaveBeenCalled();
    expect(screen.getByTestId('section-name')).toBeInTheDocument();
  });

  it('blank rename is a no-op (not a dispatch of empty string)', async () => {
    const handler = vi.fn();
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    on('rename', handler);
    await fireEvent.click(screen.getByTestId('section-rename'));
    const input = screen.getByTestId('section-rename-input');
    await fireEvent.input(input, { target: { value: '   ' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(handler).not.toHaveBeenCalled();
  });

  it('clicking ▲ dispatches moveUp with the section id', async () => {
    const handler = vi.fn();
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
      canMoveUp: true, canMoveDown: true,
    });
    on('moveUp', handler);
    await fireEvent.click(screen.getByTestId('section-move-up'));
    expect(handler.mock.calls[0][0].detail).toEqual({ sectionId: 'lead' });
  });

  it('clicking 🗑 dispatches delete', async () => {
    const handler = vi.fn();
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    on('delete', handler);
    await fireEvent.click(screen.getByTestId('section-delete'));
    expect(handler.mock.calls[0][0].detail).toEqual({ sectionId: 'lead' });
  });

  // c2 review #4: blur fires on the input as it unmounts after
  // Enter commits — without the `committed` latch the blur handler
  // would dispatch a SECOND identical rename event. Real browsers
  // fire blur even when the element is being removed; jsdom doesn't,
  // so this test seeds the bug condition by firing blur explicitly.
  it('Enter then blur on the unmounting input does NOT double-dispatch', async () => {
    const handler = vi.fn();
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    on('rename', handler);
    await fireEvent.click(screen.getByTestId('section-rename'));
    const input = screen.getByTestId('section-rename-input');
    await fireEvent.input(input, { target: { value: 'Opening' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    // Simulate the post-Enter blur browsers fire on the unmounting
    // input (jsdom doesn't replay this naturally; the latch must
    // suppress the second commitRename either way).
    await fireEvent.blur(input);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('Escape then blur does NOT dispatch from the cancel path', async () => {
    const handler = vi.fn();
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    on('rename', handler);
    await fireEvent.click(screen.getByTestId('section-rename'));
    const input = screen.getByTestId('section-rename-input');
    await fireEvent.input(input, { target: { value: 'Discarded' } });
    await fireEvent.keyDown(input, { key: 'Escape' });
    await fireEvent.blur(input);
    expect(handler).not.toHaveBeenCalled();
  });
});


// ---- §16.2.5 c2 — drag-drop tests ---------------------------------------
// c2 review #4 #6: synthetic DragEvent builder hoisted to
// __tests__/drag-fixture.ts so the matching builder in Chip.test.ts
// can't drift.
import { makeDragEvent } from './__tests__/drag-fixture';


describe('<SectionHeader> — c2 drag-drop', () => {
  beforeEach(() => cleanup());

  it('dragover with our MIME calls preventDefault → drop is enabled', async () => {
    render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    const header = screen.getByTestId('section-header');
    const e = makeDragEvent(
      'dragover',
      'application/x-station-chip-ref',
      JSON.stringify({ kind: 'atomic-ref', slot_index: 0 }),
    );
    header.dispatchEvent(e);
    // preventDefault means the browser permits the drop event later.
    expect(e.defaultPrevented).toBe(true);
  });

  it('dragover with a different MIME is ignored (no preventDefault)', async () => {
    render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    const header = screen.getByTestId('section-header');
    const e = makeDragEvent('dragover', 'text/plain', 'hello');
    header.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(false);
  });

  it('dragenter flips the visual drag-over state on', async () => {
    render(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    const header = screen.getByTestId('section-header');
    expect(header).toHaveAttribute('data-drag-over', 'false');
    header.dispatchEvent(makeDragEvent(
      'dragenter',
      'application/x-station-chip-ref',
      JSON.stringify({ kind: 'atomic-ref', slot_index: 0 }),
    ));
    await tick();
    expect(header).toHaveAttribute('data-drag-over', 'true');
  });

  it('drop dispatches `chipDrop` with the JSON-decoded ref + section id', async () => {
    const handler = vi.fn();
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    on('chipDrop', handler);
    const header = screen.getByTestId('section-header');
    const payload = { kind: 'atomic-ref' as const, slot_index: 3 };
    header.dispatchEvent(makeDragEvent(
      'drop',
      'application/x-station-chip-ref',
      JSON.stringify(payload),
    ));
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual({
      ref: payload,
      targetSectionId: 'lead',
    });
  });

  it('drop with malformed JSON does NOT dispatch chipDrop', async () => {
    const handler = vi.fn();
    const { on } = renderWithEvents(SectionHeader, {
      section: SEC, chipCount: 1, expanded: true,
    });
    on('chipDrop', handler);
    const header = screen.getByTestId('section-header');
    header.dispatchEvent(makeDragEvent(
      'drop',
      'application/x-station-chip-ref',
      '{ not-valid-json',
    ));
    expect(handler).not.toHaveBeenCalled();
  });
});
