/**
 * Shared synthetic `DragEvent` builder for vitest.
 *
 * jsdom doesn't ship a real `DataTransfer` constructor, so each
 * drag-source / drag-target test that wanted to exercise dataTransfer
 * had to assemble its own stand-in. The duplicates in `Chip.test.ts`
 * and `SectionHeader.test.ts` drifted apart (one didn't track .types,
 * the other did) — c2 review #4 #6 hoists the builder here so both
 * sides stay honest about the same shape.
 *
 * The store is an internal Map so `setData` calls from the component
 * under test (the drag source) round-trip to the test's `getData`
 * assertion in the same payload-carrying event the browser would
 * deliver.
 */


/** Minimal DataTransfer stand-in matching the surface our code reads. */
export interface MockDataTransfer {
  types: string[];
  getData(key: string): string;
  setData(key: string, value: string): void;
  dropEffect: string;
  effectAllowed: string;
}


/**
 * Construct a synthetic drag-family event with an attached
 * `DataTransfer` stand-in. Pre-populate the payload (drop / dragover
 * targets) by passing `mime` + `data`; omit them to test a source
 * (dragstart) whose code is expected to *call* `setData`.
 *
 * `types` reflects whatever's currently in the store, so a drag-target
 * test can poke at the same MIME the source set + a `getData` round-
 * trip works either direction.
 */
export function makeDragEvent(
  type: string,
  mime?: string,
  data?: string,
): Event {
  const e = new Event(type, { bubbles: true, cancelable: true });
  const store: Record<string, string> = {};
  if (mime !== undefined && data !== undefined) store[mime] = data;
  const dt: MockDataTransfer = {
    get types() { return Object.keys(store); },
    getData: (k: string) => store[k] ?? '',
    setData: (k: string, v: string) => { store[k] = v; },
    dropEffect: 'none',
    effectAllowed: 'all',
  };
  Object.defineProperty(e, 'dataTransfer', { value: dt, writable: false });
  return e;
}
