import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  API,
  createStation,
  importTinyPool,
  generate,
} from './fixtures';

/**
 * Full loop:
 *
 *   import tiny-library → create station → generate (API) →
 *   visit trace viewer → download exported XML →
 *   re-import the exported XML as a new pool → verify tracks survived
 *
 * Exercises the real UI nav (landing → detail → trace → download)
 * and proves the exporter/importer fidelity loop via the API.
 */
test.describe('end-to-end roundtrip', () => {
  test('import → create → generate → download → re-import', async ({
    page,
    request,
  }) => {
    // 1. Seed a pool and station.
    await importTinyPool(request, 'A');
    await createStation(request, 'roundtrip', 'Roundtrip FM');
    const { playlist_id } = await generate(request, 'roundtrip', 1);

    // 2. UI navigation: landing → detail → trace.
    await page.goto('/');
    await page.getByRole('link', { name: 'Roundtrip FM' }).click();
    await page.waitForURL('**/stations/roundtrip');
    await page.getByRole('link', { name: /view/ }).first().click();
    await page.waitForURL(`**/playlists/${playlist_id}`);
    await expect(page.getByRole('heading', { name: 'Your working' })).toBeVisible();

    // 3. Fetch the exported XML bytes via the API (Playwright's download
    //    handling is fiddly for inline responses; grab the bytes directly).
    const r = await request.get(`${API}/api/playlists/${playlist_id}/export`);
    expect(r.status()).toBe(200);
    const xml = await r.body();
    expect(xml.length).toBeGreaterThan(200);

    // 4. Write to a temp file and re-import as a second pool, proving the
    //    exported XML is a valid iTunes plist the importer accepts.
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'roundtrip-'));
    const xmlPath = path.join(tmpDir, 'exported.xml');
    fs.writeFileSync(xmlPath, xml);

    const reimport = await request.post(`${API}/api/sources/import`, {
      data: { path: xmlPath, name: 'RE_IMPORT' },
    });
    expect(reimport.status()).toBe(200);
    const summary = await reimport.json();
    // tiny-library has 3 tracks; the 1-slot playlist draws from pool A so
    // only the single chosen track rides through the export. Track count
    // in the re-imported pool therefore is 1.
    expect(summary.tracks_imported).toBe(1);
    expect(summary.entries_imported).toBe(1);

    // 5. And the list endpoint now reports two pools (A + RE_IMPORT).
    const list = await request.get(`${API}/api/sources`);
    const sources = await list.json();
    const names = sources.map((s: { name: string }) => s.name).sort();
    expect(names).toContain('A');
    expect(names).toContain('RE_IMPORT');

    fs.rmSync(tmpDir, { recursive: true });
  });
});
