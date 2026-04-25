import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

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
      ],
      reporter: ['text', 'text-summary']
    }
  }
});
