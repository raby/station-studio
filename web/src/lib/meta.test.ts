import { describe, it, expect, vi, beforeEach } from 'vitest';

// Hoisted mock for getMeta — the meta module imports it internally;
// we substitute so we can count calls without hitting fetch.
const { getMetaMock } = vi.hoisted(() => ({ getMetaMock: vi.fn() }));

vi.mock('./api', () => ({
  getMeta: (...args: unknown[]) => getMetaMock(...args),
}));

import { loadMeta, _resetMetaCache } from './meta';

/**
 * The meta module is a thin caching layer over getMeta() — DESIGN.md
 * §14.19. The whole point is "fetch once per page session, share
 * across components". These tests pin the cache contract.
 */
describe('lib/meta — module-cached loader', () => {
  beforeEach(() => {
    _resetMetaCache();
    getMetaMock.mockReset();
  });

  it('first call fetches; subsequent calls return the same promise without re-fetching', async () => {
    const meta = {
      strategies: ['pick', 'window'],
      rng: ['off', 'on'],
      roles: ['content', 'reference'],
      cadences: ['weekly'],
    };
    getMetaMock.mockResolvedValueOnce(meta);

    const a = loadMeta();
    const b = loadMeta();
    const c = loadMeta();

    // Same promise reference — no extra fetch in flight.
    expect(a).toBe(b);
    expect(b).toBe(c);
    expect(getMetaMock).toHaveBeenCalledTimes(1);

    // All three resolve to the same blob.
    await expect(a).resolves.toEqual(meta);
    await expect(b).resolves.toEqual(meta);
  });

  it('_resetMetaCache forces the next loadMeta to re-fetch', async () => {
    getMetaMock.mockResolvedValue({
      strategies: [],
      rng: [],
      roles: [],
      cadences: [],
    });

    await loadMeta();
    expect(getMetaMock).toHaveBeenCalledTimes(1);

    _resetMetaCache();
    await loadMeta();
    expect(getMetaMock).toHaveBeenCalledTimes(2);
  });

  it('a rejected fetch caches the rejection (subsequent calls do NOT retry)', async () => {
    // Important contract: if the user is offline at first paint, every
    // subsequent loadMeta() call would otherwise hammer the backend.
    // Cache the rejection too. Fix: call _resetMetaCache() before retry,
    // which is what a refresh button (if added) would do.
    const err = new Error('offline');
    getMetaMock.mockRejectedValueOnce(err);

    await expect(loadMeta()).rejects.toBe(err);
    await expect(loadMeta()).rejects.toBe(err);
    expect(getMetaMock).toHaveBeenCalledTimes(1);
  });
});
