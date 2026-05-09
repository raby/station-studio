/**
 * Module-cached meta loader (DESIGN.md §14.19).
 *
 * The `/api/meta` response is effectively static — the option lists don't
 * change without a backend deploy. Every component that needs them
 * (`<StationForm>`, `<LayoutFieldset>`, eventually the §16.2 visual block
 * editor) can call `loadMeta()` and share one promise; the first call
 * triggers the fetch, subsequent calls return the same already-resolved
 * promise without re-hitting the network.
 *
 * Tests reset the cache via `_resetMetaCache()` so module state doesn't
 * leak between cases. The leading underscore signals that this isn't
 * part of the public API; production code shouldn't reach for it.
 */
import { getMeta } from './api';
import type { Meta } from './api';

let cached: Promise<Meta> | null = null;

/**
 * Returns a memoised promise for the meta blob. First call fetches;
 * subsequent calls return the same promise (resolved or in-flight).
 *
 * `fetch_` exists for parity with the typed-client helpers in api.ts —
 * pass it from a SvelteKit `load` function for SSR. Default `fetch` is
 * fine inside `onMount`.
 */
export function loadMeta(fetch_: typeof fetch = fetch): Promise<Meta> {
  if (cached === null) {
    cached = getMeta(fetch_);
  }
  return cached;
}

/**
 * Test-only: clear the module cache. Vitest's `vi.mock('$lib/api')`
 * substitutes the underlying `getMeta`, but this module's cache survives
 * across tests and would serve stale data. Call from `beforeEach`.
 */
export function _resetMetaCache(): void {
  cached = null;
}
