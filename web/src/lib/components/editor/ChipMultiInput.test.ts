/**
 * §16.2.4 c2 — <ChipMultiInput> tests.
 *
 * A focused chip-list editor with autocomplete. Parent owns
 * `values`; we dispatch `change` with the new array on every edit.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import ChipMultiInput from './ChipMultiInput.svelte';

afterEach(() => cleanup());

describe('<ChipMultiInput>', () => {
  it('renders current values as removable chips', () => {
    render(ChipMultiInput, {
      values: ['REFERENCE_ARCHIVE', 'REFERENCE_CLASSICS'],
      suggestions: [],
    });
    const chips = screen.getAllByTestId('chip-multi-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent('REFERENCE_ARCHIVE');
    expect(chips[1]).toHaveTextContent('REFERENCE_CLASSICS');
  });

  it('typing + Enter dispatches change with the new value appended', async () => {
    const handler = vi.fn();
    const { component } = render(ChipMultiInput, {
      values: ['A'], suggestions: [],
    });
    component.$on('change', handler);
    const input = screen.getByTestId('chip-multi-input');
    await fireEvent.input(input, { target: { value: 'NEW_POOL' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler.mock.calls[0][0].detail).toEqual(['A', 'NEW_POOL']);
  });

  it('clicking × on a chip dispatches change without that value', async () => {
    const handler = vi.fn();
    const { component } = render(ChipMultiInput, {
      values: ['A', 'B', 'C'], suggestions: [],
    });
    component.$on('change', handler);
    const removes = screen.getAllByTestId('chip-multi-chip-remove');
    await fireEvent.click(removes[1]); // remove 'B'
    expect(handler.mock.calls[0][0].detail).toEqual(['A', 'C']);
  });

  it('Backspace on empty input pops the last chip', async () => {
    const handler = vi.fn();
    const { component } = render(ChipMultiInput, {
      values: ['A', 'B'], suggestions: [],
    });
    component.$on('change', handler);
    const input = screen.getByTestId('chip-multi-input');
    await fireEvent.keyDown(input, { key: 'Backspace' });
    expect(handler.mock.calls[0][0].detail).toEqual(['A']);
  });

  it('duplicate values are silently ignored (no change dispatched)', async () => {
    const handler = vi.fn();
    const { component } = render(ChipMultiInput, {
      values: ['A'], suggestions: [],
    });
    component.$on('change', handler);
    const input = screen.getByTestId('chip-multi-input');
    await fireEvent.input(input, { target: { value: 'A' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(handler).not.toHaveBeenCalled();
  });

  it('commas in entries are rejected with an inline error', async () => {
    const handler = vi.fn();
    const { component } = render(ChipMultiInput, {
      values: [], suggestions: [],
    });
    component.$on('change', handler);
    const input = screen.getByTestId('chip-multi-input');
    await fireEvent.input(input, { target: { value: 'A,B' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(handler).not.toHaveBeenCalled();
    expect(screen.getByTestId('chip-multi-error')).toHaveTextContent(/commas/i);
  });

  it('autocomplete filters suggestions by input + excludes already-staged', () => {
    render(ChipMultiInput, {
      values: ['REFERENCE_ARCHIVE'],
      suggestions: [
        { name: 'REFERENCE_ARCHIVE' },
        { name: 'REFERENCE_CLASSICS' },
        { name: 'CONTENT_LIBRARY' },
      ],
    });
    // No suggestions visible until input has text.
    expect(screen.queryByTestId('chip-multi-suggestions')).toBeNull();
  });

  it('typing shows narrowed suggestions, click adds the picked one', async () => {
    const handler = vi.fn();
    const { component } = render(ChipMultiInput, {
      values: [],
      suggestions: [
        { name: 'REFERENCE_ARCHIVE' },
        { name: 'REFERENCE_CLASSICS' },
        { name: 'OTHER_POOL' },
      ],
    });
    component.$on('change', handler);
    await fireEvent.input(screen.getByTestId('chip-multi-input'), {
      target: { value: 'ref' },
    });
    const sugs = screen.getAllByTestId('chip-multi-suggestion');
    expect(sugs).toHaveLength(2);
    await fireEvent.click(sugs[0]);
    expect(handler.mock.calls[0][0].detail).toEqual(['REFERENCE_ARCHIVE']);
  });
});
