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

/** Create a station via the API. Throws on non-201. */
export async function createStation(
  request: APIRequestContext,
  slug: string,
  name: string,
  sourceName = 'A',
) {
  const r = await request.post(`${API}/api/stations`, {
    data: { slug, name, template: minimalTemplate(slug, sourceName) },
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
