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
        // Edit route is Playwright-only — it's a thin parent over
        // <StationForm> that loads data via the API and dispatches
        // the PUT. Both well-tested at the unit (StationForm.test.ts)
        // and e2e (edit.spec.ts) layers; including it would only
        // re-test the parent wiring at the cost of mocking
        // $app/stores.
        'src/routes/stations/[slug]/edit/+page.svelte',
        // Note: the trace viewer (`playlists/[id]/+page.svelte`) was
        // previously excluded too. As of §14.14 it's covered by
        // trace-viewer.test.ts under jsdom — render-branch + disk-
        // export wiring assertions. Layout/CSS-shaped behaviour is
        // still Playwright's job, but the component's state machine
        // now has Vitest coverage.
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
