<script lang="ts">
  /**
   * Root layout — mounts the single shared `<ConfirmDialog>` host
   * (§16.25.t4) so any route can call `confirmDialog(...)` and have
   * the modal render on top of the current page.
   *
   * §16.5 c1 — also owns the cross-route a11y chrome:
   *   - "Skip to main content" link, the first focusable element on
   *     every page; jumps keyboard / screen-reader users past the
   *     page's nav crumbs straight to the main landmark.
   *   - Global high-contrast focus-visible ring (only renders on
   *     keyboard focus, not pointer click) so tabbing through the
   *     UI is legible across browsers' default styling.
   *
   * Page-level `<main>` elements own `id="main-content"` so the
   * skip-link target lands at the start of the page's content. The
   * id is required for the skip-link href to work; routes that
   * don't carry it just degrade to the no-op (skip-link does
   * nothing, no layout regression).
   */
  import '$lib/theme.css';
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { startInstallSupport } from '$lib/install';
  import { exposeToApp } from '$lib/native';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
  // Quit + Update now, only when opened as the downloaded app.
  import AppBar from '$lib/components/AppBar.svelte';

  // Sidebar icons, drawn like Station Player's (its ICONS table).
  const ICONS: Record<string, string[]> = {
    radio: ['M16.24 7.76a6 6 0 0 1 0 8.48', 'M7.76 16.24a6 6 0 0 1 0-8.48', 'M19.07 4.93a10 10 0 0 1 0 14.14', 'M4.93 19.07a10 10 0 0 1 0-14.14'],
    folder: ['M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v6.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z'],
    plus: ['M12 5v14', 'M5 12h14'],
    settings: ['M4 21v-7', 'M4 10V3', 'M12 21v-9', 'M12 8V3', 'M20 21v-5', 'M20 12V3', 'M1.5 14h5', 'M9.5 8h5', 'M17.5 16h5'],
    help: ['M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3', 'M12 17h.01'],
  };

  const NAV = [
    { href: '/', label: 'Stations', icon: 'radio', testid: 'sidebar-stations' },
    { href: '/sources', label: 'Source pools', icon: 'folder', testid: 'sidebar-sources' },
    { href: '/stations/new', label: 'New station', icon: 'plus', testid: 'sidebar-new' },
  ];
  const SETUP = [
    { href: '/settings', label: 'Settings', icon: 'settings', testid: 'sidebar-settings' },
    { href: '/help', label: 'Help', icon: 'help', testid: 'sidebar-help' },
  ];

  // Its own Dock / taskbar icon, once installed as an app.
  onMount(() => startInstallSupport());
  // The Mac app's menus (Go, Settings, Help) move between pages.
  onMount(() => exposeToApp({ go: (path) => void goto(path) }));

  // Stations stays lit inside a station and its playlists.
  function current(href: string, path: string): boolean {
    if (href === '/stations/new') return path === href;
    if (href === '/') return path === '/' || (path.startsWith('/stations/') && path !== '/stations/new') || path.startsWith('/playlists/');
    return path === href || path.startsWith(href + '/');
  }
</script>

<a href="#main-content" class="skip-link">Skip to main content</a>

<div class="shell">
  <nav class="sidebar" aria-label="Station Studio">
    <a class="brand" href="/">
      <span class="logo" aria-hidden="true">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="2.2" />{#each ICONS.radio as d}<path {d} />{/each}</svg>
      </span>
      <span>Station Studio</span>
    </a>
    {#each [{ items: NAV, label: '' }, { items: SETUP, label: 'Setup' }] as group}
    {#if group.label}<h2 class="nav-head">{group.label}</h2>{/if}
    <ul class="nav-list">
      {#each group.items as item}
        <li>
          <a
            class="nav-item"
            href={item.href}
            aria-current={current(item.href, $page.url.pathname) ? 'page' : undefined}
            data-testid={item.testid}
          >
            <svg class="i" viewBox="0 0 24 24" aria-hidden="true">
              {#if item.icon === 'radio'}<circle cx="12" cy="12" r="2" />{/if}
              {#if item.icon === 'help'}<circle class="ring" cx="12" cy="12" r="9" />{/if}
              {#each ICONS[item.icon] as d}<path {d} />{/each}
            </svg>
            <span>{item.label}</span>
          </a>
        </li>
      {/each}
    </ul>
    {/each}
    <div class="sidebar-foot">
      <AppBar />
    </div>
  </nav>

  <div class="content">
    <slot />
  </div>
</div>

<ConfirmDialog />

<style>
  /* §16.20 c1 — codebase-wide mobile breakpoint. 40rem = 640px
     at the default 16px root size, matches the §16.20 D1 lock.

     **Honest caveat (c1-review R5):** the variable is purely
     for JS-side consumption today — `window.matchMedia(\`(max-
     width: ${getComputedStyle(document.documentElement)
     .getPropertyValue('--mobile-breakpoint')})\`)` and similar.
     CSS `@media (max-width: ...)` queries CANNOT consume CSS
     variables in browsers we target; CSS Media Queries 4's
     `@media (width <= var(--mobile-breakpoint))` syntax exists
     but UA support is partial. So per-page CSS @media queries
     hard-code the literal `40rem`. Changing the variable below
     ONLY updates JS-side consumers; CSS @media literals must
     be updated in lockstep. If/when CSS-MQ4 support is
     universal, drop the literals in favour of the variable and
     this comment can shrink. */
  :global(:root) {
    --mobile-breakpoint: 40rem;
    /* Colours live in $lib/theme.css (Station Player's palette). */
  }

  .shell {
    display: grid;
    grid-template-columns: 232px minmax(0, 1fr);
    min-height: 100vh;
  }
  .sidebar {
    background: var(--panel);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow-y: auto;
    padding: 18px 12px 14px;
    position: sticky;
    top: 0;
    /* Sticky makes the sidebar its own layer; keep that layer above the
       page so the app's full-window "Station Studio has stopped" screen
       (in AppBar, inside the sidebar) covers the station cards. */
    z-index: 20;
  }
  .brand {
    align-items: center;
    color: var(--text);
    display: flex;
    font-size: 16px;
    font-weight: 700;
    gap: 10px;
    letter-spacing: -0.01em;
    padding: 0 8px 18px;
    text-decoration: none;
  }
  .brand:hover {
    color: var(--text);
  }
  .logo {
    align-items: center;
    background: var(--accent);
    border-radius: 8px;
    color: var(--accent-text);
    display: inline-flex;
    height: 30px;
    justify-content: center;
    width: 30px;
  }
  .logo svg {
    fill: none;
    height: 20px;
    stroke: currentColor;
    stroke-linecap: round;
    stroke-width: 2.2;
    width: 20px;
  }
  .logo circle {
    fill: currentColor;
    stroke: none;
  }
  .nav-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .nav-item {
    align-items: center;
    border-radius: 8px;
    color: var(--muted);
    display: flex;
    gap: 10px;
    min-height: 36px;
    padding: 7px 10px;
    text-decoration: none;
  }
  .nav-item:hover {
    background: var(--row-hover);
    color: var(--text);
  }
  .nav-item[aria-current='page'] {
    background: var(--accent-soft);
    color: var(--text);
    font-weight: 600;
  }
  .nav-item[aria-current='page'] svg {
    color: var(--accent);
  }
  svg.i {
    fill: none;
    flex: none;
    height: 17px;
    stroke: currentColor;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-width: 2;
    width: 17px;
  }
  svg.i circle {
    fill: currentColor;
    stroke: none;
  }
  svg.i circle.ring {
    fill: none;
    stroke: currentColor;
  }
  .nav-head {
    color: var(--faint);
    font-size: 11px;
    font-weight: 650;
    letter-spacing: 0.09em;
    margin: 20px 10px 6px;
    text-transform: uppercase;
  }
  .sidebar-foot {
    margin-top: auto;
    padding-top: 16px;
  }
  .content {
    min-width: 0;
    padding: 8px 24px 48px;
  }
  /* Narrow screens: the sidebar becomes a bar across the top. */
  @media (max-width: 40rem) {
    .shell {
      display: block;
    }
    .sidebar {
      border-bottom: 1px solid var(--border);
      border-right: 0;
      flex-flow: row wrap;
      gap: 6px 10px;
      height: auto;
      padding: 10px 12px;
      position: static;
    }
    .brand {
      padding: 0 4px;
    }
    .nav-list {
      flex-direction: row;
      flex-wrap: wrap;
    }
    .nav-head {
      display: none;
    }
    .sidebar-foot {
      flex-basis: 100%;
      padding-top: 4px;
    }
    .content {
      padding: 0 4px 32px;
    }
  }

  /* §16.5 c1 — skip-link: visually hidden until focused, then
     pinned top-left. Uses the standard "visually-hidden but
     focus-restorable" pattern (clip: rect(0,0,0,0) rather than
     `display: none`) so the element stays in the tab order. */
  .skip-link {
    background: var(--accent);
    border-radius: 4px;
    color: var(--accent-text);
    font-weight: 600;
    left: 0.5rem;
    padding: 0.5rem 0.75rem;
    position: absolute;
    text-decoration: none;
    top: -3rem;
    z-index: 10000;
  }
  /* `:focus-visible` keeps the reveal keyboard-only (the link
     is only reachable by keyboard anyway) and folds it into
     the same focus-ring contract as every other element. */
  .skip-link:focus-visible {
    outline: 3px solid var(--border);
    outline-offset: 2px;
    top: 0.5rem;
  }
  /* §16.5 c1-review R4 — honour `prefers-reduced-motion`. The
     §16.5 DESIGN brief calls reduced-motion out-of-scope, so any
     animation we add must be opt-in via the no-preference media
     query rather than retroactively gated later. */
  @media (prefers-reduced-motion: no-preference) {
    .skip-link {
      transition: top 0.15s ease-out;
    }
  }

  /* Global high-contrast focus ring. :focus-visible fires only
     when the user navigates via keyboard (not pointer click), so
     it doesn't add noise to mouse interactions while giving
     keyboard users a clear indicator across every element.
     The amber accent passes 3:1 for non-text on both themes. */
  :global(*:focus-visible) {
    outline: 3px solid var(--accent);
    outline-offset: 2px;
  }
  /* §16.5 c1-review R1 — skip-link target `<main id="main-content">`
     gets `tabindex="-1"` so activation moves keyboard focus ONTO
     <main>, not just the scroll position (WCAG 2.4.1 'Bypass
     Blocks'). The synthetic focus shouldn't draw the big blue
     ring around the whole content region, so suppress :focus on
     [tabindex="-1"] main. */
  :global(main[tabindex="-1"]:focus) {
    outline: none;
  }

  /* §16.5 c4 — visually-hidden utility for SR-only labels
     (e.g. empty action-column <th>s, icon-button context). Uses
     the standard clip+1px-box pattern so the node stays in the
     a11y tree (announced by SR) while taking zero visual space.
     Distinct from the skip-link's "hidden-until-focused" pattern:
     this one never appears.

     §16.5 c4-review R3 — `clip: rect(0,0,0,0)` is deprecated in
     CSS Masking 1; pair with `clip-path: inset(50%)` as a
     defensive fallback. Some UA contexts (transformed ancestors,
     certain Safari WKWebView versions) silently drop the
     deprecated `clip`, which would reveal a 1px artifact at the
     top-left of the host element. clip-path keeps the element
     hidden across modern engines too. */
  :global(.visually-hidden) {
    border: 0;
    clip: rect(0, 0, 0, 0);
    clip-path: inset(50%);
    height: 1px;
    margin: -1px;
    overflow: hidden;
    padding: 0;
    position: absolute;
    white-space: nowrap;
    width: 1px;
  }
</style>
