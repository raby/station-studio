// §16.13 c2 — SPA mode: no SSR, no prerender.
//
// Required for adapter-static + the `fallback: 'index.html'` config
// in `svelte.config.js`. The app already runs entirely client-side
// (every route fetches via `onMount`), so this is a one-line
// formalisation of what the code was already doing.
//
// Why both flags:
//   - `ssr = false`   — don't generate server-rendered HTML; the
//                       fallback page just boots the client app.
//   - `prerender = false` — don't try to crawl + freeze each route
//                       at build time; dynamic routes like
//                       `/stations/[slug]` can't be enumerated.
export const ssr = false;
export const prerender = false;
