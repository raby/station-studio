import type { APIRequestContext } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, '../..');

export const API = 'http://127.0.0.1:8001';

/** Absolute path to the tiny-library fixture (3 tracks). */
export const TINY_LIBRARY_PATH = path.join(
  REPO_ROOT,
  'tests/fixtures/tiny-library.xml',
);

/** Absolute path to the malformed-library fixture — for partial-failure
 *  + invalid-XML e2e cases. */
export const MALFORMED_LIBRARY_PATH = path.join(
  REPO_ROOT,
  'tests/fixtures/malformed-library.xml',
);

/** Canonical minimal template — one source, one Mon row, one pick slot.
 *
 *  `name` matches production templates (stations/*.json all carry it).
 *  Earlier omission was tolerated because most consumers skipped that
 *  field; §16.2.2.t1's runtime validator catches the drift, so the
 *  fixture now mirrors real shape. */
export function minimalTemplate(slug: string, sourceName = 'A') {
  return {
    station: slug,
    name: slug,
    cadence: 'weekly',
    rng: 'off',
    sources: [{ name: sourceName, colour: '#117733', role: 'content' }],
    cursor: { base_advance_per_week: 0, cycle_weeks: 1 },
    layout: {
      unit: 'day',
      unit_labels: ['Mon'],
      row_start_offsets: [0],
      row_template: [{ source: sourceName, strategy: 'pick', size: 1 }],
    },
  };
}

/** Canonical minimal flat_slots template — one source, three jingle
 *  slots. §16.2.5+ structural-edit + section-composition specs need
 *  flat_slots (row_template structural edits are D8 punts — server
 *  rejects with 400). Sibling to `minimalTemplate` so the e2e fan-out
 *  doesn't grow N inline 30-line literals per spec.
 *
 *  Slice-review sweep extracted this from a `createFlatStation` copy
 *  in visualise-edit.spec.ts. */
export function minimalFlatTemplate(slug: string, sourceName = 'A') {
  return {
    station: slug,
    name: slug,
    cadence: 'weekly',
    rng: 'off',
    sources: [{ name: sourceName, colour: '#117733', role: 'content' }],
    cursor: { base_advance_per_week: 0, cycle_weeks: 1 },
    layout: {
      unit: 'slot',
      unit_labels: [],
      row_start_offsets: [],
      cursors_init: {},
      flat_slots: [
        { source: sourceName, strategy: 'jingle', size: 1, genre: 'G' },
        { source: sourceName, strategy: 'jingle', size: 2, genre: 'G' },
        { source: sourceName, strategy: 'jingle', size: 4, genre: 'G' },
      ],
    },
  };
}

/** Create a station via the API. Throws on non-201.
 *
 *  Accepts an optional `template` override for specs that need a
 *  non-default layout shape (e.g. `minimalFlatTemplate` for
 *  §16.2.5+ structural-edit specs). Defaults to `minimalTemplate`
 *  for backward compatibility. */
export async function createStation(
  request: APIRequestContext,
  slug: string,
  name: string,
  sourceName = 'A',
  template?: ReturnType<typeof minimalTemplate>,
) {
  const r = await request.post(`${API}/api/stations`, {
    data: {
      slug, name,
      template: template ?? minimalTemplate(slug, sourceName),
    },
  });
  if (r.status() !== 201) {
    throw new Error(
      `createStation(${slug}) failed: ${r.status()} ${await r.text()}`,
    );
  }
  return r.json();
}

/** Import the tiny-library fixture as a source pool. */
export async function importTinyPool(request: APIRequestContext, name: string) {
  const r = await request.post(`${API}/api/sources/import`, {
    data: { path: TINY_LIBRARY_PATH, name },
  });
  if (!r.ok()) {
    throw new Error(
      `importTinyPool(${name}) failed: ${r.status()} ${await r.text()}`,
    );
  }
  return r.json();
}

/** Generate a playlist for a station + week via the API. Returns playlist_id/trace_id. */
export async function generate(
  request: APIRequestContext,
  slug: string,
  week: number,
  seed?: number,
) {
  const r = await request.post(`${API}/api/stations/${slug}/generate`, {
    data: seed === undefined ? { week } : { week, seed },
  });
  if (!r.ok()) {
    throw new Error(`generate(${slug}, ${week}) failed: ${r.status()} ${await r.text()}`);
  }
  return r.json();
}
