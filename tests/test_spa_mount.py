"""§16.13 c2 — SvelteKit SPA mount tests.

Two surfaces under test:

  1. `_resolve_static_dir()` — where do we look for the built assets?
     Wheel-bundled location wins over in-repo `web/build/`; neither →
     None (the API-only fallback path covered by existing
     test_root_endpoint_identifies_the_service).

  2. `attach_spa_routes(app, static_dir)` — when we DO have a static
     dir, the SPA is wired correctly: `/` serves index.html, asset
     paths serve real files, unknown paths fall back to index.html,
     and `/api/*` (which shouldn't reach the fallback) gets a real
     404 not a confusing 200-shaped one.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.main import _resolve_static_dir, attach_spa_routes


@pytest.fixture
def fake_static_dir(tmp_path: Path) -> Path:
    """Create a minimal SvelteKit-like build output for SPA tests."""
    build = tmp_path / "build"
    (build / "_app" / "immutable").mkdir(parents=True)
    (build / "index.html").write_text("<html><body>SPA root</body></html>")
    (build / "_app" / "immutable" / "test.js").write_text(
        "// test bundle"
    )
    (build / "favicon.ico").write_bytes(b"\x00\x01\x02")  # minimal binary
    return build


@pytest.fixture
def spa_client(fake_static_dir: Path) -> TestClient:
    """A fresh FastAPI app wired with SPA routes pointing at the fake
    build dir. Importantly, no API routers are attached — this isolates
    SPA-mount behaviour from the rest of the app."""
    app = FastAPI()
    attach_spa_routes(app, fake_static_dir)
    return TestClient(app)


# ---- _resolve_static_dir ----------------------------------------------


def test_resolve_prefers_wheel_bundled_over_in_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If both `station_studio/_bundled/web/` AND `<repo>/web/build/`
    exist (e.g. a developer who pip-installs the wheel while their
    repo's `web/build/` is stale), the wheel-bundled assets win —
    that's the canonical installed artifact."""
    bundled = tmp_path / "bundled_web"
    bundled.mkdir()
    (bundled / "index.html").write_text("from-wheel")

    class FakeFiles:
        def __truediv__(self, _: str) -> "FakeFiles":
            return self

        def is_dir(self) -> bool:
            return True

        def __str__(self) -> str:
            return str(bundled)

    monkeypatch.setattr(
        "api.main.importlib.resources.files", lambda _: FakeFiles()
    )
    assert _resolve_static_dir() == bundled


# Note (c2-review 🟡-2): the in-repo-fallback + neither-exists paths
# of `_resolve_static_dir` are intentionally NOT directly unit-tested
# here. They couple tightly to `Path(__file__).resolve().parent.parent`
# introspection, which is awkward to monkeypatch cleanly. They're
# covered implicitly by `test_root_endpoint_identifies_the_service`
# (which exercises the "neither exists" path in CI, where `web/build/`
# is typically absent) + the Playwright e2e suite (which exercises
# the "in-repo build" path via `npm run preview`).


# ---- attach_spa_routes -------------------------------------------------


def test_spa_root_serves_index_html(spa_client: TestClient) -> None:
    """`GET /` → index.html. The user's browser hits this first on a
    cold load; everything else (client-side router, hydration, fetches)
    cascades from here."""
    r = spa_client.get("/")
    assert r.status_code == 200
    assert "SPA root" in r.text
    # text/html, not application/json — proves it's actually the SPA
    # shell and not a JSON blob accidentally matching path `/`.
    assert r.headers["content-type"].startswith("text/html")


def test_spa_assets_served_via_static_mount(spa_client: TestClient) -> None:
    """`/_app/immutable/test.js` → the real file. SvelteKit emits all
    its bundles under `/_app/`; without this mount they'd 404."""
    r = spa_client.get("/_app/immutable/test.js")
    assert r.status_code == 200
    assert b"test bundle" in r.content


def test_spa_real_top_level_file_served_directly(
    spa_client: TestClient,
) -> None:
    """`/favicon.ico` is a top-level static file (not under /_app/),
    so the catch-all checks for it BEFORE serving index.html. Without
    this, the favicon request would receive the HTML SPA shell and
    browsers would render a broken-image favicon."""
    r = spa_client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.content == b"\x00\x01\x02"


def test_spa_unknown_client_route_falls_back_to_index_html(
    spa_client: TestClient,
) -> None:
    """`/stations/some-slug` doesn't match any file. The catch-all
    serves index.html; the client-side router boots and handles the
    route from there. Without the fallback the user gets a 404 on
    every deep link."""
    r = spa_client.get("/stations/anything")
    assert r.status_code == 200
    assert "SPA root" in r.text


def test_spa_nested_unknown_route_falls_back_to_index_html(
    spa_client: TestClient,
) -> None:
    """`{path:path}` matches nested slashes — `/a/b/c/d` all hit the
    SPA fallback. Locks the converter type against an accidental
    change to `{path}` which only matches single segments."""
    r = spa_client.get("/very/deeply/nested/route")
    assert r.status_code == 200
    assert "SPA root" in r.text


def test_spa_api_path_returns_404_not_spa(spa_client: TestClient) -> None:
    """When `/api/missing` reaches the catch-all (no real router
    matched), we return a real 404 instead of the SPA shell. Returning
    HTML for a JSON-shaped path is a debugging nightmare — the client
    library would error trying to parse it. This catches a router
    accidentally being un-registered."""
    r = spa_client.get("/api/missing")
    assert r.status_code == 404


# ---- c2 mid-slice review fixes ----------------------------------------


def test_spa_index_html_sets_no_cache_header(spa_client: TestClient) -> None:
    """c2-review 🟡-1: index.html ships with `Cache-Control: no-cache`
    so a deploy that ships new content-hashed asset filenames doesn't
    leave users on a stale-cached index.html pointing at 404'd
    hashes. The hashed assets themselves (under `_app/immutable/`)
    can cache aggressively via StaticFiles's default headers; this
    only affects the entry HTML."""
    r = spa_client.get("/")
    assert r.headers.get("cache-control") == "no-cache"

    # Same on the SPA fallback path — `/some/client/route` also resolves
    # to index.html, which carries the same header.
    r2 = spa_client.get("/client-route-anywhere")
    assert r2.headers.get("cache-control") == "no-cache"


def test_spa_does_not_swallow_fastapi_auto_docs(
    fake_static_dir: Path,
) -> None:
    """c2-review 🟡-3: FastAPI auto-registers `/openapi.json`, `/docs`,
    `/redoc` in its constructor — BEFORE `attach_spa_routes` runs.
    Routing precedence means those routes match first; the SPA
    catch-all only sees what falls through. A future refactor that
    reverses the order (or removes the auto-docs trust) would
    silently start serving HTML for `/openapi.json`, breaking every
    OpenAPI-consuming tool. This test pins the precedence."""
    app = FastAPI()
    attach_spa_routes(app, fake_static_dir)
    client = TestClient(app)

    # /openapi.json wins over the SPA fallback → JSON, not HTML.
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body.get("openapi", "").startswith("3.")

    # /docs wins → Swagger UI HTML, NOT the SPA shell. Both happen to
    # be HTML, so we discriminate via the response body content (the
    # Swagger UI page has "swagger" or similar marker copy; the SPA
    # shell has our test fixture's "SPA root").
    r = client.get("/docs")
    assert r.status_code == 200
    assert "SPA root" not in r.text  # not the SPA fallback
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()
