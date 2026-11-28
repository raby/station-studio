import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

// Hoist mock surface so vi.mock() factories (which run before imports) can
// reach the same functions our assertions poke.
const { getStationsMock, FakeApiError } = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }
  return { getStationsMock: vi.fn(), FakeApiError };
});

vi.mock('$lib/api', () => ({
  getStations: (...args: unknown[]) => getStationsMock(...args),
  ApiError: FakeApiError,
}));

import Page from './+page.svelte';

/**
 * Covers the four render branches of the landing page:
 *   loading → error → empty → populated
 * Each branch reached by controlling the mocked `getStations` outcome.
 */
describe('landing +page.svelte', () => {
  // The vitest.setup.ts hook from @testing-library/svelte/vitest already
  // runs `await act(); cleanup()` after each test — adding our own
  // afterEach(cleanup) double-tears the component and races the act flush,
  // which deadlocks the deferred-promise loading test specifically.
  beforeEach(() => getStationsMock.mockReset());

  it('shows "Loading…" until the fetch resolves', async () => {
    // Even with a pre-resolved promise, the .then microtask hasn't run by
    // the time render() returns synchronously — onMount's `await
    // getStations()` is still suspended on the next-microtask boundary.
    // So we can assert on the loading branch synchronously, then drain.
    getStationsMock.mockResolvedValueOnce([]);
    render(Page);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
    await act();
  });

  it('says plainly when the server is not answering, with Try again', async () => {
    getStationsMock.mockRejectedValueOnce(
      new FakeApiError(503, '503 Service Unavailable: backend down'),
    );
    render(Page);
    const notice = await screen.findByTestId('backend-down');
    expect(notice).toHaveTextContent(/isn't answering/i);
    // The technical reason stays, folded away under Details.
    expect(notice).toHaveTextContent(/backend down/);
    // No command to type.
    expect(notice).not.toHaveTextContent(/python|run\.py/i);

    getStationsMock.mockResolvedValueOnce([]);
    await fireEvent.click(screen.getByTestId('try-again'));
    expect(await screen.findByTestId('welcome')).toBeInTheDocument();
    expect(screen.queryByTestId('backend-down')).toBeNull();
    expect(getStationsMock).toHaveBeenCalledTimes(2);
  });

  const station = (slug: string, playlistCount: number) => ({
    id: 1, slug, name: slug, cadence: 'weekly', playlist_count: playlistCount,
    last_generated_week: playlistCount ? 1 : null,
    last_generated_at: playlistCount ? '2026-05-01T00:00:00Z' : null,
    updated_at: '2026-05-01T00:00:00Z',
  });

  it('shows the first-run welcome until a week is generated', async () => {
    // A new install: stations and their sample pools, nothing generated.
    getStationsMock.mockResolvedValueOnce([station('starter', 0)]);
    render(Page);
    const welcome = await screen.findByTestId('welcome');
    expect(screen.getByTestId('welcome-import')).toHaveAttribute('href', '/sources');
    expect(screen.getByTestId('welcome-new-station')).toHaveAttribute('href', '/stations/new');
    expect(welcome).toHaveTextContent(/stations below/i);
    // The stations still list under it.
    expect(screen.getByTestId('station-list')).toBeInTheDocument();
  });

  it('no welcome once any station has a week', async () => {
    getStationsMock.mockResolvedValueOnce([station('a', 0), station('b', 2)]);
    render(Page);
    await screen.findByTestId('station-list');
    expect(screen.queryByTestId('welcome')).toBeNull();
  });

  it('with no stations, the welcome says so and offers New station, no command to type', async () => {
    getStationsMock.mockResolvedValueOnce([]);
    render(Page);
    const welcome = await screen.findByTestId('welcome');
    expect(welcome).toHaveTextContent(/no stations yet/i);
    expect(welcome).not.toHaveTextContent(/python|bootstrap/i);
    expect(screen.getByTestId('welcome-new-station')).toHaveAttribute('href', '/stations/new');
  });

  it('no welcome while loading or when the server is not answering', async () => {
    getStationsMock.mockRejectedValueOnce(new FakeApiError(503, 'down'));
    render(Page);
    expect(screen.queryByTestId('welcome')).toBeNull();
    await screen.findByTestId('backend-down');
    expect(screen.queryByTestId('welcome')).toBeNull();
  });

  it('lists stations as links when the fetch returns rows', async () => {
    // §16.12 — rows are now StationSummary shape (cadence + aggregates).
    getStationsMock.mockResolvedValueOnce([
      {
        id: 1, slug: 'rebirth-classics', name: 'Rebirth Classics',
        cadence: 'weekly', playlist_count: 3,
        last_generated_week: 3, last_generated_at: '2026-05-01T00:00:00Z',
        updated_at: '2026-05-01T00:00:00Z',
      },
      {
        id: 2, slug: 'new-gen', name: 'New Gen',
        cadence: 'monthly', playlist_count: 0,
        last_generated_week: null, last_generated_at: null,
        updated_at: '2026-04-01T00:00:00Z',
      },
    ]);
    render(Page);

    const classics = await screen.findByRole('link', { name: 'Rebirth Classics' });
    expect(classics).toHaveAttribute('href', '/stations/rebirth-classics');
    const newGen = screen.getByRole('link', { name: 'New Gen' });
    expect(newGen).toHaveAttribute('href', '/stations/new-gen');
  });

  it('renders the §16.12 controls + per-row last-generated line', async () => {
    getStationsMock.mockResolvedValueOnce([
      {
        id: 1, slug: 'has-gen', name: 'Has Gen',
        cadence: 'weekly', playlist_count: 3,
        last_generated_week: 26, last_generated_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      {
        id: 2, slug: 'no-gen', name: 'No Gen',
        cadence: 'monthly', playlist_count: 0,
        last_generated_week: null, last_generated_at: null,
        updated_at: new Date().toISOString(),
      },
    ]);
    render(Page);
    await screen.findByTestId('station-list');

    // Controls present.
    expect(screen.getByTestId('station-search')).toBeInTheDocument();
    expect(screen.getByTestId('sort-last-generated')).toBeInTheDocument();
    // Cadence chips reflect the present cadences.
    expect(screen.getByTestId('cadence-weekly')).toBeInTheDocument();
    expect(screen.getByTestId('cadence-monthly')).toBeInTheDocument();
    // Per-row last-generated line for the generated station.
    expect(screen.getByTestId('last-generated')).toHaveTextContent('WEEKLY 026');
    // Never-generated muted line for the empty one.
    expect(screen.getByTestId('never-generated')).toBeInTheDocument();
  });

  it('filter/sort chips expose aria-pressed reflecting active state', async () => {
    // Full-slice review 🟡-3: chips must carry aria-pressed (matching
    // the ViewModeToggle precedent) so screen-reader users can tell
    // which filter / sort is active.
    getStationsMock.mockResolvedValueOnce([
      {
        id: 1, slug: 'jazz-fm', name: 'Jazz FM', cadence: 'weekly',
        playlist_count: 0, last_generated_week: null, last_generated_at: null,
        updated_at: new Date().toISOString(),
      },
    ]);
    render(Page);
    await screen.findByTestId('station-list');

    // Default state: sort=name active, generation=all active.
    expect(screen.getByTestId('sort-name')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('sort-updated')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByTestId('gen-all')).toHaveAttribute('aria-pressed', 'true');

    // Click a different sort → aria-pressed follows.
    await fireEvent.click(screen.getByTestId('sort-updated'));
    expect(screen.getByTestId('sort-updated')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('sort-name')).toHaveAttribute('aria-pressed', 'false');

    // Search input has an accessible label (🟢-4).
    expect(screen.getByTestId('station-search')).toHaveAttribute(
      'aria-label', 'Search stations by name or slug',
    );
  });

  it('search filters the visible rows', async () => {
    getStationsMock.mockResolvedValueOnce([
      {
        id: 1, slug: 'jazz-fm', name: 'Jazz FM', cadence: 'weekly',
        playlist_count: 0, last_generated_week: null, last_generated_at: null,
        updated_at: new Date().toISOString(),
      },
      {
        id: 2, slug: 'rock-radio', name: 'Rock Radio', cadence: 'weekly',
        playlist_count: 0, last_generated_week: null, last_generated_at: null,
        updated_at: new Date().toISOString(),
      },
    ]);
    const { container } = render(Page);
    const search = (await screen.findByTestId('station-search')) as HTMLInputElement;
    await fireEvent.input(search, { target: { value: 'jazz' } });

    const rows = container.querySelectorAll('[data-testid="station-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-slug')).toBe('jazz-fm');
  });

  it('non-ApiError failures stringify so the user still sees something', async () => {
    getStationsMock.mockRejectedValueOnce(new TypeError('network fetch failed'));
    render(Page);
    // The component stringifies non-ApiError via String(e), which yields
    // "TypeError: network fetch failed" — proves the instanceof branch.
    expect(
      await screen.findByText('TypeError: network fetch failed'),
    ).toBeInTheDocument();
  });
});
