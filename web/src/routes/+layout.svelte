<script lang="ts">
  /**
   * Root layout — mounts the single shared `<ConfirmDialog>` host
   * (§16.25.t4) so any route can call `confirmDialog(...)` and have
   * the modal render on top of the current page.
   *
   * No other layout chrome — pages own their own `<main>`. This file
   * exists ONLY to provide the dialog mount point.
   */
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
</script>

<slot />

<ConfirmDialog />
