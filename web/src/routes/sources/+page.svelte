<script lang="ts">
  /**
   * §16.25 /sources page — the canonical home for source-pool management.
   *
   * Thin wrapper around `<PoolImportPanel>` (§16.25 c5 extraction):
   * reads `?pool=…&role=…` from the URL and forwards them as the
   * panel's `initialName` / `initialRole` props so the §16.19
   * "not imported" badge can deep-link here with the add-form
   * prefilled (D3 secondary affordance — the global-context escape
   * hatch alongside the per-station embed).
   *
   * Query-param contract:
   *   ?pool=NAME       — prefill the single-pool form's name.
   *   ?role=content|reference — prefill the role select; ignored for
   *                      any other value (defensive against typos in
   *                      a hand-written link).
   *
   * Both are read at mount time only; the panel doesn't re-prefill
   * if the user later navigates within the page.
   */
  import { page } from '$app/stores';
  import PoolImportPanel from '$lib/components/sources/PoolImportPanel.svelte';

  // $page.url.searchParams is a URLSearchParams from SvelteKit's
  // page store; read once at render. The panel's `initialName` /
  // `initialRole` are also "apply once at instantiation," so this
  // wrapper-side read mirrors that lifecycle.
  // Narrow to the Literal Role via a typed helper — Svelte's `$:`
  // codegen splits the assignment across an emitted update function,
  // which strips inline narrowing from `?:`. Returning from a typed
  // function preserves the union in the reactive declaration.
  function parseRole(raw: string | null): 'content' | 'reference' | undefined {
    if (raw === 'content' || raw === 'reference') return raw;
    return undefined;
  }
  $: poolParam = $page.url.searchParams.get('pool') ?? undefined;
  $: roleParam = parseRole($page.url.searchParams.get('role'));
</script>

<svelte:head>
  <title>Sources — Station Studio</title>
</svelte:head>

<main>
  <p class="back"><a href="/">← Stations</a></p>
  <h1>Source pools</h1>
  <p class="sub">
    Import iTunes XMLs as named pools. Stations reference pools by
    name (declared in their template); this page is where the actual
    track data lives.
  </p>

  <!--
    c5 mid-slice review 🟡-2: the panel applies initial props ONCE at
    mount, so a same-route navigation (e.g. user already on /sources
    clicks another station's "not imported" badge → URL changes from
    ?pool=A to ?pool=B) would leave the old prefill in place since
    SvelteKit doesn't re-mount on same-route nav. Key the panel by
    the prefill params so a change forces a fresh mount, re-reading
    the props. Trivial cost: any in-app navigation TO /sources with
    different params re-creates the form (losing in-progress edits) —
    acceptable because the user explicitly clicked a new deep-link.
  -->
  {#key `${poolParam ?? ''}|${roleParam ?? ''}`}
    <PoolImportPanel initialName={poolParam} initialRole={roleParam} />
  {/key}
</main>

<style>
  main {
    max-width: 900px;
    margin: 0 auto;
    padding: 2rem 1rem;
  }
  .back {
    margin-bottom: 1rem;
  }
  h1 {
    margin: 0 0 0.5rem;
  }
  .sub {
    color: #666;
    margin-bottom: 2rem;
  }
</style>
