import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, cleanup, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

/**
 * §16.14.t11 — <ListRuleRow> chip editor unit tests.
 *
 * Each test renders the component in isolation with mocked $lib/api
 * calls (listSources / setStationRule / clearStationRule). The
 * parent's `updated` event is captured via the `on:updated` callback.
 */

const {
  listSourcesMock,
  setStationRuleMock,
  clearStationRuleMock,
  FakeApiError,
} = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }
  return {
    listSourcesMock: vi.fn().mockResolvedValue([]),
    setStationRuleMock: vi.fn(),
    clearStationRuleMock: vi.fn(),
    FakeApiError,
  };
});

vi.mock('$lib/api', () => ({
  listSources: (...a: unknown[]) => listSourcesMock(...a),
  setStationRule: (...a: unknown[]) => setStationRuleMock(...a),
  clearStationRule: (...a: unknown[]) => clearStationRuleMock(...a),
  ApiError: FakeApiError,
}));

import ListRuleRow from './ListRuleRow.svelte';
import type { Rule, Source } from '$lib/api';

/** Build a list-kind Rule for tests, with overridable defaults. */
function listRule(over: Partial<Rule> = {}): Rule {
  return {
    key: 'cross_pool_dedup_sources',
    kind: 'list',
    value: [],
    default_value: [],
    description: 'List of source pool names to exclude.',
    max_value: null,
    is_override: false,
    ...over,
  };
}

function source(over: Partial<Source> = {}): Source {
  return {
    id: 1,
    name: 'REFERENCE_ARCHIVE',
    role: 'reference',
    track_count: 12,
    ...over,
  };
}

describe('<ListRuleRow>', () => {
  beforeEach(() => {
    cleanup();
    listSourcesMock.mockReset().mockResolvedValue([]);
    setStationRuleMock.mockReset();
    clearStationRuleMock.mockReset();
  });

  // ---------- render ----------

  it('renders a chip per value entry', async () => {
    const rule = listRule({ value: ['A', 'B'], is_override: true });
    render(ListRuleRow, { slug: 'rb', rule });

    await screen.findByTestId('list-rule-row');
    const chips = screen.getAllByTestId('list-rule-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent).toContain('A');
    expect(chips[1].textContent).toContain('B');
  });

  it('renders an empty-state message when staged is empty', async () => {
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    await screen.findByTestId('list-rule-row');
    expect(screen.getByTestId('list-rule-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('list-rule-chip')).not.toBeInTheDocument();
  });

  // ---------- add ----------

  it('adds a chip via the Add button after typing a name', async () => {
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'NEW_POOL' } });
    await fireEvent.click(screen.getByTestId('list-rule-add'));
    expect(screen.getAllByTestId('list-rule-chip')).toHaveLength(1);
    // Input is cleared after add.
    expect((input as HTMLInputElement).value).toBe('');
  });

  it('adds a chip via Enter on the input (free-form path)', async () => {
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'FREEFORM_POOL' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    const chips = screen.getAllByTestId('list-rule-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain('FREEFORM_POOL');
  });

  it('adding a chip already present is a no-op (idempotent)', async () => {
    const rule = listRule({ value: ['EXISTING'], is_override: true });
    render(ListRuleRow, { slug: 'rb', rule });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'EXISTING' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getAllByTestId('list-rule-chip')).toHaveLength(1);
  });

  it('rejects a chip name containing a comma at input time (c1 review F5)', async () => {
    // The backend would 400 with "separator character" because
    // the on-disk format is comma-separated. Catching at input
    // time gives the operator instant feedback.
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'Rare, Imports' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    // No chip added; error shown.
    expect(screen.queryByTestId('list-rule-chip')).not.toBeInTheDocument();
    expect(screen.getByTestId('list-rule-error')).toHaveTextContent(/comma/i);
  });

  it('Backspace on an empty input deletes the last chip', async () => {
    const rule = listRule({ value: ['A', 'B'], is_override: true });
    render(ListRuleRow, { slug: 'rb', rule });
    const input = await screen.findByTestId('list-rule-input');
    // Input is already empty by default.
    await fireEvent.keyDown(input, { key: 'Backspace' });
    const chips = screen.getAllByTestId('list-rule-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain('A');
  });

  // ---------- remove ----------

  it('removes a chip via its × button', async () => {
    const rule = listRule({ value: ['A', 'B'], is_override: true });
    render(ListRuleRow, { slug: 'rb', rule });
    await screen.findByTestId('list-rule-row');
    const removeButtons = screen.getAllByTestId('list-rule-chip-remove');
    await fireEvent.click(removeButtons[0]);
    const chips = screen.getAllByTestId('list-rule-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain('B');
  });

  // ---------- dirty tracking + Save ----------

  it('Save button is disabled when staged == saved (clean state)', async () => {
    const rule = listRule({ value: ['A'], is_override: true });
    render(ListRuleRow, { slug: 'rb', rule });
    const save = await screen.findByTestId('list-rule-save');
    expect(save).toBeDisabled();
  });

  it('Save button enables after adding a chip', async () => {
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'POOL_A' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    const save = screen.getByTestId('list-rule-save');
    expect(save).not.toBeDisabled();
  });

  it('Save PUTs the staged value + emits updated event with the response', async () => {
    const updatedRule = listRule({ value: ['POOL_A'], is_override: true });
    setStationRuleMock.mockResolvedValueOnce(updatedRule);
    let captured: Rule | null = null;
    const { component } = render(ListRuleRow, {
      slug: 'rb',
      rule: listRule(),
    });
    component.$on('updated', (e: CustomEvent<Rule>) => {
      captured = e.detail;
    });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'POOL_A' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    await fireEvent.click(screen.getByTestId('list-rule-save'));
    await waitFor(() => {
      expect(setStationRuleMock).toHaveBeenCalledWith('rb', 'cross_pool_dedup_sources', ['POOL_A']);
    });
    expect(captured).toEqual(updatedRule);
  });

  it('Save shows the API error inline on failure', async () => {
    setStationRuleMock.mockRejectedValueOnce(new FakeApiError(400, 'must be a list'));
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'POOL_A' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    await fireEvent.click(screen.getByTestId('list-rule-save'));
    await waitFor(() =>
      expect(screen.getByTestId('list-rule-error')).toHaveTextContent('must be a list'),
    );
  });

  // ---------- Reset ----------

  it('Reset button is HIDDEN when is_override is false (D10 no-op suppression)', async () => {
    render(ListRuleRow, { slug: 'rb', rule: listRule({ is_override: false }) });
    await screen.findByTestId('list-rule-row');
    expect(screen.queryByTestId('list-rule-reset')).not.toBeInTheDocument();
  });

  it('Reset button SHOWS when is_override is true', async () => {
    render(
      ListRuleRow,
      { slug: 'rb', rule: listRule({ value: ['A'], is_override: true }) },
    );
    await screen.findByTestId('list-rule-row');
    expect(screen.getByTestId('list-rule-reset')).toBeInTheDocument();
  });

  it('Reset DELETEs + emits updated with default_value + is_override=false', async () => {
    clearStationRuleMock.mockResolvedValueOnce(undefined);
    let captured: Rule | null = null;
    const { component } = render(
      ListRuleRow,
      {
        slug: 'rb',
        rule: listRule({ value: ['A'], default_value: [], is_override: true }),
      },
    );
    component.$on('updated', (e: CustomEvent<Rule>) => {
      captured = e.detail;
    });
    await screen.findByTestId('list-rule-row');
    await fireEvent.click(screen.getByTestId('list-rule-reset'));
    await waitFor(() => {
      expect(clearStationRuleMock).toHaveBeenCalledWith('rb', 'cross_pool_dedup_sources');
    });
    expect(captured).not.toBeNull();
    expect(captured!.value).toEqual([]);
    expect(captured!.is_override).toBe(false);
  });

  // ---------- Autocomplete ----------

  it('shows suggestions filtered to role=reference when input has text', async () => {
    listSourcesMock.mockResolvedValueOnce([
      source({ id: 1, name: 'REFERENCE_ARCHIVE', role: 'reference' }),
      source({ id: 2, name: 'STARTER_CONTENT', role: 'content' }),
      source({ id: 3, name: 'REFERENCE_OTHER', role: 'reference' }),
    ]);
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'REF' } });
    await waitFor(() => {
      const suggestions = screen.getAllByTestId('list-rule-suggestion');
      expect(suggestions).toHaveLength(2);
      // Content-role pool is filtered out.
      const labels = suggestions.map((b) => b.textContent || '');
      expect(labels.some((l) => l.includes('REFERENCE_ARCHIVE'))).toBe(true);
      expect(labels.some((l) => l.includes('REFERENCE_OTHER'))).toBe(true);
      expect(labels.some((l) => l.includes('STARTER_CONTENT'))).toBe(false);
    });
  });

  it('suggestions exclude pools already in the staged list', async () => {
    listSourcesMock.mockResolvedValueOnce([
      source({ id: 1, name: 'REFERENCE_ARCHIVE', role: 'reference' }),
      source({ id: 2, name: 'REFERENCE_OTHER', role: 'reference' }),
    ]);
    render(
      ListRuleRow,
      {
        slug: 'rb',
        rule: listRule({ value: ['REFERENCE_ARCHIVE'], is_override: true }),
      },
    );
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'REF' } });
    await waitFor(() => {
      const suggestions = screen.getAllByTestId('list-rule-suggestion');
      // Only the not-yet-chipped pool surfaces.
      expect(suggestions).toHaveLength(1);
      expect(suggestions[0].textContent).toContain('REFERENCE_OTHER');
    });
  });

  it('clicking a suggestion adds it as a chip', async () => {
    listSourcesMock.mockResolvedValueOnce([
      source({ id: 1, name: 'REFERENCE_ARCHIVE', role: 'reference' }),
    ]);
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'REF' } });
    await waitFor(() => screen.getByTestId('list-rule-suggestion'));
    await fireEvent.click(screen.getByTestId('list-rule-suggestion'));
    const chips = screen.getAllByTestId('list-rule-chip');
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain('REFERENCE_ARCHIVE');
  });

  it('suggestions hidden when input is empty', async () => {
    listSourcesMock.mockResolvedValueOnce([
      source({ id: 1, name: 'REFERENCE_ARCHIVE', role: 'reference' }),
    ]);
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    await screen.findByTestId('list-rule-row');
    // No input text → suggestions list not shown.
    expect(screen.queryByTestId('list-rule-suggestions')).not.toBeInTheDocument();
  });

  it('soft-fails when listSources errors — typing+Enter still works', async () => {
    listSourcesMock.mockRejectedValueOnce(new FakeApiError(500, 'oops'));
    render(ListRuleRow, { slug: 'rb', rule: listRule() });
    const input = await screen.findByTestId('list-rule-input');
    await fireEvent.input(input, { target: { value: 'POOL_X' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getAllByTestId('list-rule-chip')).toHaveLength(1);
  });
});
