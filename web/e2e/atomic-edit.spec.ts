import { test, expect } from '@playwright/test';
import { API } from './fixtures';

/**
 * §16.2.3 c3 — Playwright for the atomic edit write path.
 *
 * vitest pins the form + the api helpers; this proves the full loop
 * end-to-end: click chip → edit param → Save → PATCH → refetch →
 * canvas reflects it → persisted in the DB. Plus the row_template
 * shared-block warning + the optimistic-concurrency 409 path + the
 * export-to-disk affordance.
 *
 * Fixtures POST custom stations inline (the shared minimalTemplate
 * uses `pick`, which has no editable params). A `jingle` block's
 * `genre` is the simplest editable field; no pool needed since we
 * only edit (never generate) here.
 */

function jingleFlatStation(slug: string) {
  return {
    slug,
    name: slug,
    template: {
      station: slug, name: slug, cadence: 'weekly', rng: 'off',
      sources: [{ name: 'A', colour: '#abc', role: 'content' }],
      cursor: { base_advance_per_week: 0, cycle_weeks: 1 },
      layout: {
        unit: 'slot', unit_labels: [], row_start_offsets: [], cursors_init: {},
        flat_slots: [{ source: 'A', strategy: 'jingle', size: 1, genre: 'Day' }],
      },
    },
  };
}

function jingleRowStation(slug: string) {
  return {
    slug,
    name: slug,
    template: {
      station: slug, name: slug, cadence: 'weekly', rng: 'off',
      sources: [{ name: 'A', colour: '#abc', role: 'content' }],
      cursor: { base_advance_per_week: 0, cycle_weeks: 1 },
      layout: {
        unit: 'day', unit_labels: ['Mon', 'Tue'], row_start_offsets: [0, 10],
        cursors_init: {},
        row_template: [{ source: 'A', strategy: 'jingle', size: 1, genre: 'Day' }],
      },
    },
  };
}

async function createStation(request: import('@playwright/test').APIRequestContext, body: object) {
  const r = await request.post(`${API}/api/stations`, { data: body });
  expect(r.status(), await r.text()).toBe(201);
}

test.describe('§16.2.3 atomic edit', () => {
  test('edit a genre → Save → persisted', async ({ page, request }) => {
    await createStation(request, jingleFlatStation('ed3-edit'));

    await page.goto('/stations/ed3-edit/visualise');
    await page.getByTestId('chip').first().click();

    const genre = page.getByTestId('input-genre');
    await expect(genre).toHaveValue('Day');
    // Save is dirty-gated — disabled until the value changes.
    await expect(page.getByTestId('detail-save')).toBeDisabled();
    await genre.fill('Night');
    await page.getByTestId('detail-save').click();

    // After Save the route refetches; the panel re-resolves the block
    // to the saved value (Save goes disabled again — not dirty).
    await expect(page.getByTestId('input-genre')).toHaveValue('Night');

    // Persisted in the DB: the sidecar GET reflects the new genre.
    // The input already shows "Night" once typed, so poll until the save lands.
    await expect
      .poll(async () => {
        const sidecar = await (
          await request.get(`${API}/api/stations/ed3-edit/sidecar`)
        ).json();
        return sidecar.template.layout.flat_slots[0].genre;
      })
      .toBe('Night');
  });

  test('row_template chip shows the shared-block warning', async ({ page, request }) => {
    await createStation(request, jingleRowStation('ed3-row'));
    await page.goto('/stations/ed3-row/visualise');
    await page.getByTestId('chip').first().click();
    await expect(page.getByTestId('shared-block-warning')).toBeVisible();
  });

  test('concurrent change → 409 conflict banner on Save', async ({ page, request }) => {
    await createStation(request, jingleFlatStation('ed3-conflict'));

    // Token the UI will load with.
    const token = (
      await (await request.get(`${API}/api/stations/ed3-conflict/sidecar`)).json()
    ).updated_at;

    await page.goto('/stations/ed3-conflict/visualise');
    await page.getByTestId('chip').first().click();

    // A concurrent edit via the API bumps the server token, making the
    // UI's loaded token stale.
    const patched = await request.patch(`${API}/api/stations/ed3-conflict/blocks`, {
      data: {
        address: { slot_index: 0 },
        fields: { genre: 'Concurrent' },
        expected_updated_at: token,
      },
    });
    expect(patched.status()).toBe(200);

    // The UI's Save now carries the stale token → 409 → conflict banner.
    await page.getByTestId('input-genre').fill('Mine');
    await page.getByTestId('detail-save').click();
    await expect(page.getByTestId('conflict-banner')).toBeVisible();

    // Reload latest clears the banner + installs the fresh token; the
    // concurrent value is now what's shown.
    await page.getByTestId('conflict-reload').click();
    await expect(page.getByTestId('conflict-banner')).toHaveCount(0);
  });

  test('export to disk reports the path', async ({ page, request }) => {
    await createStation(request, jingleFlatStation('ed3-export'));
    await page.goto('/stations/ed3-export/visualise');
    await page.getByTestId('export-template').click();
    await expect(page.getByTestId('export-msg')).toContainText('ed3-export.json');
  });
});
