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
  import { goto } from '$app/navigation';
  import { createStation } from '$lib/api';
  import StationForm from '$lib/components/StationForm.svelte';

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

<main>
  <p class="crumbs"><a href="/">← stations</a></p>
  <h1>New station</h1>
  <p class="sub">
    Produces a template JSON identical to a checked-in <code>stations/*.json</code>.
    Validation runs server-side against <code>engine.template.parse_template</code>.
  </p>

  <StationForm mode="create" onSubmit={handleCreate} />
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
</style>
