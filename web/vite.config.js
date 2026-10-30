import { sveltekit } from '@sveltejs/kit/vite';
// `defineConfig` from 'vitest/config' is the Vite one with the `test` block
// typed in. Importing from 'vite' makes svelte-check warn that `test` isn't a
// known property; importing from 'vitest/config' is the documented fix and is
// drop-in compatible (it re-exports Vite's defineConfig).
import { defineConfig } from 'vitest/config';

const isTest = !!process.env.VITEST;

export default defineConfig({
  plugins: [sveltekit()],
  // The Mac app shows the UI in the system WebKit (macwindow.py) and
  // supports macOS 11 (packaging/station-studio.spec), so the build
  // must run on Safari 14. Vite 5's default target was this list;
  // Vite 7's (baseline-widely-available) starts at Safari 16.
  build: {
    target: ['es2020', 'edge88', 'firefox78', 'chrome87', 'safari14'],
  },
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
      // Coverage gates (DESIGN.md §14.12 + §16.26). The §14.9 M1
      // finding — lib/api claim was 100% but actual was 84% — would
      // have been caught here had this gate existed.
      //
      // Lines (per-glob) catches single-file regressions like M1
      // (a 15pp drop on lib/api fails the build, not silently passes
      // aggregate). It's the most reliable v8-over-Svelte metric;
      // statements/branches/functions count noisy compile artifacts
      // (reactive update points etc.) and are gated at the aggregate
      // level instead, where the noise averages out.
      //
      // §16.26 ratchet policy. Numbers below are set at the CURRENT
      // FLOOR — not the aspirational target. The previous gates
      // (85/80/70 lines/statements/branches) drifted out of touch
      // with reality across §16.16–§16.20 and silently failed CI
      // for ~2 weeks before the v1.5.0 release surfaced it. The
      // rule going forward:
      //
      //   * Ratchet UPWARD when new code raises the actual. If a
      //     PR ships tests that bring statements from 75.7 → 78,
      //     bump the gate to 77 in the same commit.
      //   * Do NOT write tests purely to raise the gate. YAGNI —
      //     the gate exists to catch regressions, not to drive
      //     test-writing crusades. Coverage rises naturally as
      //     well-tested code ships.
      //   * Functions stays unchanged at 80 because the actual
      //     (80.61% as of 2026-06-29) still passes; ratchet up
      //     iff a future PR brings it well above (e.g. to 82+).
      thresholds: {
        'src/**/*': { lines: 84 },  // actual 84.37 (2026-06-29)
        statements: 75,             // actual 75.68 (2026-06-29)
        functions: 80,              // actual 80.61 (2026-06-29)
        branches: 65,               // actual 65.77 (2026-06-29)
      },
    }
  }
});
