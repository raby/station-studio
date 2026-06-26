<!--
  /stations/[slug]/edit — edit-mode for an existing station (DESIGN.md §16.7).

  Loads the existing station via getStation(slug) and mounts <StationForm>
  in edit mode with the loaded data as `initial`. Submit handler calls
  updateStation (PUT) and navigates back to the station's detail page.

  Slug is immutable per §16.7 D3; the StationForm renders the slug input
  readonly. The PUT body still includes the slug field (the form's
  buildTemplate() preserves it) but the backend only accepts it equal to
  the URL slug — any deviation is rejected with 400.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/stores';
  import { getStation, updateStation, ApiError } from '$lib/api';
  import type { Station, Meta } from '$lib/api';
  import { loadMeta } from '$lib/meta';
  import { isAdvancedTemplate } from '$lib/template';
  import StationForm from '$lib/components/StationForm.svelte';

  let station: Station | null = null;
  let meta: Meta | null = null;
  let loadError: string | null = null;
  let loading = true;

  // SvelteKit guarantees `slug` is present because this route only matches
  // when the param exists, but the param type is `string | undefined`.
  $: slug = $page.params.slug ?? '';

  onMount(async () => {
    if (!slug) {
      loadError = 'missing slug';
      loading = false;
      return;
    }
    try {
      // Parallel: station metadata + meta (template constants) per §14.19.
      // loadMeta is module-cached so the second mount on the same client
      // doesn't re-hit the network.
      [station, meta] = await Promise.all([getStation(slug), loadMeta()]);
    } catch (e) {
      loadError = e instanceof ApiError ? e.message : String(e);
    } finally {
      loading = false;
    }
  });

  /**
   * Edit-mode submit handler. <StationForm> calls this with the
   * already-validated payload; on PUT failure the form catches and
   * renders inline. On success: back to the station detail.
   */
  async function handleSave(payload: {
    slug: string;
    name: string;
    template: Record<string, unknown>;
  }) {
    await updateStation(slug, payload);
    await goto(`/stations/${encodeURIComponent(slug)}`);
  }
</script>

<svelte:head>
  <title>Edit {station?.name ?? slug} — Station Studio</title>
</svelte:head>

<main id="main-content" tabindex="-1">
  <p class="crumbs">
    <a href="/">stations</a> ›
    <a href="/stations/{slug}">{station?.name ?? slug}</a> ›
    edit
  </p>

  {#if loading}
    <p>Loading…</p>
  {:else if loadError}
    <p class="error">{loadError}</p>
  {:else if station && meta}
    {@const advanced = isAdvancedTemplate(station.template)}
    <h1>Edit {station.name}</h1>
    <p class="sub">
      Template JSON is the canonical artifact (DESIGN.md §4.2). Validation runs
      server-side against <code>engine.template.parse_template</code>.
    </p>

    {#if advanced}
      <!--
        §14.33 (review-fix from whole-project audit): the StationForm UI
        only handles v1 strategies (pick / window) and the row_template
        layout form. Any template carrying §14.22–§14.32 features
        (sequential / jingle / mix / weighted / prev_ref, flat_slots,
        cursor.persist, cursors_init, per-block cursor_id / genre /
        exclusion_window / window / position / fallback_genre) would be
        silently stripped on save by buildTemplate(). The §17 Phase D
        visual block editor will extend the form; until then, block the
        Edit UI for advanced templates with a clear path forward:
        update the template via the PUT /api/stations/{slug} endpoint
        directly.
      -->
      <section class="advanced-guard" data-testid="advanced-template-guard">
        <h2>This template uses advanced features.</h2>
        <p>
          The station's template carries fields the form UI doesn't
          yet support (advanced strategies, flat-slot layout, named
          cursors, or per-block overrides). Saving from this form
          would strip those fields silently.
        </p>
        <p>
          Update the template directly via the API instead:
        </p>
        <pre>PUT /api/stations/{slug}
{`Content-Type: application/json`}

{`{ "slug": "${slug}", "name": "${station.name}", "template": { ... } }`}</pre>
        <p>
          Or wait for the visual block editor (§17 Phase D). The
          station's current configuration is unaffected.
        </p>
        <p>
          <a href="/stations/{slug}">← Back to station details</a>
        </p>
      </section>
    {:else}
      <StationForm
        mode="edit"
        initial={station}
        {meta}
        onSubmit={handleSave}
      />
    {/if}
  {/if}
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 780px;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  .crumbs {
    font-size: 0.85rem;
    margin-bottom: 1rem;
    color: #666;
  }
  .crumbs a {
    color: #666;
    text-decoration: none;
  }
  .crumbs a:hover { text-decoration: underline; }
  h1 { margin: 0; }
  .sub { color: #666; }
  code {
    background: #f4f4f4;
    padding: 0.1em 0.3em;
    border-radius: 3px;
    font-size: 0.85em;
  }
  .error {
    color: crimson;
    background: #fff5f5;
    border: 1px solid #fed7d7;
    padding: 0.5rem 0.75rem;
    border-radius: 4px;
  }

  .advanced-guard {
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 6px;
    padding: 1rem 1.25rem;
    margin: 1rem 0;
  }
  .advanced-guard h2 {
    margin-top: 0;
    font-size: 1.1rem;
    color: #92400e;
  }
  .advanced-guard pre {
    background: #1f2937;
    color: #e5e7eb;
    padding: 0.75rem;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 0.85rem;
  }
</style>
