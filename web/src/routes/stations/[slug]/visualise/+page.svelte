<script lang="ts">
  /**
   * §16.2.2 c2 — visualise route scaffold.
   *
   * Fetches GET /api/stations/{slug}/sidecar and renders a minimal
   * placeholder. The real canvas (Section / ChipFlow / Chip / etc.)
   * lands in c3. This file's job: prove the load path + types are
   * wired before c3 starts composing components.
   *
   * Three render states:
   *   - loading       → "Loading…"
   *   - error (404)   → station-not-found message + back link
   *   - loaded        → counts summary + sidecar_error warning (if any)
   *
   * Discoverability link from /stations/[slug] lands in c4 (per the
   * design's "first-impression" treatment); c2 is reachable only by
   * direct URL or test.
   */
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { ApiError, getStationSidecar } from '$lib/api';
  import type { Sidecar, StationTemplate } from '$lib/editor';
  import { expandSection } from '$lib/editor';

  let template: StationTemplate | null = null;
  let sidecar: Sidecar | null = null;
  let sidecarError: string | null = null;
  let loadError: string | null = null;
  let loading = true;

  $: slug = $page.params.slug ?? '';

  onMount(async () => {
    if (!slug) {
      loadError = 'missing slug';
      loading = false;
      return;
    }
    try {
      const r = await getStationSidecar(slug);
      // The API returns opaque records; cast at the use site per the
      // api.ts type-layering note. Validation happens server-side
      // (engine.editor_metadata) so the cast is safe for any
      // response that didn't 5xx.
      template = r.template as unknown as StationTemplate;
      sidecar = r.sidecar as unknown as Sidecar;
      sidecarError = r.sidecar_error;
    } catch (e) {
      loadError = e instanceof ApiError ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  // Derived counts the placeholder renders — also exercises
  // expandSection from $lib/editor so the helper is reachable from
  // the route (catches import / type-mismatch issues at build time
  // rather than at c3-component-author time).
  $: totalChips =
    sidecar !== null
      ? sidecar.sections.reduce(
          (acc, s) => acc + expandSection(s, sidecar!).length,
          0,
        )
      : 0;
</script>

<svelte:head>
  <title>Visualise — {template?.name ?? slug} — Station Studio</title>
</svelte:head>

<main>
  <p class="crumbs">
    <a href="/stations/{encodeURIComponent(slug)}">← {slug}</a>
  </p>

  <h1>Visualise template</h1>

  {#if loading}
    <p>Loading…</p>
  {:else if loadError}
    <p class="error" data-testid="visualise-error">{loadError}</p>
  {:else if template && sidecar}
    <p class="slug">{template.station} — {template.name}</p>

    {#if sidecarError}
      <!--
        Decision E warning banner. The committed sidecar failed
        validation; server fell back to the synthesized trivial one
        + populated sidecar_error. Operator can keep working while
        figuring out what broke.
      -->
      <div class="sidecar-warning" data-testid="sidecar-warning">
        <strong>Warning:</strong> the committed
        <code>stations/{slug}.editor.json</code> is malformed —
        falling back to the trivial view.
        <pre>{sidecarError}</pre>
        Fix the file or regenerate it with
        <code>make derive-sidecar STATION={slug}</code>.
      </div>
    {/if}

    <!--
      c2 placeholder. Real canvas (Section / ChipFlow / Chip) ships
      in c3. Counts surface let smoke tests + the dev `make dev` run
      confirm the load + expansion paths work end-to-end before c3
      builds on top.
    -->
    <section class="placeholder" data-testid="visualise-placeholder">
      <p>
        Sidecar loaded:
        <strong data-testid="section-count">{sidecar.sections.length}</strong>
        section(s),
        <strong data-testid="macro-count">{(sidecar.macros ?? []).length}</strong>
        macro(s),
        <strong data-testid="chip-count">{totalChips}</strong>
        chip(s) total.
      </p>
      <p class="hint">The visual canvas ships in §16.2.2 c3.</p>
    </section>
  {/if}
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 900px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .crumbs {
    font-size: 0.85rem;
    margin-bottom: 1rem;
  }
  .crumbs a {
    color: #666;
    text-decoration: none;
  }
  .crumbs a:hover {
    text-decoration: underline;
  }
  h1 {
    margin: 0;
  }
  .slug {
    color: #888;
    font-family: ui-monospace, monospace;
    margin-top: 0.1rem;
  }
  .sidecar-warning {
    background: #fff8e1;
    border: 1px solid #f0c36d;
    border-radius: 4px;
    padding: 0.75rem 1rem;
    margin: 1rem 0;
    font-size: 0.9rem;
  }
  .sidecar-warning pre {
    background: #fffaf0;
    padding: 0.4rem 0.6rem;
    border-radius: 3px;
    overflow-x: auto;
    font-size: 0.85rem;
    margin: 0.5rem 0;
  }
  .placeholder {
    margin-top: 1.5rem;
    padding: 1rem;
    background: #f4f4f4;
    border-radius: 6px;
  }
  .hint {
    color: #666;
    font-size: 0.9rem;
    margin-top: 0.5rem;
  }
  .error {
    color: crimson;
  }
  code {
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
  }
</style>
