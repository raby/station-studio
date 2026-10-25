<!--
  /stations/new — the create-mode wizard.

  Post-§16.7-extraction this page is a thin parent over <StationForm>:
  the form's state, validation, and template-build logic live in the
  shared component (web/src/lib/components/StationForm.svelte). This
  page owns the create-specific behaviour:
    - calls createStation() (POST /api/stations) on submit
    - navigates to the new station's detail page on success
    - the page heading + crumbs

  The companion route /stations/[slug]/edit (lands in §16.7 commit 2)
  uses the same <StationForm> with mode="edit" and PUT semantics.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { createStation, ApiError } from '$lib/api';
  import type { Meta } from '$lib/api';
  import { loadMeta } from '$lib/meta';
  import StationForm from '$lib/components/StationForm.svelte';
  import ErrorBanner from '$lib/components/ErrorBanner.svelte';

  // Meta loads once, shared with the edit route via the lib/meta cache
  // (DESIGN.md §14.19). Fetch in onMount; render the form only after the
  // meta is available so dropdowns aren't briefly blank.
  let meta: Meta | null = null;
  let metaError: string | null = null;

  onMount(async () => {
    try {
      meta = await loadMeta();
    } catch (e) {
      metaError = e instanceof ApiError ? e.message : String(e);
    }
  });

  /**
   * Create-mode submit handler. <StationForm> calls this with the
   * already-validated payload; failures are caught inside the form
   * (rendered inline as the form's own error). Success → navigate.
   */
  async function handleCreate(payload: {
    slug: string;
    name: string;
    template: Record<string, unknown>;
  }) {
    await createStation(payload);
    await goto(`/stations/${encodeURIComponent(payload.slug)}`);
  }
</script>

<svelte:head>
  <title>New station — Station Studio</title>
</svelte:head>

<main id="main-content" tabindex="-1">
  <p class="crumbs"><a href="/">← stations</a></p>
  <h1>New station</h1>
  <p class="sub">
    Produces a template JSON identical to a checked-in <code>stations/*.json</code>.
    Validation runs server-side against <code>engine.template.parse_template</code>.
  </p>

  <!-- §16.5 c3 — meta-fetch failure via ErrorBanner. Assertive
       because the form can't render without meta — the operator
       hit a hard wall, not a recoverable validation miss. -->
  {#if metaError}
    <ErrorBanner
      message={`Could not load meta: ${metaError}`}
      severity="assertive"
      prefix="Wizard"
    />
  {:else if meta}
    <StationForm mode="create" {meta} onSubmit={handleCreate} />
  {:else}
    <p>Loading…</p>
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
    color: var(--muted);
  }
  .crumbs a {
    color: var(--muted);
    text-decoration: none;
  }
  .crumbs a:hover { text-decoration: underline; }
  h1 { margin: 0; }
  .sub { color: var(--muted); }
  code {
    background: var(--panel-2);
    padding: 0.1em 0.3em;
    border-radius: 3px;
    font-size: 0.85em;
  }
  /* §16.5 c3 — `.error` retired; meta-load failure now renders via
     <ErrorBanner> which owns its own styling. */
</style>
