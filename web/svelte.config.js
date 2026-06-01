// §16.13 c2 — adapter-static for the installable wheel.
//
// `adapter-auto` (the previous choice) detects the deployment target
// and falls back to adapter-node when none is detected — which means
// the build emits Node.js server code that FastAPI can't serve.
//
// adapter-static, by contrast, emits ONLY static HTML/JS/CSS. The
// catch-all `fallback: 'index.html'` makes it a Single-Page App
// (client-side routing for `/sources`, `/stations/[slug]`, etc.).
//
// SSR is off in `src/routes/+layout.ts` — the app already fetches
// every route client-side via `onMount` (no `load()` server-side
// calls anywhere), so the migration is mechanical, not a refactor.
//
// `strict: false` keeps the build from failing on dynamic routes
// like `/stations/[slug]` that can't be crawled at build time —
// they resolve at runtime via the SPA fallback.
//
// Trade-off (already documented in §16.13's §16.22 reconciliation):
// because the wheel ships no Node server, hard-loaded trace links
// can never SSR. §16.22's first-paint goal is foreclosed; only the
// in-app `+page.ts load()` refactor it described would survive.
import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      fallback: 'index.html',
      precompress: false,
      strict: false,
    }),
  },
};

export default config;
