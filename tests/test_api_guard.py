"""api/guard.py: Studio answers only at this computer's own address (DNS
rebinding), and takes changes only from its own pages (other websites),
while curl and scripts keep working."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_db
from api.guard import ALLOWED_HOSTS_ENV, Guard, allowed_hosts, hostname, same_origin
from api.main import app as studio
from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Config

LOCAL = "http://localhost:8001"


def guarded(environ=None, trusted=()):
    inner = FastAPI()

    @inner.get("/api/thing")
    def read() -> dict:
        return {"ok": True}

    @inner.post("/api/thing")
    def change() -> dict:
        return {"changed": True}

    @inner.get("/")
    def page() -> dict:
        return {"page": True}

    inner.add_middleware(Guard, trusted_origins=trusted, environ=environ or {})
    return inner


@pytest.mark.parametrize("value, name", [
    ("localhost:8001", "localhost"), ("127.0.0.1", "127.0.0.1"), ("[::1]:8001", "::1"),
    ("Studio.LAN:8001", "studio.lan"), ("http://localhost:5173", "localhost"),
    ("", None), (None, None), ("[bad", None),
])
def test_hostname(value, name):
    assert hostname(value) == name


def test_allowed_hosts():
    assert allowed_hosts({}) == {"localhost", "127.0.0.1", "::1"}
    assert allowed_hosts({ALLOWED_HOSTS_ENV: " NAS.local , 192.168.1.5:8001,"}) == {
        "localhost", "127.0.0.1", "::1", "nas.local", "192.168.1.5"}
    assert allowed_hosts({ALLOWED_HOSTS_ENV: "nas.local,*"}) is None


@pytest.mark.parametrize("base", ["http://localhost:8001", "http://127.0.0.1:8001", "http://[::1]:8001"])
def test_this_computer_is_answered(base):
    client = TestClient(guarded(), base_url=base)
    assert client.get("/api/thing").json() == {"ok": True}
    assert client.post("/api/thing").json() == {"changed": True}


def test_another_name_is_refused_and_told_how_to_allow_it():
    """A DNS-rebinding page's requests carry its own name: nothing is
    answered, reading included, and the page itself says what to set."""
    client = TestClient(guarded(), base_url="http://evil.example:8001")
    r = client.get("/api/thing")
    assert r.status_code == 403
    assert r.json()["detail"] == (
        "Station Studio only answers at this computer's own address (localhost or "
        "127.0.0.1). To open it at evil.example, start it with "
        "STATION_STUDIO_ALLOWED_HOSTS=evil.example.")
    page = client.get("/")
    assert page.status_code == 403 and page.headers["content-type"].startswith("text/plain")
    assert "STATION_STUDIO_ALLOWED_HOSTS=evil.example" in page.text
    assert client.post("/api/thing").status_code == 403


def test_a_missing_host_is_refused():
    client = TestClient(guarded(), base_url=LOCAL)
    assert client.get("/api/thing", headers={"host": ""}).status_code == 403


def test_names_in_the_setting_are_answered():
    nas = guarded({ALLOWED_HOSTS_ENV: "nas.local"})
    assert TestClient(nas, base_url="http://nas.local:8001").get("/api/thing").status_code == 200
    assert TestClient(nas, base_url="http://localhost:8001").get("/api/thing").status_code == 200
    assert TestClient(nas, base_url="http://other.lan:8001").get("/api/thing").status_code == 403
    anyone = guarded({ALLOWED_HOSTS_ENV: "*"})
    assert TestClient(anyone, base_url="http://other.lan:8001").get("/api/thing").status_code == 200


@pytest.mark.parametrize("headers", [
    {},  # curl, scripts, the Makefile's prune-exports
    {"sec-fetch-site": "same-origin", "origin": LOCAL},
    {"sec-fetch-site": "none"},
    {"origin": LOCAL},  # a browser without Sec-Fetch-Site
    {"sec-fetch-site": "same-site", "origin": "http://localhost:5173"},  # trusted dev server
])
def test_changes_from_studio_itself_or_outside_a_browser_go_through(headers):
    client = TestClient(guarded(trusted=["http://localhost:5173"]), base_url=LOCAL)
    assert client.post("/api/thing", headers=headers).json() == {"changed": True}


@pytest.mark.parametrize("headers", [
    {"sec-fetch-site": "cross-site", "origin": "https://evil.example"},
    {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site", "origin": "http://localhost:3000"},  # another local page
    {"origin": "https://evil.example"},
    {"origin": "http://localhost:3000"},
    {"origin": "null"},  # sandboxed frames, file:// pages
])
def test_changes_from_other_pages_are_refused(headers):
    client = TestClient(guarded(), base_url=LOCAL)
    for method in ("post", "put", "patch", "delete"):
        r = client.request(method, "/api/thing", headers=headers)
        assert r.status_code == 403, method
        assert r.json() == {"detail": "Only Station Studio's own pages can make changes here."}


def test_reading_from_another_page_is_left_to_the_browser():
    """A cross-site GET is answered: the browser won't let that page read
    it (no CORS for other origins), and Studio's links and downloads are
    GETs that other pages may open."""
    client = TestClient(guarded(), base_url=LOCAL)
    assert client.get("/api/thing", headers={"sec-fetch-site": "cross-site"}).status_code == 200


def test_same_origin_without_a_host():
    assert same_origin({"origin": LOCAL}, None) is False


@pytest.fixture
def studio_client(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/guard.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)
    monkeypatch.setenv("STATION_STUDIO_SKIP_AUTO_SEED", "1")
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    Session = make_sessionmaker(engine)
    out = tmp_path / "exports"
    (out / "pr").mkdir(parents=True)
    with Session() as db:
        db.add(Config(key="playlist_output_dir", value=str(out)))
        db.commit()

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    studio.dependency_overrides[get_db] = _db
    with TestClient(studio, base_url="http://127.0.0.1:8001") as client:
        yield client, out
    studio.dependency_overrides.clear()


def test_another_website_cant_delete_studios_exports(studio_client):
    """The round-2 finding, replayed on Studio itself: a page elsewhere
    making the browser POST prune?confirm=true deleted orphaned exports."""
    client, out = studio_client
    orphan = out / "pr" / "099.xml"
    orphan.write_text("orphan")
    r = client.post("/api/playlists/export/prune?confirm=true",
                    headers={"sec-fetch-site": "cross-site", "origin": "https://evil.example"})
    assert r.status_code == 403
    assert orphan.is_file()
    # Studio's own page, and curl, still can.
    r = client.post("/api/playlists/export/prune?confirm=true",
                    headers={"sec-fetch-site": "same-origin", "origin": "http://127.0.0.1:8001"})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert not orphan.exists()


def test_a_rebinding_page_cant_read_studio(studio_client):
    client, _ = studio_client
    r = client.get("/api/stations", headers={"host": "rebind.evil.example:8001"})
    assert r.status_code == 403
