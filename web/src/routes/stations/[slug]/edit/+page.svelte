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
  import type { Station } from '$lib/api';
  import StationForm from '$lib/components/StationForm.svelte';

  let station: Station | null = null;
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
      station = await getStation(slug);
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

<main>
  <p class="crumbs">
    <a href="/">stations</a> ›
    <a href="/stations/{slug}">{station?.name ?? slug}</a> ›
    edit
  </p>

  {#if loading}
    <p>Loading…</p>
  {:else if loadError}
    <p class="error">{loadError}</p>
  {:else if station}
    <h1>Edit {station.name}</h1>
    <p class="sub">
      Template JSON is the canonical artifact (DESIGN.md §4.2). Validation runs
      server-side against <code>engine.template.parse_template</code>.
    </p>

    <StationForm
      mode="edit"
      initial={station}
      onSubmit={handleSave}
    />
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
</style>
