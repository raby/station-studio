// Registers @testing-library/svelte's vitest hook, which flushes Svelte's
// scheduler (so onMount side-effects settle before the next test) and
// unmounts components. Without this, async onMount work (fetches, store
// subscriptions) is invisible to assertions.
import '@testing-library/svelte/vitest';
