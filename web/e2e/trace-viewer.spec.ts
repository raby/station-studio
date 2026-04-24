import { test, expect } from '@playwright/test';
import { createStation, importTinyPool, generate, API } from './fixtures';

test.describe('trace viewer', () => {
  test('renders slot rows and a Download iTunes XML link', async ({
    page,
    request,
  }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'trv', 'Trace View Station');
    const { playlist_id } = await generate(request, 'trv', 1);

    await page.goto(`/playlists/${playlist_id}`);

    await expect(
      page.getByRole('heading', { name: /Trace View Station/ }),
    ).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Your working' })).toBeVisible();

    // At least one slot row must appear, with source chip 'A'.
    const chips = page.locator('.chip');
    await expect(chips.first()).toBeVisible();
    await expect(chips.first()).toHaveText('A');

    // Download link targets the export endpoint.
    const dl = page.getByRole('link', { name: 'Download iTunes XML' });
    await expect(dl).toHaveAttribute(
      'href',
      `/api/playlists/${playlist_id}/export`,
    );
  });

  test('download link returns an iTunes-shaped plist', async ({ request }) => {
    await importTinyPool(request, 'A');
    await createStation(request, 'trv-dl', 'Download Station');
    const { playlist_id } = await generate(request, 'trv-dl', 1);

    const r = await request.get(`${API}/api/playlists/${playlist_id}/export`);
    expect(r.status()).toBe(200);
    const body = await r.text();
    expect(body).toContain('<?xml');
    expect(body).toContain('//Apple Computer//DTD PLIST 1.0//EN');
    expect(body).toContain('Download Station 001'); // playlist Name derives from station.name + week
  });
});
