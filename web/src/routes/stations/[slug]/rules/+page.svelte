<script lang="ts">
  /**
   * §16.10 — per-station Rules route.
   *
   * Thin shell around <RulesPanel>: resolves the slug from the route
   * + renders a back-crumb. The panel owns its own fetch / edit /
   * save state. Standalone route (mirrors /visualise) until the
   * §16.2 sidebar shell embeds RulesPanel as a tab.
   */
  import { page } from '$app/stores';
  import RulesPanel from '$lib/components/RulesPanel.svelte';

  $: slug = $page.params.slug ?? '';
</script>

<svelte:head>
  <title>Rules — {slug} — Station Studio</title>
</svelte:head>

<main>
  <p class="crumbs">
    <a href="/stations/{encodeURIComponent(slug)}">← {slug}</a>
  </p>

  {#if slug}
    <RulesPanel {slug} />
  {:else}
    <p class="error">missing slug</p>
  {/if}
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 720px;
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
  .error {
    color: crimson;
  }
</style>
