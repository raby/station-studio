import { test, expect } from "@playwright/test";
import { API } from "./fixtures";

/**
 * api/guard.py in a real browser: a page on another site can't make
 * Station Studio change anything, while Studio's own page still can.
 * The browser marks where each request comes from (Sec-Fetch-Site,
 * Origin) and no page can change that; the backend tests fake those
 * headers, this checks that Chromium really sends them.
 *
 * The other site is 127.0.0.1:8001, the address the backend runs at,
 * posting to Studio at localhost:5173 (whose /api goes on to the same
 * backend): to the browser, localhost and 127.0.0.1 are different
 * sites, and both are real servers on this computer. The change tried
 * is a dry-run prune, one of the body-less POSTs a page elsewhere could
 * send; without the guard it's answered 200.
 */
const PRUNE = "/api/playlists/export/prune";

test.describe("other websites", () => {
  test("a page on another site can't make changes", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${API}/`);
    const target = `${baseURL}${PRUNE}`;
    const answered = page.waitForResponse(target);
    // no-cors: what a page elsewhere can send without asking Studio first.
    await page.evaluate(
      (url) =>
        fetch(url, { method: "POST", mode: "no-cors" }).catch(() => null),
      target,
    );
    expect((await answered).status()).toBe(403);
  });

  test("Studio's own page still can", async ({ page }) => {
    await page.goto("/");
    const status = await page.evaluate(
      async (url) => (await fetch(url, { method: "POST" })).status,
      PRUNE,
    );
    expect(status).toBe(200);
  });
});
