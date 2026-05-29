import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, cleanup, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

const {
  getStationRulesMock,
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
    getStationRulesMock: vi.fn(),
    setStationRuleMock: vi.fn(),
    clearStationRuleMock: vi.fn(),
    FakeApiError,
  };
});

vi.mock('$lib/api', () => ({
  getStationRules: (...a: unknown[]) => getStationRulesMock(...a),
  setStationRule: (...a: unknown[]) => setStationRuleMock(...a),
  clearStationRule: (...a: unknown[]) => clearStationRuleMock(...a),
  ApiError: FakeApiError,
}));

import RulesPanel from './RulesPanel.svelte';

/**
 * §16.10 — per-station rule editor. Pins the load → edit → save /
 * reset flow + client-side validation. $lib/api is mocked so tests
 * drive the component without a backend.
 */

function rule(over: Partial<Record<string, unknown>> = {}) {
  return {
    key: 'artist_dedup_window',
    value: 3,
    default_value: 3,
    description: 'Artist dedup lookback window.',
    max_value: 1000,
    is_override: false,
    ...over,
  };
}

describe('<RulesPanel>', () => {
  beforeEach(() => {
    cleanup();
    getStationRulesMock.mockReset();
    setStationRuleMock.mockReset();
    clearStationRuleMock.mockReset();
  });

  it('renders rows with default / override badges after load', async () => {
    getStationRulesMock.mockResolvedValueOnce({
      rules: [
        rule({ key: 'artist_dedup_window', is_override: false }),
        rule({ key: 'mix_exclusion_weeks', value: 8, default_value: 4, is_override: true }),
      ],
    });
    render(RulesPanel, { slug: 'cls' });

    await screen.findByTestId('rules-panel');
    const rows = await screen.findAllByTestId('rule-row');
    expect(rows).toHaveLength(2);
    // First rule is unset → default badge; second is overridden.
    expect(rows[0].querySelector('[data-testid="default-badge"]')).not.toBeNull();
    expect(rows[1].querySelector('[data-testid="override-badge"]')).not.toBeNull();
  });

  it('Save is disabled until the value is edited', async () => {
    getStationRulesMock.mockResolvedValueOnce({ rules: [rule()] });
    render(RulesPanel, { slug: 'cls' });

    const save = await screen.findByTestId('rule-save');
    expect(save).toBeDisabled();

    const input = screen.getByTestId('rule-input') as HTMLInputElement;
    await fireEvent.input(input, { target: { value: '5' } });
    expect(save).toBeEnabled();
  });

  it('Save PUTs the edited value and flips the row to overridden', async () => {
    getStationRulesMock.mockResolvedValueOnce({ rules: [rule({ value: 3, is_override: false })] });
    setStationRuleMock.mockResolvedValueOnce(
      rule({ value: 5, is_override: true }),
    );
    render(RulesPanel, { slug: 'cls' });

    const input = (await screen.findByTestId('rule-input')) as HTMLInputElement;
    await fireEvent.input(input, { target: { value: '5' } });
    await fireEvent.click(screen.getByTestId('rule-save'));

    expect(setStationRuleMock).toHaveBeenCalledWith('cls', 'artist_dedup_window', 5);
    // Row reflects the canonical post-write state → override badge.
    await waitFor(() =>
      expect(screen.getByTestId('override-badge')).toBeInTheDocument(),
    );
    // Save disabled again (edited === new value).
    expect(screen.getByTestId('rule-save')).toBeDisabled();
  });

  it('Reset clears the override + reloads', async () => {
    // First load: overridden. After reset → reload returns default.
    getStationRulesMock
      .mockResolvedValueOnce({ rules: [rule({ value: 8, is_override: true })] })
      .mockResolvedValueOnce({ rules: [rule({ value: 3, is_override: false })] });
    clearStationRuleMock.mockResolvedValueOnce(undefined);
    render(RulesPanel, { slug: 'cls' });

    await fireEvent.click(await screen.findByTestId('rule-reset'));
    expect(clearStationRuleMock).toHaveBeenCalledWith('cls', 'artist_dedup_window');
    // Reloaded state: default badge, reset button gone.
    await waitFor(() =>
      expect(screen.getByTestId('default-badge')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('rule-reset')).toBeNull();
  });

  it('Reset button only shows for overridden rules', async () => {
    getStationRulesMock.mockResolvedValueOnce({ rules: [rule({ is_override: false })] });
    render(RulesPanel, { slug: 'cls' });
    await screen.findByTestId('rule-row');
    expect(screen.queryByTestId('rule-reset')).toBeNull();
  });

  it('client-side validation: above max_value → row error, no API call', async () => {
    getStationRulesMock.mockResolvedValueOnce({
      rules: [rule({ max_value: 1000 })],
    });
    render(RulesPanel, { slug: 'cls' });

    const input = (await screen.findByTestId('rule-input')) as HTMLInputElement;
    await fireEvent.input(input, { target: { value: '5000' } });
    await fireEvent.click(screen.getByTestId('rule-save'));

    expect(screen.getByTestId('rule-row-error')).toHaveTextContent('≤ 1000');
    expect(setStationRuleMock).not.toHaveBeenCalled();
  });

  it('client-side validation: negative → row error, no API call', async () => {
    getStationRulesMock.mockResolvedValueOnce({ rules: [rule()] });
    render(RulesPanel, { slug: 'cls' });

    const input = (await screen.findByTestId('rule-input')) as HTMLInputElement;
    await fireEvent.input(input, { target: { value: '-1' } });
    await fireEvent.click(screen.getByTestId('rule-save'));

    expect(screen.getByTestId('rule-row-error')).toHaveTextContent('≥ 0');
    expect(setStationRuleMock).not.toHaveBeenCalled();
  });

  it('surfaces a backend error inline without blanking the panel', async () => {
    getStationRulesMock.mockResolvedValueOnce({ rules: [rule()] });
    setStationRuleMock.mockRejectedValueOnce(
      new FakeApiError(400, '400 Bad Request: value out of bounds'),
    );
    render(RulesPanel, { slug: 'cls' });

    const input = (await screen.findByTestId('rule-input')) as HTMLInputElement;
    await fireEvent.input(input, { target: { value: '7' } });
    await fireEvent.click(screen.getByTestId('rule-save'));

    await waitFor(() =>
      expect(screen.getByTestId('rule-row-error')).toHaveTextContent('out of bounds'),
    );
    // Panel still rendered.
    expect(screen.getByTestId('rules-panel')).toBeInTheDocument();
  });

  it('renders a load error when the fetch fails', async () => {
    getStationRulesMock.mockRejectedValueOnce(new FakeApiError(404, 'station not found'));
    render(RulesPanel, { slug: 'ghost' });
    expect(await screen.findByTestId('rules-error')).toHaveTextContent('station not found');
  });
});
