import json
import time

from station_player import picks
from station_player.picks import Picks, clean

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40


def feed(*items, title="From Rebirth"):
    return {"title": title, "picks": list(items)}


def test_clean_keeps_only_what_makes_sense():
    data = clean(feed(
        {"title": "Classics", "station": "http://radio/c", "text": "  Old   skool ", "button": "Play the mix",
         "from": "06:00", "to": "10:00", "days": ["Mon", "tuesday", "funday"], "starts": "2026-01-01",
         "ends": "nope", "image": "picks/c.png"},
        {"title": "No station"},
        {"title": "Bad link", "station": "file:///etc/passwd"},
        {"title": "Half a time", "station": "https://radio/h", "from": "09:00", "image": "https://elsewhere.example/x.png"},
        "junk",
    ))
    assert data["title"] == "From Rebirth"
    first, second = data["picks"]
    assert first == {"id": "0", "title": "Classics", "text": "Old skool", "button": "Play the mix",
                     "station": "http://radio/c", "name": "Classics",
                     "image": "https://raby.github.io/station-studio/feed/picks/c.png",
                     "from": "06:00", "to": "10:00", "days": ["mon", "tue"], "starts": "2026-01-01"}
    # Only from and to together; pictures only from the picks' own site.
    assert second["id"] == "3" and "from" not in second and "image" not in second and second["button"] == "Listen"
    assert clean([]) == {"title": "", "picks": []}
    assert len(clean(feed(*[{"title": f"{i}", "station": "http://r/"} for i in range(50)]))["picks"]) == picks.MAX_PICKS


def test_the_shipped_picks_are_valid():
    raw = json.loads(picks.BUNDLED.read_text(encoding="utf-8"))
    assert len(clean(raw)["picks"]) == len(raw["picks"]) > 0


def test_fetched_hourly_and_kept_when_broken(tmp_path, monkeypatch):
    answers = [json.dumps(feed({"title": "Live", "station": "http://radio/live"})).encode()]

    def fetch(url, limit):
        answer = answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer, "application/json"

    p = Picks(tmp_path, fetch=fetch)
    assert p.current()["picks"][0]["title"] == "Rebirth Radio"  # the shipped copy until the first fetch
    p.refresh()
    assert [x["title"] for x in p.current()["picks"]] == ["Live"]
    assert p.lists("http://radio/live") and not p.lists("http://radio/other")
    # A broken file (or no network) keeps the last good list.
    answers[0] = b"{not json"
    p.refresh()
    assert p.current()["picks"][0]["title"] == "Live"
    answers[0] = OSError("offline")
    p.refresh()
    assert p.current()["picks"][0]["title"] == "Live"
    # The last good list survives a restart.
    assert Picks(tmp_path, fetch=fetch).current()["picks"][0]["title"] == "Live"
    # Not asked again within the hour.
    calls = []
    p2 = Picks(tmp_path, fetch=lambda u, n: calls.append(u) or (b"{}", ""))
    p2.current()
    for _ in range(100):
        if calls:
            break
        time.sleep(0.01)
    p2.current()
    time.sleep(0.05)
    assert len(calls) == 1


def test_images(tmp_path):
    fetched = []

    def fetch(url, limit):
        fetched.append(url)
        if url.endswith(".json"):
            return json.dumps(feed(
                {"title": "A", "station": "http://r/a", "image": "art/a.png"},
                {"title": "B", "station": "http://r/b", "image": "art/b.png"},
                {"title": "C", "station": "http://r/c"})).encode(), ""
        return (PNG if url.endswith("a.png") else b"<html>not an image"), "image/png"

    p = Picks(tmp_path, fetch=fetch)
    p.refresh()
    assert p.image("0") == (PNG, "image/png")
    assert p.image("0") == (PNG, "image/png")
    assert sum(u.endswith("a.png") for u in fetched) == 1  # kept after the first time
    assert p.image("1") is None  # says it's a PNG, isn't
    assert p.image("2") is None and p.image("99") is None


def test_server_relays_and_remembers_picked_stations(tmp_path):
    from station_player.config import ConfigStore
    from station_player.server import PlayerApp

    app = PlayerApp(ConfigStore(tmp_path / "home"), scan=False)
    app.picks = Picks(tmp_path / "p", fetch=lambda u, n: (json.dumps(feed({"title": "Mix", "station": "http://radio/mix"})).encode(), ""))
    app.picks.refresh()
    assert app.can_preview("http://radio/mix") and not app.can_preview("http://radio/other")
    app.station_played("Mix", "http://radio/mix")
    assert app.store.config.recent_stations[0]["url"] == "http://radio/mix"
