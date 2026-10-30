/**
 * Test helper: render a component and listen to the events it
 * dispatches with `createEventDispatcher`.
 *
 * Svelte 4 tests did `const { component } = render(X, props)` then
 * `component.$on('save', handler)`. Svelte 5 components have no `$on`;
 * listeners go in at mount as `events`. Svelte looks a listener up when
 * the event is dispatched, not at mount, so `on()` can still be called
 * after render, keeping the tests' render-then-listen shape.
 *
 * Components moved to runes take callback props (`onsave`) instead,
 * and their tests pass those as props; this helper is for the ones
 * still on `createEventDispatcher`.
 */
import { render } from '@testing-library/svelte';
import type { Component, SvelteComponent } from 'svelte';

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- events carry any detail
type Handler = (e: CustomEvent<any>) => void;

// Legacy (Svelte 4 syntax) components are classes; runes ones are functions.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyComponent = Component<any> | (new (...args: any[]) => SvelteComponent);

export function renderWithEvents(Comp: AnyComponent, props: Record<string, unknown>) {
  // One handler per event name: each test listens once per name, as
  // it did with $on. A second on() for the same name replaces the first.
  const events: Record<string, Handler> = {};
  const result = render(Comp as Component, { props, events } as never);
  return {
    ...result,
    on(name: string, handler: Handler): void {
      events[name] = handler;
    },
  };
}
