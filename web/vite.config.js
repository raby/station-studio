import { sveltekit } from '@sveltejs/kit/vite';
// `defineConfig` from 'vitest/config' is the Vite one with the `test` block
// typed in. Importing from 'vite' makes svelte-check warn that `test` isn't a
// known property; importing from 'vitest/config' is the documented fix and is
// drop-in compatible (it re-exports Vite's defineConfig).
import { defineConfig } from 'vitest/config';

const isTest = !!process.env.VITEST;

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8001'
    }
  },
  // In test mode, prefer Svelte's browser build so onMount() isn't a
  // no-op. Needs to live at top-level resolve (not under test.resolve)
  // because SvelteKit's Vite plugin overrides module resolution before
  // Vitest's own resolve applies.
  resolve: isTest
    ? { conditions: ['browser'] }
    : {},
  // Vitest reads this section; keep it next to vite so plugins and aliases
  // (including $lib → src/lib from SvelteKit) are shared with the app build.
  test: {
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{js,ts}'],
    globals: false,
    // The @testing-library/svelte vitest hook flushes Svelte's scheduler
    // after each test (so onMount() side-effects land) and calls cleanup()
    // for us. Without this, async onMount work is invisible to assertions.
    setupFiles: ['./vitest.setup.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,js,svelte}'],
      exclude: [
        'src/**/*.{test,spec}.{js,ts}',
        'src/**/*.d.ts',
        'src/app.html',
        // Trace viewer is Playwright-only by §14.5 / §16.6 design — the
        // page does layout work that's awkward to assert in jsdom and
        // `playlists/[id]/+page.svelte` is well-covered by the e2e
        // suite. Excluded so its 0% lines don't drag the aggregate
        // and the per-file thresholds below stay meaningful.
        'src/routes/playlists/[id]/+page.svelte',
        // Edit route is Playwright-only for the same reason: it's a
        // thin parent over <StationForm> that loads data via the API
        // and dispatches the PUT — both well-tested at the unit and
        // e2e layers respectively. The component's behaviour is
        // pinned in StationForm.test.ts (§16.7 commit 3); the route
        // wiring is pinned in e2e/edit.spec.ts.
        'src/routes/stations/[slug]/edit/+page.svelte',
      ],
      reporter: ['text', 'text-summary'],
      // Coverage gates (DESIGN.md §14.12). The §14.9 M1 finding — the
      // lib/api claim was 100% but actual was 84% — would have been
      // caught here had this gate existed.
      //
      // Per-file `lines` threshold catches single-file regressions like
      // M1 (a 15pp drop on lib/api would fail the build, not silently
      // pass aggregate). Lines is the most reliable v8-over-Svelte
      // metric; statements/branches/functions count noisy compile
      // artifacts (reactive update points etc.) and are gated at the
      // aggregate level instead, where the noise averages out.
      thresholds: {
        // Per-file lines: every included file >= 85%. The current files
        // sit at 95% / 100% / 100% / 100% — a real regression in any one
        // would land below 85% well before slipping past code review.
        'src/**/*': { lines: 85 },
        // Aggregate gates as a backstop for the noisier metrics.
        statements: 80,
        functions: 80,
        branches: 70,
      },
    }
  }
});
