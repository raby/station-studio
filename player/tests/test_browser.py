"""The player page in a real browser (Chromium via Playwright): gapless
playback, the queue, resuming, library search, volume levelling and the
new-version notice. Skipped when Playwright isn't installed."""
import json
import math
import re
import os
import struct
import subprocess
import threading
import urllib.request
import time
import wave
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

# CI sets this so a broken browser install fails instead of skipping.
REQUIRED = os.environ.get("STATION_PLAYER_REQUIRE_BROWSER") == "1"
if REQUIRED:
    import playwright.sync_api as sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

from station_player.config import ConfigStore  # noqa: E402
from station_player.directory import Directory  # noqa: E402
from station_player.server import PlayerApp, make_server  # noqa: E402

RATE = 22050


def offline(url, user_agent, limit):
    raise OSError("no internet in tests")


def tone(path: Path, seconds: float, amplitude: float, freq: float = 440.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(RATE * seconds)
    frames = b"".join(
        struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / RATE))) for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    return path


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        except Exception as exc:  # no browser downloaded
            if REQUIRED:
                raise
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def player(tmp_path):
    music = tmp_path / "Music"
    tone(music / "01 One.wav", 1.5, 0.5, 440)
    tone(music / "02 Two.wav", 3.0, 0.5, 550)
    tone(music / "03 Three.wav", 1.5, 0.5, 660)
    tone(music / "04 Quiet.wav", 2.0, 0.05, 330)
    store = ConfigStore(tmp_path / "home")
    store.config.studio = True  # everything shows (a new install without station-studio is the plain player)
    store.config.music_folders = [str(music)]
    store.config.stations = [{"name": "Test FM", "url": "http://127.0.0.1:9/stream"}]
    app = PlayerApp(store)
    app.directory = Directory(fetch=offline)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{server.server_address[1]}/", tmp_path
    server.shutdown()
    server.server_close()


def open_settings(page, tab):
    page.locator(".nav-item[data-view=settings]").click()
    page.locator(f"#tab-{tab}").click()


@pytest.fixture
def page(browser, player):
    ctx = browser.new_context()
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(player[1])
    pg.wait_for_function("() => S.view.type === 'radio'")  # a new window opens on the Radio page
    pg.locator("#nav-folders .nav-item").first.click()
    pg.wait_for_selector("#view tbody tr")
    yield pg
    ctx.close()
    assert not errors, errors


def now_title(page) -> str:
    return page.locator("#now-title").inner_text()


def test_tracks_play_back_to_back_without_a_gap(page):
    page.evaluate("""() => {
      window.events = [];
      for (const d of decks) for (const type of ["playing", "ended"]) {
        d.addEventListener(type, () => window.events.push([type, d.dataset.trackId || "", performance.now()]));
      }
    }""")
    page.locator("#view tbody tr").first.dblclick()
    page.wait_for_function("() => events.filter(e => e[0] === 'ended').length >= 2", timeout=15000)
    events = page.evaluate("events")
    playing = [e for e in events if e[0] == "playing"]
    ended = [e for e in events if e[0] == "ended"]
    assert len({e[1] for e in playing}) >= 3
    # The next track was already playing when the last one ended.
    for end in ended[:2]:
        nxt = next(e for e in playing if e[1] != end[1] and e[2] > end[2] - 500)
        assert nxt[2] - end[2] < 30, events
    assert page.evaluate("S.order.length") == 4


def test_play_next_and_add_to_queue(page):
    rows = page.locator("#view tbody tr")
    rows.nth(0).dblclick()
    page.wait_for_function("() => !decks[deckIdx].paused")
    rows.nth(3).focus()
    page.keyboard.press("q")
    rows.nth(2).focus()
    page.keyboard.press("n")
    assert page.evaluate("S.upNext.map(t => t.name)") == ["Three", "Quiet"]
    assert page.locator("#upnext-count").inner_text() == "2"
    page.locator(".nav-item[data-view=upnext]").click()
    assert "Three" in page.locator("#view").inner_text()
    # Queued tracks play next, then the playlist carries on after "One".
    page.wait_for_function("() => current() && current().name === 'Three'", timeout=10000)
    page.wait_for_function("() => current() && current().name === 'Quiet'", timeout=10000)
    page.wait_for_function("() => current() && current().name === 'Two'", timeout=10000)
    assert page.evaluate("S.upNext.length") == 0


def test_track_menu_from_the_more_button(page):
    row = page.locator("#view tbody tr").nth(1)
    row.hover()
    row.locator(".row-more").click()
    menu = page.locator("#track-menu")
    assert menu.is_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")  # "Add to queue"
    assert menu.is_hidden()
    # Nothing was playing, so the queued track starts.
    page.wait_for_function("() => current() && current().name === 'Two' && S.extra")


def test_resumes_where_it_left_off(page):
    page.locator("#view tbody tr").nth(1).dblclick()
    page.wait_for_function("() => decks[deckIdx].currentTime > 0.6")
    page.evaluate("audio.pause()")
    at = page.evaluate("audio.currentTime")
    page.reload()
    page.wait_for_function("() => current() && current().name === 'Two'")
    page.wait_for_function("() => audio.readyState >= 1 && audio.currentTime > 0")
    assert page.evaluate("audio.paused")
    assert abs(page.evaluate("audio.currentTime") - at) < 0.15
    assert now_title(page) == "Two"
    page.locator("#btn-play").click()
    page.wait_for_function("() => current().name === 'Three'", timeout=10000)


def test_resume_drops_tracks_that_left_the_library(page, player):
    app, _, tmp = player
    page.locator("#view tbody tr").nth(2).dblclick()
    page.wait_for_function("() => current() && current().name === 'Three'")
    page.evaluate("audio.pause()")
    (tmp / "Music" / "02 Two.wav").unlink()
    app.rescan()
    page.reload()
    page.wait_for_function("() => current() && current().name === 'Three'")
    assert page.evaluate("S.queue.map(t => t.name)") == ["One", "Three", "Quiet"]
    assert page.evaluate("S.queue[S.order[S.pos]].name") == "Three"


def test_resume_a_radio_station(page):
    page.locator(".nav-item[data-view=radio]").click()
    page.evaluate("playStation(S.lib.stations[0], { autoplay: false })")
    page.reload()
    page.wait_for_function("() => S.mode === 'radio'")
    assert "Test FM" in now_title(page)
    assert page.evaluate("radioEl.paused")


@pytest.fixture
def tone_station(tmp_path):
    """A "station" on this computer: a tone the player's relay can play."""
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    tone(tmp_path / "streams" / "tone.wav", 20.0, 0.4)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path / "streams")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/tone.wav"
    server.shutdown()
    server.server_close()


def test_tuner_tunes_between_stations_and_the_wave_moves(page, player, tone_station):
    app = player[0]
    app.store.config.stations = [{"name": n, "url": f"{tone_station}?{i}"} for i, n in enumerate(["One FM", "Two FM", "Three FM"])]
    page.evaluate("loadState()")
    page.locator(".nav-item[data-view=radio]").click()
    tuner = page.locator("#tuner")
    sync_api.expect(tuner).to_have_attribute("aria-valuetext", "Not tuned")
    sync_api.expect(page.locator("#tuner-needle")).to_be_hidden()
    page.locator(".tuner-name", has_text="Two FM").click()
    page.wait_for_function("() => S.mode === 'radio' && !audio.paused && audio === radioRelay", timeout=10000)
    sync_api.expect(tuner).to_have_attribute("aria-valuetext", "Two FM")
    sync_api.expect(page.locator(".tuner-name.on")).to_have_text("Two FM")
    # The wave listens to the relay and moves with the sound.
    sync_api.expect(page.locator("#wave")).to_be_visible()
    page.wait_for_function("() => { const an = waveSource(); if (!an) return false; an.getFloatTimeDomainData(WAVE.buf);"
                           " return WAVE.buf.some((v) => Math.abs(v) > 0.05); }", timeout=10000)
    # Keys tune to the next station along.
    tuner.focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_function("() => S.station.name === 'Three FM'")
    sync_api.expect(tuner).to_have_attribute("aria-valuetext", "Three FM")
    page.keyboard.press("Home")
    page.wait_for_function("() => S.station.name === 'One FM'")
    # Dragging the needle hisses between stations, and lets go on the nearest.
    box = tuner.bounding_box()
    y = box["y"] + box["height"] * 0.5  # on the scale, between the names
    page.mouse.move(box["x"] + box["width"] * 0.2, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.5, y, steps=5)
    page.mouse.move(box["x"] + box["width"] * 0.62, y, steps=5)
    assert page.evaluate("TUNE.noise !== null && TUNE.noise.hum.length === 3")  # static over a hum
    page.mouse.up()
    page.wait_for_function("() => S.station.name === 'Two FM' && TUNE.noise === null")
    # Paused, the wave lies flat.
    page.evaluate("pauseAll()")
    page.wait_for_function("() => waveSource() === null")
    # Names take turns above and below the scale, each with its logo and a line to its spot.
    sync_api.expect(page.locator("#tuner .tuner-line")).to_have_count(3)
    sync_api.expect(page.locator("#tuner .tuner-name .glyph, #tuner .tuner-name .station-art")).to_have_count(3)
    scale = page.locator(".tuner-scale").bounding_box()
    above, below, again = (n.bounding_box() for n in page.locator(".tuner-name").all())
    assert above["y"] + above["height"] <= scale["y"] and below["y"] >= scale["y"] + scale["height"]
    assert again["y"] + again["height"] <= scale["y"]
    sync_api.expect(tuner).not_to_have_class(re.compile("logos-only"))
    # Crowded, only the logos show, with the names to hover over.
    app.store.config.stations = [{"name": f"A long station name {i}", "url": f"{tone_station}?{i}"} for i in range(14)]
    page.evaluate("loadState()")
    page.locator(".nav-item[data-view=home]").click()
    page.locator(".nav-item[data-view=radio]").click()
    sync_api.expect(page.locator("#tuner")).to_have_class(re.compile("logos-only"))
    sync_api.expect(page.locator(".tuner-name").first.locator(".tuner-label")).to_be_hidden()
    sync_api.expect(page.get_by_role("button", name="A long station name 3", exact=True)).to_have_count(1)


def test_ambient_mode(page):
    page.locator("#btn-ambient").click()
    box = page.locator("#ambient")
    sync_api.expect(box).to_be_visible()
    for key in ["fish", "waves", "rain", "stream", "night", "fire", "birds"]:
        page.locator(f'#ambient .seg[data-scene="{key}"]').click()
        sync_api.expect(page.locator(f'#ambient .seg[data-scene="{key}"]')).to_have_attribute("aria-pressed", "true")
        page.wait_for_function("() => AMB.sound && AMB.ctx.state === 'running'")
        page.wait_for_timeout(300)  # a few frames drawn (a drawing error fails the test)
        # The fish tank has no recording, so it makes its own sound;
        # the others swap to theirs once it's loaded.
        if key == "fish":
            assert page.evaluate("AMB.sound.real") is False
        else:
            page.wait_for_function("() => AMB.sound.real", timeout=15000)
    assert page.evaluate("store('ambient-scene')") == "birds"
    # Its volume, and the sleep timer's fade, set the level.
    page.locator("#ambient input[type=range]").fill("0.2")
    assert page.evaluate("AMB.volume") == 0.2
    page.evaluate("volMul.sleep = 0.5; applyVolume()")
    page.wait_for_function("() => Math.abs(AMB.out.gain.value - 0.05) < 0.01")
    page.evaluate("volMul.sleep = 1; applyVolume()")
    # The controls fade after a few still seconds, and come back on a move.
    page.wait_for_function("() => $('#ambient').classList.contains('still')", timeout=6000)
    page.mouse.move(200, 200)
    sync_api.expect(box).not_to_have_class(re.compile("still"))
    page.keyboard.press("Escape")
    sync_api.expect(box).to_be_hidden()
    assert page.evaluate("AMB.sound") is None
    sync_api.expect(page.locator("#btn-ambient")).to_be_focused()
    # Opened again it starts the last scene; the sleep timer's end stops its sound.
    page.locator("#btn-ambient").click()
    sync_api.expect(page.locator('#ambient .seg[aria-pressed="true"]')).to_have_text("Birds")
    page.evaluate("setSleep(0.001)")
    page.wait_for_function("() => AMB.sound === null", timeout=5000)
    page.locator("#btn-ambient-close").click()


def test_ambient_plays_a_recording_when_there_is_one(page, tmp_path):
    # A recording for Rain, as the app would ship it in static/ambient/.
    rec = tone(tmp_path / "rain.wav", 2.0, 0.3, 300).read_bytes()
    listing = {"rain": {"file": "rain.ogg", "title": "Rain on a roof", "by": "Someone", "licence": "CC0"}}
    page.route("**/ambient/sounds.json", lambda r: r.fulfill(json=listing))
    page.route("**/ambient/rain.ogg", lambda r: r.fulfill(body=rec, content_type="audio/ogg"))
    page.locator("#btn-ambient").click()
    page.locator('#ambient .seg[data-scene="rain"]').click()
    page.wait_for_function("() => AMB.sound && AMB.sound.real")
    sync_api.expect(page.locator("#ambient .ambient-credit")).to_have_text("Rain on a roof by Someone (CC0)")
    # Round it goes: a second time through is lined up before the first ends.
    page.wait_for_function("() => AMB.sound.parts.length >= 1")
    # A scene without one keeps its made-up sound, and loses the credit.
    page.locator('#ambient .seg[data-scene="fish"]').click()
    page.wait_for_function("() => AMB.sound && AMB.ctx.state === 'running'")
    page.wait_for_timeout(300)
    assert page.evaluate("AMB.sound.real") is False
    sync_api.expect(page.locator("#ambient .ambient-credit")).to_be_hidden()
    page.locator("#btn-ambient-close").click()


def test_ambient_recordings_are_served(player):
    url = player[1] + "ambient/sounds.json"
    with urllib.request.urlopen(url) as r:
        assert r.headers["Content-Type"] == "application/json"
        listing = json.load(r)
    for info in listing.values():
        with urllib.request.urlopen(player[1] + "ambient/" + info["file"]) as r:
            assert r.headers["Content-Type"].startswith("audio/")


def test_mini_player_pops_out_and_back(page):
    # Chrome and Edge: the bar moves into a picture-in-picture window.
    btn = page.locator("#btn-mini")
    sync_api.expect(btn).to_have_attribute("aria-label", "Pop out the player")
    btn.click()
    page.wait_for_function("() => MINI.on && miniDoc && miniDoc.querySelector('.bar')")
    assert page.evaluate("document.querySelector('.bar')") is None
    page.wait_for_function("() => getComputedStyle(miniDoc.querySelector('.bar')).position === 'fixed'")
    # Its buttons still work, and what's playing shows there.
    page.evaluate("miniDoc.querySelector('#btn-play').click()")
    page.wait_for_function("() => !audio.paused")
    page.wait_for_function("() => miniDoc.querySelector('#now-title').textContent === 'One'")
    assert page.evaluate("miniDoc.querySelector('#btn-mini').getAttribute('aria-label')") == "Put the player back"
    page.evaluate("miniDoc.querySelector('#btn-mini').click()")
    page.wait_for_function("() => !MINI.on && document.querySelector('.app > .bar')")
    sync_api.expect(btn).to_have_attribute("aria-label", "Pop out the player")
    assert page.evaluate("!audio.paused")
    # Closing the little window puts it back too.
    btn.click()
    page.wait_for_function("() => MINI.on && miniDoc")
    page.evaluate("MINI.win.close()")
    page.wait_for_function("() => !MINI.on && document.querySelector('.app > .bar')")
    sync_api.expect(page.locator("#btn-play")).to_be_visible()


def test_mini_player_in_the_mac_app(browser, player):
    # As in the Mac app's own window, which hears what the page says.
    ctx = browser.new_context()
    ctx.add_init_script("""
        window.said = [];
        window.webkit = { messageHandlers: { stationApp: { postMessage: (m) => window.said.push(m) } } };
    """)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(player[1])
    pg.wait_for_function("() => launch.ready")
    btn = pg.locator("#btn-mini")
    sync_api.expect(btn).to_have_attribute("aria-label", "Pop out the player")
    btn.click()
    assert pg.evaluate("said") == ["mini"]
    sync_api.expect(pg.locator("body")).to_have_class(re.compile("mini"))
    sync_api.expect(pg.locator(".sidebar")).to_be_hidden()
    sync_api.expect(pg.locator("#btn-play")).to_be_visible()
    sync_api.expect(btn).to_have_attribute("aria-label", "Put the player back")
    btn.click()
    assert pg.evaluate("said") == ["mini", "full"]
    sync_api.expect(pg.locator(".sidebar")).to_be_visible()
    # View › Mini Player in the menu bar, twice.
    pg.evaluate("stationApp.mini()")
    sync_api.expect(pg.locator("body")).to_have_class(re.compile("mini"))
    pg.evaluate("stationApp.mini()")
    assert pg.evaluate("said") == ["mini", "full", "mini", "full"]
    # The window putting itself back says so; the page needn't answer.
    pg.evaluate("stationApp.mini(true)")
    pg.evaluate("stationApp.mini(false)")
    assert pg.evaluate("said") == ["mini", "full", "mini", "full", "mini"]
    sync_api.expect(pg.locator(".sidebar")).to_be_visible()
    ctx.close()
    assert not errors, errors


def test_library_search(page):
    box = page.locator("#lib-search")
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "lib-search"
    box.type("thr")
    page.wait_for_function("() => S.view.type === 'search'")
    page.wait_for_selector("#view h1:has-text('Search: thr')")
    assert page.locator("#view tbody tr").count() == 1
    box.press("Enter")
    page.wait_for_function("() => document.activeElement.matches('#view tbody tr')")
    page.keyboard.press("Enter")
    page.wait_for_function("() => current() && current().name === 'Three'")
    assert "Search" in page.locator("#now-sub").inner_text()
    box.fill("")
    box.press("Escape")
    page.wait_for_function("() => S.view.type !== 'search'")


def test_volume_levelling_measures_and_evens_out_tracks(page, player):
    app, _, tmp = player
    open_settings(page, "playback")
    page.locator("input[data-key=level]").check()
    page.locator("#nav-folders .nav-item").first.click()
    page.locator("#view tbody tr").nth(3).dblclick()  # the quiet one
    page.wait_for_function("() => L.known.has(current().id)", timeout=15000)
    quiet = page.evaluate("L.known.get(current().id)")
    page.locator("#view tbody tr").nth(0).dblclick()
    page.wait_for_function("() => L.known.has(current().id)", timeout=15000)
    loud = page.evaluate("L.known.get(current().id)")
    # A sine at amplitude A measures about 20*log10(A) - 3.7 LUFS (K-weighted).
    assert loud["lufs"] - quiet["lufs"] == pytest.approx(20, abs=1.5)
    assert loud["peak"] == pytest.approx(0.5, abs=0.02)
    gains = page.evaluate("[...L.known.keys()].map(id => levelGain(id))")
    assert max(gains) / min(gains) > 5
    assert page.evaluate("L.master !== null && decks.every(d => d.volume === 1)")
    # Measurements are remembered by the server for next time (each is
    # sent in the background, so the second may still be on its way).
    deadline = time.monotonic() + 5
    while True:
        saved = json.loads((tmp / "home" / "loudness.json").read_text())
        if len(saved["entries"]) >= 2 or time.monotonic() > deadline:
            break
        time.sleep(0.05)
    assert len(saved["entries"]) >= 2


def test_new_version_notice(page, player):
    app, url, _ = player
    app.latest_version = "99.0.0"
    page.reload()
    note = page.locator("#update")
    note.wait_for(state="visible")
    assert "99.0.0" in note.inner_text()
    sync_api.expect(note.get_by_role("button", name="Update now")).to_be_visible()
    note.get_by_role("button", name="Not now").click()
    assert note.is_hidden()
    page.reload()
    page.wait_for_selector("#view tbody tr")
    assert note.is_hidden()  # dismissed for this version


def test_update_from_settings(page, player, monkeypatch):
    import station_player.server as server_mod
    from station_player import updates
    from station_player.selfupdate import Updater

    app = player[0]
    monkeypatch.setattr(updates, "_fetch_latest", lambda: "99.0.0")
    runs = []

    def run(cmd, **kwargs):
        runs.append(cmd)
        if "pip" in cmd:
            return subprocess.CompletedProcess(cmd, run.code, "", "ERROR: no network")
        return subprocess.CompletedProcess(cmd, 0, "99.0.0\n", "")

    def launch(cmd):
        # What starting the new version looks like from the page.
        monkeypatch.setattr(server_mod, "__version__", "99.0.0")

    run.code = 1
    app.updater = Updater(lambda: None, kind="pip", run=run, launch=launch)
    open_settings(page, "app")
    box = page.locator("#update-settings")
    sync_api.expect(box).to_contain_text(f"You have Station Player {server_mod.__version__}")
    box.get_by_role("button", name="Check for updates").click()
    sync_api.expect(box).to_contain_text("Station Player 99.0.0 is out")
    note = page.locator("#update")
    sync_api.expect(note).to_contain_text("Station Player 99.0.0 is out")
    assert "null" not in note.inner_text()  # nothing stray in the sidebar note
    # It didn't work: say why, and where to get it instead.
    box.get_by_role("button", name="Update to 99.0.0").click()
    sync_api.expect(box.locator(".update-failed")).to_contain_text("no network")
    assert box.get_by_role("link", name="Download it yourself").get_attribute("href").endswith("/player-v99.0.0")
    # It works: the page reloads into the new version.
    run.code = 0
    page.evaluate("window.notReloaded = true")
    box.get_by_role("button", name="Update to 99.0.0").click()
    page.wait_for_function("() => !window.notReloaded && S.lib && S.lib.version === '99.0.0'", timeout=15000)
    assert runs[-2][-1] == "station-player==99.0.0"


def test_choose_the_stations_on_the_tuner(page, player):
    app = player[0]
    app.store.config.stations = [{"name": f"Station {i}", "url": f"http://127.0.0.1:9/{i}"} for i in range(4)]
    page.reload()
    page.locator(".nav-item[data-view=radio]").click()
    dial = page.locator(".tuner")
    sync_api.expect(dial).to_contain_text("Station 1")
    open_settings(page, "radio")
    page.get_by_label("Station 1").uncheck()
    sync_api.expect(page.get_by_label("Station 1")).to_be_focused()
    assert app.store.config.stations[1]["tuner"] is False
    page.locator(".nav-item[data-view=radio]").click()
    sync_api.expect(dial).not_to_contain_text("Station 1")
    sync_api.expect(page.locator(".card", has_text="Station 1")).to_be_visible()  # its card stays
    # Fewer than two on it: no tuner at all.
    open_settings(page, "radio")
    page.get_by_label("Station 2").uncheck()
    page.get_by_label("Station 3").uncheck()
    page.locator(".nav-item[data-view=radio]").click()
    sync_api.expect(dial).to_have_count(0)
    open_settings(page, "radio")
    page.get_by_label("Station 1").check()
    sync_api.expect(page.get_by_label("Station 1")).to_be_focused()  # put back once it's saved
    for _ in range(50):  # the save may still be on its way
        if "tuner" not in app.store.config.stations[1]:
            break
        page.wait_for_timeout(100)
    assert "tuner" not in app.store.config.stations[1]
    # Saved settings keep it.
    from station_player.config import config_from_json
    assert config_from_json(app.store.config.to_json()).stations[2]["tuner"] is False


def test_home_and_discover(page, player):
    import json as _json
    from station_player.picks import Picks

    app = player[0]
    picks_feed = {"title": "From Rebirth", "picks": [
        {"title": "Rebirth Classics", "text": "Old skool all day.", "button": "Play the mix", "station": "http://127.0.0.1:9/classics"},
        {"title": "Never now", "station": "http://127.0.0.1:9/never", "ends": "2000-01-01"},
    ]}
    app.picks = Picks(player[2] / "picks", fetch=lambda url, limit: (_json.dumps(picks_feed).encode(), ""))
    app.picks.refresh()
    page.evaluate("HOME.fetched = 0")  # the window opened with the shipped picks
    page.locator(".nav-item[data-view=home]").click()
    sync_api.expect(page.locator("#view h1")).to_have_text(re.compile(r"^Good (morning|afternoon|evening|night)$"))
    picks = page.locator("#home-picks")
    sync_api.expect(picks.locator("h2")).to_have_text("From Rebirth")
    sync_api.expect(picks.locator(".pick")).to_have_count(1)  # the other one ended long ago
    sync_api.expect(page.locator(".home .tile", has_text="Test FM")).to_be_visible()
    # A pick plays straight away, and joins Recently played.
    picks.get_by_role("button", name="Play the mix: Rebirth Classics").click()
    page.wait_for_function("() => S.mode === 'radio' && S.station.url === 'http://127.0.0.1:9/classics'")
    page.wait_for_function("() => (S.lib.recent_stations || []).some(r => r.url === 'http://127.0.0.1:9/classics')")
    # Times of day, by this computer's clock (overnight too).
    assert page.evaluate("""() => [
      pickNow({from: "06:00", to: "11:00"}, new Date(2026, 0, 5, 7, 30)),
      pickNow({from: "06:00", to: "11:00"}, new Date(2026, 0, 5, 11, 0)),
      pickNow({from: "18:00", to: "06:00"}, new Date(2026, 0, 5, 2, 0)),
      pickNow({from: "18:00", to: "06:00"}, new Date(2026, 0, 5, 12, 0)),
      pickNow({days: ["sat", "sun"]}, new Date(2026, 0, 5, 12, 0)),
      pickNow({days: ["mon"]}, new Date(2026, 0, 5, 12, 0)),
      pickNow({starts: "2026-01-06"}, new Date(2026, 0, 5, 12, 0)),
    ]""") == [True, False, True, False, False, True, False]
    # Home's way into the globe: news and talk.
    page.get_by_role("button", name=re.compile("^News and talk around the world")).first.click()
    sync_api.expect(page.locator("#view h1")).to_have_text("Discover")
    sync_api.expect(page.locator(".nav-item[data-view=discover]")).to_have_attribute("aria-current", "true")
    sync_api.expect(page.locator("#globe")).to_be_focused()
    sync_api.expect(page.locator(".globe-kinds [data-kind=news]")).to_have_attribute("aria-pressed", "true")
    # And to sport.
    page.locator(".nav-item[data-view=home]").click()
    page.get_by_role("button", name=re.compile("^Sport around the world")).first.click()
    sync_api.expect(page.locator(".globe-kinds [data-kind=sport]")).to_have_attribute("aria-pressed", "true")
    # And to searching.
    page.get_by_role("button", name=re.compile("^Search for a station")).click()
    sync_api.expect(page.locator("#finder-q")).to_be_focused()
    # The Radio page points here.
    page.locator(".nav-item[data-view=radio]").click()
    page.get_by_role("button", name="Open Discover").click()
    sync_api.expect(page.locator("#view h1")).to_have_text("Discover")
    sync_api.expect(page.locator("#globe")).to_be_visible()  # back on the globe after searching
    # A reload stays put; a new window opens on the Radio page.
    page.reload()
    sync_api.expect(page.locator("#view h1")).to_have_text("Discover")
    other = page.context.new_page()
    other.goto(player[1])
    other.wait_for_function("() => S.view.type === 'radio'")
    other.close()
    # Or on Home, if Settings › App says so.
    open_settings(page, "app")
    page.get_by_label("Open on").select_option("home")
    other = page.context.new_page()
    other.goto(player[1])
    sync_api.expect(other.locator("#view .home")).to_be_visible()
    other.close()


def test_home_tiles_and_picks_pause_while_playing(page, player, tone_station):
    import json as _json
    from station_player.picks import Picks

    app = player[0]
    app.store.config.stations = [{"name": "Tone FM", "url": f"{tone_station}?1"}]
    feed = {"title": "From Rebirth", "picks": [{"title": "The mix", "button": "Play the mix", "station": f"{tone_station}?2"}]}
    app.picks = Picks(player[2] / "picks", fetch=lambda url, limit: (_json.dumps(feed).encode(), ""))
    app.picks.refresh()
    page.evaluate("HOME.fetched = 0; loadState()")
    page.locator(".nav-item[data-view=home]").click()
    # A station's tile: Listen, then Pause.
    tile = page.locator("#view .tile", has_text="Tone FM")
    sync_api.expect(tile).to_have_attribute("aria-label", "Listen to Tone FM")
    tile.click()
    page.wait_for_function("() => S.mode === 'radio' && S.station.name === 'Tone FM' && !audio.paused", timeout=10000)
    sync_api.expect(tile).to_have_attribute("aria-label", "Pause Tone FM")
    sync_api.expect(tile).to_have_class(re.compile(r"\blive\b"))
    tile.click()
    page.wait_for_function("() => audio.paused")
    sync_api.expect(tile).to_have_attribute("aria-label", "Listen to Tone FM")
    # A pick's button turns to Pause while its station plays.
    picks = page.locator("#home-picks")
    picks.get_by_role("button", name="Play the mix: The mix").click()
    page.wait_for_function("() => S.mode === 'radio' && S.station.url.endsWith('?2') && !audio.paused", timeout=10000)
    pause = picks.get_by_role("button", name="Pause: The mix")
    sync_api.expect(pause).to_have_text("Pause")
    pause.click()
    page.wait_for_function("() => audio.paused")
    sync_api.expect(picks.get_by_role("button", name="Play the mix: The mix")).to_have_text("Play the mix")


def test_seeking_back_near_the_end_doesnt_start_the_next_track_early(page):
    page.locator("#view tbody tr").nth(1).dblclick()  # "Two", 3 s
    page.wait_for_function("() => handoffTimer !== null", timeout=10000, polling=5)
    page.evaluate("audio.currentTime = 0.5")
    page.wait_for_timeout(400)
    assert page.evaluate("current().name") == "Two"
    assert page.evaluate("decks.filter(d => !d.paused).length") == 1
    page.wait_for_function("() => current().name === 'Three'", timeout=10000)
    page.wait_for_timeout(400)
    assert page.evaluate("decks.filter(d => !d.paused).length") == 1


def test_track_changes_leave_keyboard_focus_alone(page):
    page.locator("#view tbody tr").nth(0).dblclick()
    nav = page.locator("#nav-folders .nav-item").first
    nav.focus()
    page.wait_for_function("() => current() && current().name === 'Two'", timeout=10000)
    assert page.evaluate("document.activeElement === document.querySelector('#nav-folders .nav-item')")


def test_add_to_queue_while_on_the_radio_plays_that_track(page):
    rows = page.locator("#view tbody tr")
    rows.nth(0).dblclick()
    rows.nth(2).focus()
    page.keyboard.press("q")  # "Three" queued
    page.evaluate("playStation(S.lib.stations[0], { autoplay: false })")
    rows.nth(3).focus()
    page.keyboard.press("q")
    page.wait_for_function("() => S.mode === 'track' && current().name === 'Quiet'")
    assert page.evaluate("S.upNext.map(t => t.name)") == ["Three"]


def test_reordering_the_queue_keeps_focus(page):
    rows = page.locator("#view tbody tr")
    rows.nth(0).dblclick()
    for i in (2, 3):
        rows.nth(i).focus()
        page.keyboard.press("q")
    page.locator(".nav-item[data-view=upnext]").click()
    page.locator("button[aria-label='Move Quiet up']").focus()
    page.keyboard.press("Enter")
    assert page.evaluate("S.upNext.map(t => t.name)") == ["Quiet", "Three"]
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Move Quiet down"


# ---- 0.4.0: my playlists, crossfade, recording, own window ----------------

def answer_prompts(page, *answers):
    """Answer the next prompt() dialogs in turn; confirm() gets True."""
    queue = list(answers)
    seen = []

    def handle(dialog):
        seen.append(dialog.message)
        if dialog.type == "prompt":
            dialog.accept(queue.pop(0))
        else:
            dialog.accept()
    page.on("dialog", handle)
    return seen


def row_names(page):
    return page.locator("#view tbody tr td.title").all_inner_texts()


def open_menu(page, index):
    row = page.locator("#view tbody tr").nth(index)
    row.hover()
    row.locator(".row-more").click()


def test_make_and_edit_a_playlist(page):
    answer_prompts(page, "Road trip", "Evenings")
    page.locator("#btn-new-playlist").click()
    page.wait_for_selector("#view h1:has-text('Road trip')")
    assert "This playlist is empty" in page.locator("#view").inner_text()
    page.locator("#nav-folders .nav-item").first.click()
    page.wait_for_selector("#view tbody tr")
    for i in (1, 0, 2):
        open_menu(page, i)
        page.locator("#track-menu button", has_text="Add to playlist…").click()
        page.locator("#track-menu button", has_text="Road trip").click()
    page.locator("#nav-mine .nav-item", has_text="Road trip").click()
    page.wait_for_selector("#view h1:has-text('Road trip')")
    page.wait_for_function("() => document.querySelectorAll('#view tbody tr').length === 3")
    assert row_names(page) == ["Two", "One", "Three"]
    assert page.locator("#nav-mine .count").inner_text() == "3"

    # Alt+Up moves a track and keeps it focused; Delete removes one.
    page.locator("#view tbody tr").nth(1).focus()
    page.keyboard.press("Alt+ArrowUp")
    page.wait_for_function("() => document.querySelector('#view tbody tr td.title').textContent === 'One'")
    assert row_names(page) == ["One", "Two", "Three"]
    assert page.evaluate("document.activeElement.querySelector('td.title').textContent") == "One"
    page.keyboard.press("Delete")
    page.wait_for_function("() => document.querySelectorAll('#view tbody tr').length === 2")
    assert row_names(page) == ["Two", "Three"]

    # Drag "Three" above "Two".
    rows = page.locator("#view tbody tr")
    rows.nth(1).drag_to(rows.nth(0), target_position={"x": 20, "y": 2})
    page.wait_for_function("() => document.querySelector('#view tbody tr td.title').textContent === 'Three'")

    page.locator("#view button", has_text="Rename").click()
    page.wait_for_selector("#view h1:has-text('Evenings')")
    assert page.locator("#nav-mine .nav-item", has_text="Evenings").count() == 1

    href = page.locator("#view a", has_text="Export M3U").get_attribute("href")
    m3u = page.evaluate("async (h) => (await fetch(h)).text()", href)
    assert m3u.startswith("#EXTM3U\n#PLAYLIST:Evenings\n") and "03 Three.wav" in m3u

    page.locator("#view button", has_text="Delete").click()
    page.wait_for_function("() => !document.querySelector('#nav-mine .nav-item')")


def test_copy_a_playlist_and_save_the_queue(page):
    answer_prompts(page, "My copy", "Saved queue")
    page.locator("#view button", has_text="Copy to My playlists").click()
    page.wait_for_selector("#view h1:has-text('My copy')")
    assert row_names(page) == ["One", "Two", "Three", "Quiet"]
    rows = page.locator("#view tbody tr")
    rows.nth(1).dblclick()
    rows.nth(3).focus()
    page.keyboard.press("n")
    page.locator(".nav-item[data-view=upnext]").click()
    page.locator("#view button", has_text="Save as a playlist").click()
    page.wait_for_selector("#view h1:has-text('Saved queue')")
    assert row_names(page) == ["Two", "Quiet", "Three", "Quiet"]  # in play order


def test_missing_tracks_can_be_removed_from_my_playlists(page, player):
    app, _, tmp = player
    ids = [t["id"] for t in app.library.playlist_detail(app.library.playlist_summaries()[0]["id"])["tracks"]]
    app.create_playlist("Mix", ids[:2])
    (tmp / "Music" / "01 One.wav").unlink()
    app.rescan()
    page.reload()
    page.locator("#nav-mine .nav-item").first.click()
    page.wait_for_selector("#view tbody tr.missing")
    open_menu(page, 0)
    items = page.locator("#track-menu button").all_inner_texts()
    assert items == ["Move down", "Remove from this playlist"]
    page.locator("#track-menu button", has_text="Remove").click()
    page.wait_for_function("() => document.querySelectorAll('#view tbody tr').length === 1")
    assert row_names(page) == ["Two"]


def test_crossfade(page):
    page.evaluate("localStorage.setItem('sp.crossfade', '0.6')")
    page.reload()
    page.wait_for_selector("#view tbody tr")
    page.evaluate("""() => {
      window.fades = [];
      setInterval(() => { if (X.timer) fades.push([performance.now(), decks.map(d => d.paused ? null : d.volume)]); }, 20);
    }""")
    page.locator("#view tbody tr").first.dblclick()
    page.wait_for_function("() => current().name === 'Two' && X.timer === null && fades.length > 0", timeout=10000)
    fades = page.evaluate("fades")
    span = fades[-1][0] - fades[0][0]
    assert 350 < span < 900, span
    # Both tracks play during the fade, at less than full volume, the
    # new one rising and the old one falling.
    middle = [v for _, v in fades if None not in v and 0.05 < min(v)]
    assert middle, fades
    assert all(max(v) < 0.8 for v in middle)
    assert fades[0][1][0] == pytest.approx(0.8, abs=0.1) or fades[0][1][1] == pytest.approx(0.8, abs=0.1)
    page.wait_for_timeout(100)
    assert page.evaluate("decks.filter(d => !d.paused).length") == 1
    assert page.evaluate("audio.volume") == pytest.approx(0.8)


def test_pausing_during_a_crossfade_stops_both_tracks(page):
    page.evaluate("localStorage.setItem('sp.crossfade', '0.6')")
    page.reload()
    page.wait_for_selector("#view tbody tr")
    page.locator("#view tbody tr").first.dblclick()
    page.wait_for_function("() => X.timer !== null", timeout=10000, polling=10)
    page.locator("#btn-play").click()
    assert page.evaluate("decks.every(d => d.paused)")
    assert page.evaluate("X.timer === null && fadeMul.every(m => m === 1)")
    assert page.evaluate("current().name") == "Two"
    page.locator("#btn-play").click()
    page.wait_for_function("() => !audio.paused && audio.volume > 0.79")


def test_crossfade_setting(page):
    open_settings(page, "playback")
    slider = page.locator("#crossfade")
    slider.focus()
    for _ in range(4):
        page.keyboard.press("ArrowRight")
    assert page.locator("output[for=crossfade]").inner_text() == "4 s"
    assert page.evaluate("localStorage.getItem('sp.crossfade')") == "4"


def test_record_a_station(page, player):
    from station_player.recorder import Recorder
    from test_recorder import FakeStream, Opener

    app, _, tmp = player
    # A station sending a real (WAV) sound, so the recording plays back.
    sound = tone(tmp / "station.wav", 2.0, 0.3).read_bytes()
    app.recorder = Recorder(tmp / "rec", opener=Opener(FakeStream(sound, ctype="audio/wav")))
    page.locator(".nav-item[data-view=radio]").click()
    card = page.locator(".card", has_text="Test FM")
    card.locator(".rec-btn").click()
    page.wait_for_function("() => document.querySelector('#rec-status').textContent.includes('Recording Test FM')")
    page.wait_for_function("() => document.querySelector('.card .rec').textContent.includes('MB')")
    assert card.locator(".rec-btn").inner_text() == "Stop recording"
    seen = []
    page.once("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    page.locator("#btn-quit").click()
    assert "recording" in seen[0]
    card.locator(".rec-btn").click()
    page.wait_for_function("() => document.querySelector('.card .rec').textContent.startsWith('Saved Test FM')")
    assert page.locator("#rec-status").is_hidden()
    assert len(list((tmp / "rec").glob("Test FM *.wav"))) == 1
    # The buttons stay inside the card, even at its narrowest.
    page.evaluate("document.querySelector('.cards').style.gridTemplateColumns = 'repeat(auto-fill, 220px)'")
    assert page.evaluate("""() => [...document.querySelectorAll('.card')].every(c => {
      const box = c.getBoundingClientRect();
      return [...c.querySelectorAll('button')].every(b => b.getBoundingClientRect().right <= box.right + 0.5);
    })""")
    # Play it: the recordings folder joins the music folders.
    card.locator(".rec-play").click()
    page.wait_for_function("() => S.mode === 'track' && current().name.startsWith('Test FM') && !audio.paused")
    assert app.store.config.music_folders[-1] == str((tmp / "rec").resolve())
    assert page.locator("#view h1").inner_text() == "rec"


def test_own_window_setting(page, player):
    app, _, _ = player
    app.window_browser = None  # the page loaded with whatever this machine has
    page.reload()
    page.wait_for_selector("#view")
    open_settings(page, "app")
    assert page.locator("input[data-key=window]").count() == 0
    assert "Microsoft Edge" in page.locator("#view").inner_text()
    app.window_browser = "Google Chrome"
    page.reload()
    page.wait_for_selector("#view")
    open_settings(page, "app")
    box = page.locator("input[data-key=window]")
    assert box.is_checked()
    box.uncheck()
    page.wait_for_function("() => S.lib.config.own_window === false")
    assert app.store.config.own_window is False


def test_saving_the_queue_with_repeat_all_lists_each_track_once(page):
    answer_prompts(page, "Loop")
    page.locator("#btn-repeat").click()  # repeat all
    page.locator("#view tbody tr").nth(2).dblclick()
    page.wait_for_function("() => current() && current().name === 'Three'")
    page.locator(".nav-item[data-view=upnext]").click()
    page.locator("#view button", has_text="Save as a playlist").click()
    page.wait_for_selector("#view h1:has-text('Loop')")
    assert row_names(page) == ["Three", "Quiet", "One", "Two"]


def test_open_with_plays_the_file(page, player):
    app, _, tmp = player
    song = tone(tmp / "Downloads" / "Opened Song.wav", 3.0, 0.4)
    app.open_files([str(song)])
    page.wait_for_function("() => S.mode === 'track' && current().name === 'Opened Song' && !audio.paused",
                           timeout=5000)
    assert page.locator("#view h1").inner_text() == "Opened files"
    assert page.locator("#nav-folders .nav-item", has_text="Opened files").count() == 1
    # Reopening the player later doesn't play it again.
    page.locator("#btn-play").click()
    page.reload()
    page.wait_for_selector("#view tbody tr")
    page.wait_for_timeout(2000)
    assert page.evaluate("audio.paused")


def test_a_window_opened_for_a_file_plays_it(browser, player):
    app, url, tmp = player
    song = tone(tmp / "Downloads" / "Fresh.wav", 3.0, 0.4)
    app.open_files([str(song)])
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(url)
    pg.wait_for_function("() => S.mode === 'track' && current().name === 'Fresh'", timeout=10000)
    ctx.close()


def test_install_as_an_app(page):
    open_settings(page, "app")
    assert page.locator("#btn-install").count() == 0
    page.evaluate("""() => {
      const e = new Event("beforeinstallprompt", { cancelable: true });
      e.prompt = () => { window.prompted = true; };
      e.userChoice = Promise.resolve({ outcome: "accepted" });
      dispatchEvent(e);
    }""")
    page.locator("#btn-install").click()
    assert page.evaluate("window.prompted === true")
    page.wait_for_function("() => !document.querySelector('#btn-install')")
    manifest = page.evaluate("async () => (await fetch('/manifest.webmanifest')).json()")
    assert manifest["name"] == "Station Player" and manifest["display"] == "standalone"
    assert {i["sizes"] for i in manifest["icons"]} == {"192x192", "512x512"}


def test_window_opened_while_the_player_is_off_starts_it_and_waits(browser, tmp_path):
    store = ConfigStore(tmp_path / "home")
    server = make_server(PlayerApp(store), port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ctx = browser.new_context()
    try:
        pg = ctx.new_page()
        pg.goto(f"http://127.0.0.1:{port}/")
        pg.evaluate("navigator.serviceWorker.ready.then(() => true)")
        server.shutdown()
        server.server_close()
        # Opened from the Dock with the player off: a page that starts it.
        pg.goto(f"http://127.0.0.1:{port}/", wait_until="commit")
        sync_api.expect(pg.locator("#offline-status")).to_contain_text("Starting Station Player")
        # The player comes back on the same address: the window opens it.
        server = make_server(PlayerApp(store), port=port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        pg.wait_for_selector(".sidebar", timeout=10000)
        assert pg.url == f"http://127.0.0.1:{port}/"
    finally:
        ctx.close()
        server.shutdown()
        server.server_close()


def test_plays_are_logged_for_the_play_log(page, player):
    from datetime import date
    app = player[0]
    page.locator("#view tbody tr").first.dblclick()
    page.wait_for_function("() => current() && current().name === 'Two'", timeout=10000)
    today = date.today()
    deadline = time.monotonic() + 5
    while not app.play_log.entries(today, today) and time.monotonic() < deadline:
        time.sleep(0.1)
    [entry] = app.play_log.entries(today, today)
    assert entry["title"] == "One" and 1 <= entry["seconds"] <= 3


def test_sleep_timer(page):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # After this track: it plays to the end, then the next one waits, paused.
    page.locator("#sleep").select_option("track")
    page.locator("#view tbody tr").first.dblclick()
    page.wait_for_function("() => current().name === 'Two' && audio.paused && !SLEEP.track", timeout=10000)
    assert page.evaluate("decks.every(d => d.paused)")
    assert page.locator("#sleep").input_value() == ""
    # In a number of minutes (here a second and a half): fades, then stops.
    page.locator("#btn-play").click()
    page.evaluate("setSleep(0.025)")
    assert page.locator("#sleep").input_value() == "on"
    assert "Sleep in 1 min" in page.locator("#sleep option[value=on]").inner_text()
    page.wait_for_function("() => audio.paused && SLEEP.at === 0", timeout=5000)
    assert page.evaluate("volMul.sleep") == 1 and page.evaluate("audio.volume") == pytest.approx(0.8)
    assert not errors


def test_alarm_plays_and_fades_in(page, player):
    app = player[0]
    ring(app)
    page.wait_for_function("() => !audio.paused && S.mode === 'track'", timeout=5000)
    assert page.evaluate("ALARM.timer !== null && volMul.alarm < 0.2")
    # A sleep timer set meanwhile (a snooze) doesn't undo the fade-in.
    page.evaluate("setSleep(15)")
    page.wait_for_timeout(600)
    assert page.evaluate("audio.volume") < 0.2
    assert page.evaluate("audio.volume") < 0.2
    # It plays once, not again on the next check.
    page.evaluate("pauseAll()")
    page.wait_for_timeout(1500)
    assert page.evaluate("audio.paused")


def test_phone_remote_commands_reach_the_page(page, player):
    app = player[0]
    app.store.config.remote = True
    page.evaluate("loadState()")
    pid = next(iter(app.library.playlists))
    app.remote_command({"cmd": "playlist", "id": pid})
    page.wait_for_function("() => !audio.paused && S.mode === 'track'", timeout=5000)
    app.remote_command({"cmd": "volume", "value": 0.25})
    page.wait_for_function("() => Number($('#volume').value) === 0.25", timeout=5000)
    deadline = time.monotonic() + 5
    while app.player_status.get("volume") != 0.25 and time.monotonic() < deadline:
        time.sleep(0.1)
    assert app.player_status["playing"] is True and app.player_status["title"]
    app.remote_command({"cmd": "toggle"})
    page.wait_for_function("() => audio.paused", timeout=5000)
    app.remote_command({"cmd": "sleep", "minutes": 30})
    page.wait_for_function("() => SLEEP.at > 0", timeout=5000)


def test_play_log_and_remote_settings(page, player):
    app = player[0]
    app.store.config.remote_port = 0
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    open_settings(page, "log")
    href = page.locator("#log-download").get_attribute("href")
    assert href.startswith("/api/playlog.csv?from=") and "&to=" in href
    page.locator("#tab-remote").click()
    page.locator("input[data-key=remote]").check()
    page.wait_for_function("() => S.lib.remote && S.lib.remote.on")
    if page.evaluate("S.lib.remote.url"):  # the test machine is on a network
        page.wait_for_function("() => { const i = document.querySelector('.remote-box img'); return i && i.naturalWidth > 0; }")
        old = app.store.config.remote_token
        page.locator("#btn-new-link").click()
        page.wait_for_function(f"() => !S.lib.remote.url.includes({old!r})")
    page.locator("input[data-key=remote]").uncheck()
    page.wait_for_function("() => !S.lib.remote.on")
    assert app.remote_server is None
    assert not errors


class FakePicker:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def pick(self, kind):
        self.asked.append(kind)
        return self.answer


def test_welcome_screen_adds_music(browser, tmp_path):
    home = tmp_path / "user"
    tone(home / "Music" / "Found.wav", 1.0, 0.3)
    other = tone(tmp_path / "Elsewhere" / "Chosen.wav", 1.0, 0.3).parent
    store = ConfigStore(tmp_path / "conf")
    app = PlayerApp(store)
    app.suggest_home = home
    app.picker = FakePicker({"path": str(other)})
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ctx = browser.new_context()
    try:
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{server.server_address[1]}/")
        # Nothing set up yet: the welcome screen, with the Music folder it found.
        sync_api.expect(pg.locator("#view h1")).to_have_text("Welcome to Station Player")
        found = pg.locator(".suggestion", has_text="Music folder")
        sync_api.expect(found).to_contain_text("1 song")
        found.get_by_role("button").click()
        sync_api.expect(found.locator(".added-mark")).to_be_visible()
        sync_api.expect(pg.locator(".toast").first).to_contain_text("Added")
        assert store.config.music_folders == [str((home / "Music").resolve())]
        # The computer's own folder chooser adds another.
        pg.locator("#add-folder").click()
        pg.wait_for_function("() => (S.lib.sources || []).length === 2")
        assert app.picker.asked == ["folder"]
        pg.locator("#btn-start-listening").click()
        pg.wait_for_selector("#view tbody tr")
        assert not errors
    finally:
        ctx.close()
        server.shutdown()
        server.server_close()


def test_music_settings_page(page, player):
    app, _, tmp = player
    app.picker = FakePicker({"cancelled": True})
    open_settings(page, "music")
    card = page.locator(".source", has_text="Music")
    sync_api.expect(card).to_contain_text("4 tracks")
    # Arrow keys move between the settings pages.
    page.locator("#tab-music").focus()
    page.keyboard.press("ArrowRight")
    sync_api.expect(page.locator("#tab-playback")).to_have_attribute("aria-selected", "true")
    page.keyboard.press("ArrowLeft")
    sync_api.expect(page.locator("#tab-music")).to_be_focused()
    # Cancelling the chooser changes nothing.
    page.locator("#add-playlists").click()
    page.wait_for_timeout(300)
    assert app.picker.asked == ["file"] and len(app.store.config.music_folders) == 1
    # A computer with no chooser offers the path box instead.
    import station_player.setup as setup

    class NoPicker:
        def pick(self, kind):
            raise setup.PickerUnavailable("No folder window on this computer")
    app.picker = NoPicker()
    page.locator("#add-folder").click()
    sync_api.expect(page.locator("details.typed")).to_have_attribute("open", "")
    sync_api.expect(page.locator(".toast.error")).to_contain_text("type the path")
    # Removing a folder leaves its files alone.
    page.locator(".source", has_text="Music").get_by_role("button", name="Remove").click()
    page.wait_for_function("() => S.lib.sources.length === 0")
    assert (tmp / "Music" / "01 One.wav").exists()


def test_edit_test_and_move_stations(page, player):
    app = player[0]
    app.store.config.stations.append({"name": "Second FM", "url": "http://127.0.0.1:9/two"})
    page.evaluate("loadState()")
    app.test_station = lambda url: {"ok": True, "type": "audio/mpeg", "name": "Tested FM", "url": url}
    page.locator(".nav-item[data-view=radio]").click()
    page.get_by_role("button", name="Edit Test FM").click()
    card = page.locator(".card", has=page.locator("form.edit"))
    card.get_by_role("button", name="Test", exact=True).click()
    sync_api.expect(card.locator(".test-result")).to_contain_text("It works: Tested FM")
    card.get_by_label("Name").fill("Renamed FM")
    card.get_by_role("button", name="Save", exact=True).click()
    sync_api.expect(page.locator(".toast").last).to_contain_text("Saved Renamed FM")
    assert app.store.config.stations[0]["name"] == "Renamed FM"
    page.get_by_role("button", name="Edit Renamed FM").click()
    page.get_by_role("button", name="Move Renamed FM down").click()
    page.wait_for_function("() => S.lib.stations[1].name === 'Renamed FM'")
    assert [s["name"] for s in app.store.config.stations] == ["Second FM", "Renamed FM"]
    sync_api.expect(page.get_by_role("button", name="Move Renamed FM up")).to_be_focused()
    page.keyboard.press("Escape")
    sync_api.expect(page.locator("form.edit")).to_have_count(0)
    sync_api.expect(page.get_by_role("button", name="Edit Renamed FM")).to_be_focused()


def test_recordings_page(page, player):
    from station_player.recorder import Recorder
    from test_recorder import FakeStream, Opener

    app, _, tmp = player
    sound = tone(tmp / "station.wav", 2.0, 0.3).read_bytes()
    app.recorder = Recorder(tmp / "rec", opener=Opener(FakeStream(sound, ctype="audio/wav")))
    (tmp / "rec").mkdir()
    tone(tmp / "rec" / "Old FM 2026-10-01 07.30.wav", 1.0, 0.3)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.locator(".nav-item[data-view=recordings]").click()
    row = page.locator(".recording", has_text="Old FM")
    sync_api.expect(row.locator(".name")).to_contain_text("Old FM ·")
    row.get_by_role("button", name="Edit").click()
    sync_api.expect(page.locator("#rec-label-0")).to_be_focused()
    page.locator("#rec-label-0").fill("Breakfast show")
    page.locator("#rec-tags-0").fill("interview, live")
    page.get_by_role("button", name="Save", exact=True).click()
    row = page.locator(".recording", has_text="Breakfast show")
    sync_api.expect(row.locator(".tag")).to_have_count(2)
    sync_api.expect(page.get_by_role("button", name="Edit Breakfast show")).to_be_focused()
    # Tags filter the list; so does the search box.
    page.locator(".tag-filter .chip", has_text="interview").click()
    sync_api.expect(page.locator(".recording")).to_have_count(1)
    page.locator("#rec-search").fill("nothing like this")
    sync_api.expect(page.locator(".recording")).to_have_count(0)
    sync_api.expect(page.locator("#rec-search")).to_be_focused()
    page.locator("#rec-search").fill("")
    # Plays without leaving the page, under its label.
    row.get_by_role("button", name="Play Breakfast show").click()
    page.wait_for_function("() => S.mode === 'track' && current().name === 'Breakfast show' && !audio.paused")
    assert page.evaluate("S.view.type") == "recordings"
    assert "Old FM" in page.locator("#now-sub").inner_text()
    assert not errors


def test_clock_page_and_alarms(page, player):
    app = player[0]
    page.on("dialog", lambda d: d.accept())
    page.locator(".nav-item[data-view=clock]").click()
    sync_api.expect(page.locator("#view h1")).to_have_text("Clock")
    sync_api.expect(page.locator(".clock-hero .digits .hm")).not_to_be_empty()
    sync_api.expect(page.locator(".next-alarm")).to_have_text("No alarms on")
    # The clock face, remembered next time.
    page.get_by_role("button", name="Clock face").click()
    sync_api.expect(page.locator(".clock-hero svg.analogue")).to_be_visible()
    page.wait_for_function("() => document.querySelector('[data-hand=m]').getAttribute('transform')")
    assert page.evaluate("store('clock-face')") == "analogue"
    page.get_by_role("button", name="Digits").click()
    # Add an alarm.
    page.locator("#btn-add-alarm").click()
    sync_api.expect(page.locator("#alarm-time")).to_be_focused()
    page.locator("#alarm-time").fill("06:45")
    page.locator("#alarm-label").fill("Work")
    page.get_by_role("button", name="Set alarm").click()
    card = page.locator(".alarm-card", has_text="Work")
    sync_api.expect(card.locator(".alarm-time")).to_have_text("06:45")
    sync_api.expect(card.locator(".facts")).to_contain_text("Weekdays")
    sync_api.expect(card.locator(".alarm-edit")).to_be_focused()
    (a,) = app.store.config.alarms
    assert (a["time"], a["label"], a["days"], a["on"], a["source"]["kind"]) == ("06:45", "Work", [0, 1, 2, 3, 4], True, "playlist")
    sync_api.expect(page.locator(".next-alarm")).to_contain_text(re.compile(r"Next alarm: \w+ 0?6:45.* · Work"))
    sync_api.expect(page.locator("#nav-next-alarm")).to_contain_text("6:45")
    # A second one, at weekends, playing the radio.
    page.locator("#btn-add-alarm").click()
    page.locator("#alarm-time").fill("09:30")
    page.locator(".alarm-form .days label", has_text="Mon").click()  # untick the weekdays
    for day in ["Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        page.locator(".alarm-form .days label", has_text=day).click()
    page.locator("#alarm-source").select_option(label="Test FM")
    page.get_by_role("button", name="Set alarm").click()
    sync_api.expect(page.locator(".alarm-card")).to_have_count(2)
    weekend = page.locator(".alarm-card", has_text="09:30")
    sync_api.expect(weekend.locator(".facts")).to_contain_text("Weekends · Test FM")
    # Turn the first off: the switch keeps focus.
    switch = card.locator("input[type=checkbox]")
    switch.uncheck()
    page.wait_for_function("() => !S.lib.config.alarms[0].on")
    sync_api.expect(page.locator(f"#view input[data-key=alarm-{a['id']}]")).to_be_focused()
    assert app.store.config.alarms[0]["on"] is False
    # Edit the time; Escape cancels an edit.
    weekend.locator(".alarm-edit").click()
    page.keyboard.press("Escape")
    sync_api.expect(page.locator(".alarm-form")).to_have_count(0)
    weekend.locator(".alarm-edit").click()
    page.locator("#alarm-time").fill("10:15")
    page.get_by_role("button", name="Save alarm").click()
    sync_api.expect(page.locator(".alarm-card", has_text="10:15")).to_have_count(1)
    assert app.store.config.alarms[1]["time"] == "10:15"
    # Delete it.
    page.locator(".alarm-card", has_text="10:15").locator(".alarm-edit").click()
    page.get_by_role("button", name="Delete").click()
    sync_api.expect(page.locator(".alarm-card")).to_have_count(1)
    assert len(app.store.config.alarms) == 1
    # Settings points here now.
    open_settings(page, "alarm")
    page.locator("#btn-open-clock").click()
    sync_api.expect(page.locator("#view h1")).to_have_text("Clock")


def test_one_off_alarm_and_radio_schedule(page, player):
    app = player[0]
    page.locator(".nav-item[data-view=clock]").click()
    page.locator("#btn-add-alarm").click()
    page.locator("#alarm-time").fill("23:59")
    sync_api.expect(page.locator("#alarm-date")).to_be_hidden()
    page.locator("#alarm-repeat").select_option("once")
    sync_api.expect(page.locator(".alarm-form .days")).to_be_hidden()
    page.locator("#alarm-date").fill("2099-01-02")
    page.get_by_role("button", name="Set alarm").click()
    sync_api.expect(page.locator(".alarm-card .facts")).to_contain_text("Once, ")
    (a,) = app.store.config.alarms
    assert (a["date"], a["days"], a["action"]) == ("2099-01-02", [], "alarm")
    # A one-off in the past isn't allowed.
    page.locator(".alarm-card .alarm-edit").click()
    page.locator("#alarm-date").fill("2001-01-01")
    page.get_by_role("button", name="Save alarm").click()
    sync_api.expect(page.locator(".alarm-form .form-msg")).to_have_text("That time has already gone by")
    page.keyboard.press("Escape")

    # The radio page's schedule: switches, kept apart from the alarms.
    page.locator(".nav-item[data-view=radio]").click()
    page.locator("#btn-add-switch").click()
    page.locator("#alarm-time").fill("17:00")
    page.locator("#alarm-label").fill("The news")
    page.locator("#alarm-source").select_option(label="Test FM")
    page.get_by_role("button", name="Add to schedule").click()
    sync_api.expect(page.locator(".schedule .alarm-card .name")).to_have_text("The news")
    sync_api.expect(page.locator(".schedule .alarm-card .facts")).to_have_text("Weekdays · Test FM")
    switch = app.store.config.alarms[1]
    assert (switch["action"], switch["time"], switch["source"]["kind"]) == ("switch", "17:00", "station")
    page.locator(".nav-item[data-view=clock]").click()
    sync_api.expect(page.locator(".alarm-card")).to_have_count(1)  # the switch isn't an alarm

    # A switch only changes what's already playing.
    station = page.evaluate("S.lib.stations[0].id")
    command = {"cmd": "switch", "kind": "station", "id": station, "name": "Test FM", "label": "The news"}
    app.remote_command(command)
    page.wait_for_timeout(2500)
    assert page.evaluate("S.mode") == "idle"
    pid = next(iter(app.library.playlists))
    app.remote_command({"cmd": "playlist", "id": pid})
    page.wait_for_function("() => !audio.paused && S.mode === 'track'", timeout=5000)
    app.remote_command(command)
    page.wait_for_function("() => S.mode === 'radio' && S.station.name === 'Test FM'", timeout=5000)
    sync_api.expect(page.locator(".toast").last).to_contain_text("Schedule: The news, switched to Test FM")


def ring(app, label="Wake up"):
    """Set an alarm off now, as the server does at its time."""
    from datetime import datetime
    now = datetime.now()
    pid = next(iter(app.library.playlists))
    app.store.config.alarms = [{"id": "0000000a", "on": True, "time": now.strftime("%H:%M"), "days": [now.weekday()],
                                "label": label, "source": {"kind": "playlist", "id": pid, "name": "Music"}}]
    app._alarm_fired = {}
    assert app.check_alarm(now)


def test_alarm_screen_snooze_and_stop(page, player):
    app = player[0]
    ring(app)
    screen = page.locator("#alarm-screen")
    sync_api.expect(screen).to_be_visible(timeout=5000)
    sync_api.expect(screen.locator("h2")).to_have_text("Wake up")
    sync_api.expect(screen).to_contain_text("Playing Music")
    sync_api.expect(page.locator("#btn-snooze")).to_be_focused()
    page.wait_for_function("() => !audio.paused")
    # Tab stays on the alarm screen.
    for _ in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('alarm-screen').contains(document.activeElement)")
    page.locator("#btn-snooze").click()
    sync_api.expect(screen).to_be_hidden()
    page.wait_for_function("() => audio.paused")
    assert app.alarm_state()["snoozed_until"] > time.time() + 8 * 60
    sync_api.expect(page.locator(".toast").last).to_contain_text("Snoozed until")
    page.locator(".nav-item[data-view=clock]").click()
    sync_api.expect(page.locator(".next-alarm")).to_contain_text("Snoozed until")
    page.locator("#btn-cancel-snooze").click()
    page.wait_for_function("() => !S.lib.alarm_state.snoozed_until")
    assert app.alarm_state()["snoozed_until"] is None
    # Ringing again: Stop.
    ring(app, "Second")
    sync_api.expect(screen.locator("h2")).to_have_text("Second", timeout=5000)
    page.locator("#btn-stop-alarm").click()
    sync_api.expect(screen).to_be_hidden()
    page.wait_for_function("() => audio.paused && ALARM.timer === null && volMul.alarm === 1")
    assert app.alarm_state()["ringing"] is None
    # Keep playing: the alarm goes, the music doesn't.
    ring(app, "Third")
    sync_api.expect(screen.locator("h2")).to_have_text("Third", timeout=5000)
    page.wait_for_function("() => !audio.paused")
    page.keyboard.press("Escape")
    sync_api.expect(screen).to_be_hidden()
    page.wait_for_timeout(300)
    assert page.evaluate("!audio.paused && volMul.alarm === 1")
    assert app.alarm_state()["ringing"] is None


def test_snooze_from_the_phone(page, player):
    app = player[0]
    app.store.config.remote = True
    page.evaluate("loadState()")
    ring(app)
    sync_api.expect(page.locator("#alarm-screen")).to_be_visible(timeout=5000)
    app.remote_command({"cmd": "snooze"})
    sync_api.expect(page.locator("#alarm-screen")).to_be_hidden(timeout=5000)
    page.wait_for_function("() => audio.paused")
    assert app.alarm_state()["snoozed_until"]
    ring(app)
    sync_api.expect(page.locator("#alarm-screen")).to_be_visible(timeout=5000)
    app.remote_command({"cmd": "stop_alarm"})
    sync_api.expect(page.locator("#alarm-screen")).to_be_hidden(timeout=5000)
    assert app.alarm_state() == {"ringing": None, "snoozed_until": None}


def test_an_alarm_nobody_answers_stops_after_half_an_hour(page, player):
    app = player[0]
    ring(app)
    sync_api.expect(page.locator("#alarm-screen")).to_be_visible(timeout=5000)
    page.wait_for_function("() => !audio.paused")
    with app._lock:
        app._ringing["at"] -= app.RING_FOR + 1
    sync_api.expect(page.locator("#alarm-screen")).to_be_hidden(timeout=5000)
    page.wait_for_function("() => audio.paused && ALARM.seq === null")


def test_an_alarm_stopped_before_the_page_sees_it_stays_quiet(page, player):
    app = player[0]
    page.evaluate("checkInbox = async () => {}")  # hold the page's next look
    ring(app)
    app.stop_alarm()
    page.evaluate("alarmGoesOff(" + json.dumps(app.inbox()["alarm"]) + ", 0)")
    page.wait_for_timeout(500)
    assert page.evaluate("audio.paused && document.getElementById('alarm-screen').hidden")


def test_night_mode(page):
    page.locator(".nav-item[data-view=clock]").click()
    page.locator("#btn-night").click()
    night = page.locator("#night")
    sync_api.expect(night).to_be_visible()
    sync_api.expect(night.locator(".digits .hm")).not_to_be_empty()
    page.keyboard.press("Space")
    sync_api.expect(night).to_be_hidden()
    sync_api.expect(page.locator("#btn-night")).to_be_focused()


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201a0b2e1a10000000049454e44ae426082")


class FakeDirectoryNet:
    def __init__(self):
        self.asked = []

    def __call__(self, url, user_agent, limit, **kw):
        self.asked.append(url)
        if url.startswith("https://logo.example/"):
            return PNG, "image/png"
        if url == "https://somafm.com/channels.json":
            return json.dumps({"channels": [{"id": "groovesalad", "title": "Groove Salad", "genre": "ambient",
                                             "description": "Ambient beats", "listeners": "9",
                                             "playlists": [{"format": "mp3", "url": "x"}]}]}).encode(), "application/json"
        if "/json/stations/byurl" in url and getattr(self, "byurl", False):
            return json.dumps([{"name": "Found", "url_resolved": "http://127.0.0.1:9/stream",
                                "favicon": "https://logo.example/found.png"}]).encode(), "application/json"
        if "/json/url/" in url or "/json/stations/byurl" in url:
            return b"[]", "application/json"
        jazz = "tag=jazz" in url
        items = [
            {"stationuuid": "11111111-1111", "name": "Jazz Cafe Radio", "url_resolved": "http://127.0.0.1:9/jazz",
             "favicon": "https://logo.example/jazz.png", "tags": "jazz,smooth", "country": "United Kingdom",
             "countrycode": "GB", "codec": "MP3", "bitrate": 128, "votes": 50},
        ] + ([] if jazz else [
            {"stationuuid": "22222222-2222", "name": "World News", "url_resolved": "http://127.0.0.1:9/news",
             "favicon": "", "tags": "news", "country": "Ireland", "codec": "AAC", "bitrate": 64, "votes": 40},
        ])
        return json.dumps(items).encode(), "application/json"


def test_find_stations(page, player):
    app = player[0]
    net = FakeDirectoryNet()
    app.directory = Directory(fetch=net, servers=["api.example"], public=lambda host: True)
    page.locator(".nav-item[data-view=discover]").click()
    sync_api.expect(page.locator("#globe")).to_be_visible()  # Discover opens on the globe
    page.get_by_role("button", name="List", exact=True).click()
    # Opens on the most popular stations, with genres to browse.
    results = page.locator("#finder-results .find-card")
    sync_api.expect(results).to_have_count(2)
    sync_api.expect(page.locator("#finder-results")).to_contain_text("The most popular stations in United States")
    assert "countrycode=US" in net.asked[0]  # this computer's country (en-US here)
    page.locator("#finder-country").select_option("")
    sync_api.expect(page.locator("#finder-results")).to_contain_text("The most popular stations right now")
    jazz = results.filter(has_text="Jazz Cafe Radio")
    sync_api.expect(jazz.locator(".facts")).to_have_text("United Kingdom · MP3 128 kbps")
    page.wait_for_function("() => { const i = document.querySelector('.find-card img.logo'); return i && i.naturalWidth === 1; }")
    sync_api.expect(results.filter(has_text="World News").locator(".glyph")).to_be_visible()
    sync_api.expect(page.locator("#finder-tags .chip", has_text="drum and bass")).to_be_visible()
    page.locator("#finder-tags .chip", has_text="jazz").click()
    sync_api.expect(results).to_have_count(1)
    assert "tag=jazz" in net.asked[-1]
    sync_api.expect(page.locator("#finder-tags .chip", has_text="jazz")).to_have_attribute("aria-pressed", "true")
    # SomaFM's own channels.
    page.locator("#finder-tags .chip.somafm").click()
    sync_api.expect(results).to_have_count(1)
    sync_api.expect(results.locator(".name")).to_have_text("SomaFM Groove Salad")
    sync_api.expect(page.locator("#finder-tags .chip", has_text="jazz")).to_have_attribute("aria-pressed", "false")
    page.locator("#finder-tags .chip", has_text="jazz").click()
    sync_api.expect(results).to_have_count(1)
    sync_api.expect(results.locator(".name")).to_have_text("Jazz Cafe Radio")
    # A search by name and country.
    page.locator("#finder-q").fill("cafe")
    page.locator("#finder-country").select_option("GB")
    page.locator("#finder-q").press("Enter")
    sync_api.expect(page.locator("#finder-results")).to_contain_text("“cafe”")
    asked = net.asked[-1]
    assert "name=cafe" in asked and "countrycode=GB" in asked
    # Listen before adding.
    jazz.get_by_role("button", name="Listen to Jazz Cafe Radio").click()
    page.wait_for_function("() => S.mode === 'radio' && S.station.url === 'http://127.0.0.1:9/jazz'")
    assert page.evaluate("S.station.name") == "Jazz Cafe Radio"
    deadline = time.monotonic() + 5
    while not any("/json/url/11111111-1111" in u for u in net.asked) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert any("/json/url/11111111-1111" in u for u in net.asked)
    # Add it to your stations.
    jazz.get_by_role("button", name="Add Jazz Cafe Radio to your stations").click()
    sync_api.expect(jazz.get_by_role("button", name="Jazz Cafe Radio is in your stations")).to_be_disabled()
    assert any(s["url"] == "http://127.0.0.1:9/jazz" and s["name"] == "Jazz Cafe Radio" for s in app.store.config.stations)
    sync_api.expect(page.locator("#finder-q")).to_have_value("cafe")  # still there after the redraw


def test_find_stations_offline(page):
    page.locator(".nav-item[data-view=discover]").click()
    page.get_by_role("button", name="List", exact=True).click()
    sync_api.expect(page.locator("#finder-results .errors")).to_contain_text("Couldn't reach the station directory")
    sync_api.expect(page.locator("#finder-tags .chip").first).to_have_text("SomaFM")


class ManyStationsNet(FakeDirectoryNet):
    """100 stations, paged; the first has a logo, the second a low bitrate."""

    def __call__(self, url, user_agent, limit):
        if "/json/stations/search" not in url:
            return super().__call__(url, user_agent, limit)
        self.asked.append(url)
        q = parse_qs(urlparse(url).query)
        start, count = int(q.get("offset", ["0"])[0]), int(q["limit"][0])
        items = [{"stationuuid": f"uuid-{n}", "name": f"Station {n}", "url_resolved": f"http://127.0.0.1:9/s{n}",
                  "favicon": "https://logo.example/s.png" if n == 0 else "", "tags": "pop", "country": "Ireland",
                  "countrycode": "IE", "codec": "UNKNOWN" if n == 2 else "MP3" if n == 3 else "AAC",
                  "bitrate": {1: 32, 2: 0, 3: 56}.get(n, 128), "votes": 100 - n}
                 for n in range(start, min(100, start + count))]
        return json.dumps(items).encode(), "application/json"


def test_logos_recently_played_and_more_results(page, player):
    app = player[0]
    net = ManyStationsNet()
    app.directory = Directory(fetch=net, servers=["api.example"], public=lambda host: True)
    page.locator(".nav-item[data-view=discover]").click()
    page.get_by_role("button", name="List", exact=True).click()
    results = page.locator("#finder-results .find-card")
    sync_api.expect(results).to_have_count(40)
    sync_api.expect(page.locator("#recent")).to_be_hidden()  # nothing played yet
    # A hint on thin-sounding streams only.
    sync_api.expect(results.filter(has_text="Station 1").first.locator(".low-q")).to_have_text("Low quality")
    sync_api.expect(results.filter(has_text="Station 3").first.locator(".low-q")).to_be_visible()  # MP3 56k
    sync_api.expect(page.locator("#finder-results .low-q")).to_have_count(2)  # not AAC at 128k
    sync_api.expect(results.filter(has_text="Station 2").first.locator(".facts")).to_have_text("Ireland")  # no "UNKNOWN"
    # Show more adds the next page below, and focus moves to it.
    page.locator("#btn-more").click()
    sync_api.expect(results).to_have_count(80)
    assert "offset=40" in net.asked[-1]
    sync_api.expect(results.nth(40).locator(".listen")).to_be_focused()
    page.locator("#btn-more").click()
    sync_api.expect(results).to_have_count(100)
    sync_api.expect(page.locator("#btn-more")).to_have_count(0)  # that's all of them
    # A new search while more were loading can still show more.
    page.evaluate("F.more = true; findStations({})")
    sync_api.expect(page.locator("#btn-more")).to_be_enabled()
    # Searches as you type, after a pause (one letter isn't enough).
    before = len(net.asked)
    page.locator("#finder-q").press_sequentially("s")
    page.wait_for_timeout(700)
    assert len(net.asked) == before
    page.locator("#finder-q").press_sequentially("ta")
    page.wait_for_function("() => F.q === 'sta' && !F.loading")
    assert "name=sta" in net.asked[-1]
    sync_api.expect(page.locator("#finder-q")).to_have_value("sta")
    # Listening puts it in Recently played, with its logo kept by the player.
    first = results.filter(has_text="Station 0").first
    first.get_by_role("button", name="Listen to Station 0").click()
    # The logo shows in the now-playing bar and the system's media panel;
    # live radio has no seek bar.
    sync_api.expect(page.locator("#now-art")).to_have_attribute("src", re.compile(r"/api/directory/logo\?u="))
    assert page.evaluate("navigator.mediaSession.metadata.artwork.length") == 1
    sync_api.expect(page.locator(".bar .seek")).to_be_hidden()
    sync_api.expect(page.locator("#finder-q")).to_have_value("sta")  # typing survived the update
    page.wait_for_function("() => (S.lib.recent_stations || []).length === 1")
    page.locator(".nav-item[data-view=radio]").click()
    recent = page.locator("#recent .recent-card")
    sync_api.expect(recent).to_have_count(1)
    sync_api.expect(recent.locator(".facts")).to_have_text("Not saved")
    sync_api.expect(recent.locator("img.logo")).to_have_attribute("src", re.compile(r"^/logo/[0-9a-f]{16}\.png$"))
    assert app.store.config.recent_stations[0]["url"] == "http://127.0.0.1:9/s0"
    # Record it straight from Recently played, without saving it first.
    from station_player.recorder import Recorder
    from test_recorder import FakeStream, Opener
    app.recorder = Recorder(player[2] / "rec", opener=Opener(FakeStream(b"x", live=True)))
    recent.get_by_role("button", name="Record Station 0").click()
    sync_api.expect(recent.get_by_role("button", name="Stop recording Station 0")).to_be_visible()
    sync_api.expect(recent).to_have_class(re.compile(r"\brec-on\b"))
    sync_api.expect(page.locator("#rec-status")).to_contain_text("Recording Station 0")
    recent.get_by_role("button", name="Stop recording Station 0").click()
    sync_api.expect(recent.get_by_role("button", name="Record Station 0")).to_be_visible()
    sync_api.expect(page.locator("#rec-status")).to_be_hidden()
    # Add it from Recently played: a saved station with the same logo.
    recent.get_by_role("button", name="Add Station 0 to your stations").click()
    card = page.locator(".card[data-url='http://127.0.0.1:9/s0']")
    sync_api.expect(card.locator("img.logo")).to_have_attribute("src", re.compile(r"^/logo/"))
    sync_api.expect(recent.locator(".facts")).to_have_text("In your stations")
    saved = next(s for s in app.store.config.stations if s["url"] == "http://127.0.0.1:9/s0")
    assert saved["logo"] == app.store.config.recent_stations[0]["logo"]
    # A saved station plays with its own logo; a track brings the seek bar back.
    card.get_by_role("button", name="Listen").click()
    sync_api.expect(page.locator("#now-art")).to_have_attribute("src", f"/logo/{saved['logo']}")
    page.locator("#nav-folders .nav-item").first.click()
    page.locator("#view tbody tr").first.dblclick()
    page.wait_for_function("() => S.mode === 'track'")
    sync_api.expect(page.locator(".bar .seek")).to_be_visible()
    # Clear forgets the list (the saved station keeps its logo).
    page.locator(".nav-item[data-view=radio]").click()
    page.get_by_role("button", name="Clear Recently played").click()
    sync_api.expect(page.locator("#recent")).to_be_hidden()
    assert app.logos.has(saved["logo"])


def test_choose_find_and_remove_a_station_logo(page, player, tmp_path):
    app = player[0]
    app.logos.grace = 0
    net = FakeDirectoryNet()
    app.directory = Directory(fetch=net, servers=["api.example"], public=lambda host: True)
    page.locator(".nav-item[data-view=radio]").click()
    card = page.locator(".card[data-url='http://127.0.0.1:9/stream']")
    card.get_by_role("button", name="Edit Test FM").click()
    card.get_by_label("Station name").fill("Renamed FM")  # typed, not saved yet
    # Choose a picture from a file.
    picture = tmp_path / "logo.png"
    picture.write_bytes(PNG)
    card.locator(".logo-row input[type=file]").set_input_files(str(picture))
    # The form stays open, showing the new logo.
    sync_api.expect(card.locator(".logo-row img.logo")).to_have_attribute("src", re.compile(r"^/logo/"))
    assert app.store.config.stations[0]["logo"].endswith(".png")
    sync_api.expect(card.get_by_label("Station name")).to_have_value("Renamed FM")
    # Something that isn't a picture is refused.
    bad = tmp_path / "x.png"
    bad.write_bytes(b"<svg onload=alert(1)>")
    card.locator(".logo-row input[type=file]").set_input_files(str(bad))
    sync_api.expect(card.locator(".test-result")).to_contain_text("isn't a PNG")
    # The one Radio Browser has (none here: it says so), then no logo.
    card.get_by_role("button", name="Find logo").click()
    sync_api.expect(card.locator(".test-result")).to_contain_text("no logo")
    sync_api.expect(card.get_by_role("button", name="Find logo")).to_be_enabled()
    card.get_by_role("button", name="No logo").click()
    sync_api.expect(card.locator(".logo-row .glyph")).to_be_visible()
    assert "logo" not in app.store.config.stations[0]
    assert not any((app.store.home / "logos").glob("*.png"))  # nothing left over
    card.get_by_role("button", name="Cancel").click()
    sync_api.expect(card.locator(".card-top .glyph")).to_be_visible()
    # Find logos looks up every station without one.
    net.byurl = True
    page.locator("#btn-find-logos").click()
    sync_api.expect(card.locator(".card-top img.logo")).to_have_attribute("src", re.compile(r"^/logo/"))
    sync_api.expect(page.locator("#btn-find-logos")).to_have_count(0)  # every station has one now
    assert any("/json/stations/byurl" in u for u in net.asked)


def test_select_several_tracks_and_add_them_together(page, player):
    app = player[0]
    answer_prompts(page, "Mix")
    page.locator("#nav-folders .nav-item").first.click()
    page.wait_for_selector("#view tbody tr")
    rows = page.locator("#view tbody tr")
    bar = page.locator(".select-bar")
    # One click picks one track: no bar.
    rows.nth(0).click()
    sync_api.expect(bar).to_be_hidden()
    # A track taken out of the selection stays out.
    rows.nth(0).click(modifiers=["ControlOrMeta"])
    rows.nth(1).click(modifiers=["ControlOrMeta"])
    sync_api.expect(bar).to_be_hidden()
    rows.nth(0).click()
    # Shift-click picks a run, Cmd/Ctrl-click one more or one less.
    rows.nth(2).click(modifiers=["Shift"])
    sync_api.expect(bar.locator(".count")).to_have_text("3 tracks selected")
    sync_api.expect(bar).not_to_contain_text("null")
    rows.nth(1).click(modifiers=["ControlOrMeta"])
    sync_api.expect(bar.locator(".count")).to_have_text("2 tracks selected")
    sync_api.expect(page.locator("#view tbody tr.selected")).to_have_count(2)
    # Add them to a new playlist, in the playlist's order.
    bar.get_by_role("button", name="Add to playlist…").click()
    page.locator("#track-menu button", has_text="New playlist…").click()
    page.wait_for_function("() => S.lib.playlists.some((p) => p.kind === 'mine' && p.count === 2)")
    mix = next(p for p in app.my_summaries() if p["name"] == "Mix")
    assert [t["name"] for t in app.playlist_detail(mix["id"])["tracks"]] == ["One", "Three"]
    # The ⋯ menu on a selected track acts on them all; Escape clears.
    rows.nth(3).click(modifiers=["ControlOrMeta"])
    open_menu(page, 3)
    sync_api.expect(page.locator("#track-menu")).to_have_attribute("aria-label", "3 tracks selected")
    page.locator("#track-menu button", has_text="Add 3 tracks to a playlist…").click()
    page.locator("#track-menu button", has_text="Mix").click()
    sync_api.expect(page.locator("#nav-mine .count")).to_have_text("5")
    page.keyboard.press("Escape")
    rows.nth(3).focus()
    page.keyboard.press("Escape")
    sync_api.expect(bar).to_be_hidden()
    # Cmd/Ctrl+Space picks a track without starting or stopping playback.
    rows.nth(0).focus()
    page.keyboard.press("ControlOrMeta+Space")
    assert page.evaluate("S.mode") == "idle"
    page.keyboard.press("Escape")
    # Keyboard: Shift+arrows select a run; Cmd/Ctrl+A all; play them next.
    rows.nth(0).focus()
    page.keyboard.press("Shift+ArrowDown")
    sync_api.expect(bar.locator(".count")).to_have_text("2 tracks selected")
    page.keyboard.press("ControlOrMeta+a")
    sync_api.expect(bar.locator(".count")).to_have_text("4 tracks selected")
    bar.get_by_role("button", name="Add to queue").click()
    page.wait_for_function("() => S.mode === 'track' && S.upNext.length === 3")
    # In my playlist, several go at once.
    page.locator("#nav-mine .nav-item", has_text="Mix").click()
    page.wait_for_function("() => document.querySelectorAll('#view tbody tr').length === 5")
    rows.nth(0).click()
    rows.nth(1).click(modifiers=["Shift"])
    bar.get_by_role("button", name="Remove").click()
    page.wait_for_function("() => document.querySelectorAll('#view tbody tr').length === 3")
    assert row_names(page) == ["One", "Three", "Quiet"]


def test_add_a_recording_to_a_playlist(page, player):
    from station_player.recorder import Recorder
    from test_recorder import FakeStream, Opener

    app, _, tmp = player
    app.recorder = Recorder(tmp / "rec", opener=Opener(FakeStream(b"", ctype="audio/wav")))
    (tmp / "rec").mkdir()
    tone(tmp / "rec" / "Old FM 2026-10-01 07.30.wav", 1.0, 0.3)
    app.create_playlist("Shows", [])
    page.reload()
    page.locator(".nav-item[data-view=recordings]").click()
    add = page.get_by_role("button", name=re.compile(r"^Add Old FM .* to a playlist$"))
    add.click()
    page.locator("#track-menu button", has_text="Shows").click()
    sync_api.expect(page.locator("#nav-mine .count")).to_have_text("1")
    shows = next(p for p in app.my_summaries() if p["name"] == "Shows")
    assert [t["name"] for t in app.playlist_detail(shows["id"])["tracks"]][0].startswith("Old FM")
    sync_api.expect(add).to_be_focused()  # back where it was


def test_tag_stations_and_filter_by_tag(page, player):
    app = player[0]
    app.store.config.stations.append({"name": "Second FM", "url": "http://127.0.0.1:9/two", "tags": ["soul"]})
    page.evaluate("loadState()")
    page.locator(".nav-item[data-view=radio]").click()
    page.get_by_role("button", name="Edit Test FM").click()
    card = page.locator(".card", has=page.locator("form.edit"))
    # A tag used on another station is one click away.
    card.get_by_role("button", name="Add the tag soul").click()
    sync_api.expect(card.get_by_label("Tags")).to_have_value("soul")
    card.get_by_label("Tags").fill("soul, Late night, SOUL")
    card.get_by_role("button", name="Save", exact=True).click()
    sync_api.expect(page.locator(".toast").last).to_contain_text("Saved Test FM")
    assert app.store.config.stations[0]["tags"] == ["soul", "Late night"]
    chips = page.locator("#station-tags .chip")
    sync_api.expect(chips).to_have_text(["All", "Late night (1)", "soul (2)"])
    chips.filter(has_text="Late night").click()
    sync_api.expect(page.locator(".cards .card:visible")).to_have_count(1)
    sync_api.expect(page.locator("#station-tags [data-tag='Late night']")).to_be_focused()
    # The filter is remembered, and a tag on a card picks it too.
    page.reload()
    page.locator(".nav-item[data-view=radio]").click()
    sync_api.expect(page.locator(".cards .card:visible")).to_have_count(1)
    page.locator("#station-tags .chip", has_text="All").click()
    sync_api.expect(page.locator(".cards .card:visible")).to_have_count(2)
    page.locator(".card .tag", has_text="soul").first.click()
    sync_api.expect(page.locator(".cards .card:visible")).to_have_count(2)
    sync_api.expect(page.locator("#station-tags [data-tag=soul]")).to_have_attribute("aria-pressed", "true")


def test_desktop_icon_station_plays_before_the_library_is_read(browser, player):
    app, url, _ = player
    app.start_station("http://127.0.0.1:9/stream", hold=True)
    app.remote_commands[0]["at"] = 0  # the library took a long time to read
    original = app.state
    app.state = lambda: {**original(), "scanning": True}
    ctx = browser.new_context()
    pg = ctx.new_page()
    try:
        pg.goto(url)
        pg.wait_for_function("() => S.mode === 'radio' && S.station && S.station.name === 'Test FM'")
        app.state = original
        pg.wait_for_function("() => S.view.type === 'radio'")
        assert pg.evaluate("S.mode") == "radio"  # resuming the last session didn't replace it
    finally:
        ctx.close()


def test_tag_filter_keeps_what_you_typed(page, player):
    app = player[0]
    app.store.config.stations.append({"name": "Second FM", "url": "http://127.0.0.1:9/two", "tags": ["soul"]})
    page.evaluate("loadState()")
    page.locator(".nav-item[data-view=radio]").click()
    page.get_by_role("button", name="Edit Test FM").click()
    card = page.locator(".card", has=page.locator("form.edit"))
    card.get_by_label("Name").fill("Typed FM")
    page.locator("#station-tags .chip", has_text="soul").click()
    sync_api.expect(page.locator(".card", has=page.locator("form.edit")).get_by_label("Name")).to_have_value("Typed FM")


def test_put_a_station_on_the_desktop(page, player):
    app = player[0]
    made = []

    class Icons:
        def status(self):
            return {"supported": True, "platform": "mac", "shortcut": False, "login": False}

        def station_shortcut(self, name, url, icons, logo=None):
            made.append(name)
            return Path("/Users/x/Desktop") / f"{name}.app"

    app.desktop = Icons()
    page.evaluate("loadState()")
    page.locator(".nav-item[data-view=radio]").click()
    page.get_by_role("button", name="Edit Test FM").click()
    page.get_by_role("button", name="Put on desktop").click()
    sync_api.expect(page.locator(".toast").last).to_contain_text("Test FM is on your desktop")
    assert made == ["Test FM"]


def test_help_page(page):
    page.locator(".nav-item[data-view=help]").click()
    sync_api.expect(page.locator("#view h1")).to_have_text("Help")
    page.locator(".help-toc .chip", has_text="Tagging stations").click()
    sync_api.expect(page.locator("#help-tags h2")).to_be_focused()
    page.locator("#help-radio").get_by_role("button", name="Go to Stations").click()
    sync_api.expect(page.locator("#view h1")).to_have_text("Radio")


def test_dock_menu_play_pause(page, player):
    page.locator("#view tbody tr").first.dblclick()
    page.wait_for_function("() => S.mode === 'track' && !audio.paused")
    page.evaluate("runLaunch('toggle')")
    page.wait_for_function("() => audio.paused")
    # A window opened from the menu acts once it's up, and tidies its address.
    page.goto(player[1] + "?do=toggle")
    page.wait_for_function("() => S.mode === 'track' && !audio.paused", timeout=10000)
    assert page.evaluate("location.search") == ""
    manifest = json.loads((Path(__file__).parents[1] / "station_player/static/manifest.webmanifest").read_text())
    assert [s["url"] for s in manifest["shortcuts"]] == ["/?do=toggle", "/?do=next"]


def test_downloaded_app_shows_how_to_update_it(page, player):
    app = player[0]
    app.desktop.bundled = True
    app.latest_version = "99.0.0"
    page.reload()
    note = page.locator("#update")
    note.wait_for(state="visible")
    sync_api.expect(note.get_by_role("button", name="Update now")).to_be_visible()
    # The installer made its shortcuts, so Settings doesn't offer to.
    open_settings(page, "app")
    sync_api.expect(page.locator("#update-settings")).to_contain_text("checks it's the genuine file")
    sync_api.expect(page.locator("#view")).to_contain_text("Start Station Player in the background")
    sync_api.expect(page.get_by_text("Add Station Player to")).to_have_count(0)


# ---- the car page (player/car/, published on GitHub Pages) -------------------------

CAR_DIR = Path(__file__).parents[1] / "car"


@pytest.fixture
def car_site(tmp_path):
    """The car page, plus a "station" that fails its first request (a
    dropped signal) and then plays."""
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    sound = tone(tmp_path / "radio" / "tone.wav", 30.0, 0.3).read_bytes()
    hits = {"flaky": 0}

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            name = self.path.split("?")[0].lstrip("/")
            if name in ("good.wav", "flaky.wav"):
                if name == "flaky.wav":
                    hits["flaky"] += 1
                    if hits["flaky"] == 1:
                        self.send_error(503)
                        return
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(sound)))
                self.end_headers()
                self.wfile.write(sound)
                return
            super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(CAR_DIR)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/", hits
    server.shutdown()
    server.server_close()


def test_car_page_plays_the_players_stations(browser, player, car_site):
    app = player[0]
    base, hits = car_site
    app.store.config.stations = [
        {"name": "Good FM", "url": base + "good.wav", "tags": ["jazz"]},
        {"name": "Flaky Radio", "url": base + "flaky.wav"},
        {"name": "Third", "url": base + "good.wav?3", "tags": ["jazz"]},
    ]
    link = app.car_link()
    assert link.startswith("https://raby.github.io/station-studio/car/#s=")
    ctx = browser.new_context()
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "index.html" + link[link.index("#"):])
    tiles = page.locator(".station")
    sync_api.expect(tiles).to_have_count(3)
    assert tiles.all_inner_texts()[0].endswith("Good FM")

    tiles.nth(0).click()
    sync_api.expect(page.locator("#now-status")).to_have_text("Playing", timeout=10000)
    sync_api.expect(page.locator("#now-name")).to_have_text("Good FM")
    assert page.evaluate("navigator.mediaSession.metadata.title") == "Good FM"

    # Next (the steering wheel's skip) goes to Flaky Radio, whose first
    # try fails: it reconnects by itself.
    page.locator("#btn-next").click()
    sync_api.expect(page.locator("#now-name")).to_have_text("Flaky Radio")
    sync_api.expect(page.locator("#now-status")).to_have_text("Playing", timeout=15000)
    assert hits["flaky"] >= 2

    # Tags filter the stations, and skipping stays within them.
    page.locator(".tag", has_text="jazz").click()
    sync_api.expect(tiles).to_have_count(2)
    page.locator(".station", has_text="Third").click()
    page.locator("#btn-next").click()
    sync_api.expect(page.locator("#now-name")).to_have_text("Good FM")

    # Paused and resumed from outside the page (an iPhone handing the sound
    # over to the car): the big button still shows Stop while it plays.
    page.evaluate("audio.pause()")
    sync_api.expect(page.locator("#btn-play")).to_have_attribute("aria-label", "Play")
    page.evaluate("audio.play()")
    sync_api.expect(page.locator("#btn-play")).to_have_attribute("aria-label", "Stop")

    page.locator("#btn-play").click()  # stop
    page.wait_for_function("() => audio.paused && !audio.getAttribute('src')")
    sync_api.expect(page.locator("#btn-play")).to_have_attribute("aria-label", "Play")

    # Opened again without the link (a home-screen icon that lost it), it
    # still has the stations, the tag and the last station.
    page.goto(base + "index.html")
    sync_api.expect(tiles).to_have_count(2)
    sync_api.expect(page.locator("#now-name")).to_have_text("Good FM")
    ctx.close()
    assert not errors, errors


def test_car_page_without_stations_says_how_to_get_them(browser, car_site):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(car_site[0] + "index.html")
    sync_api.expect(page.locator("#empty")).to_be_visible()
    assert page.locator("#btn-play").is_disabled()
    ctx.close()


def test_settings_in_the_car(page, player):
    app = player[0]
    app.store.config.stations = [{"name": "Old Radio", "url": "http://example.com/live"},
                                 {"name": "New Radio", "url": "https://example.com/live"}]
    open_settings(page, "car")
    sync_api.expect(page.locator("#view h2")).to_have_text("In the car")
    img = page.locator(".car-box img")
    page.wait_for_function("() => document.querySelector('.car-box img').naturalWidth > 0")
    assert img.get_attribute("src").startswith("/api/car/qr.svg")
    sync_api.expect(page.locator("#btn-car-copy")).to_be_enabled()
    sync_api.expect(page.locator("#view")).to_contain_text("Old Radio has only an http:// link")


def test_alarm_greeting_follows_the_time_of_day(page):
    at = lambda h, m=0: page.evaluate(f"alarmGreeting(new Date(2026, 0, 1, {h}, {m}))")
    assert [at(7), at(12, 30), at(18, 25), at(23, 30), at(4)] == [
        "Good morning", "Good afternoon", "Good evening", "Hello", "Hello"]
    # Home's own greeting stays its own (the two once shared a name, so Home said Hello late at night)
    home = lambda h: page.evaluate(f"greeting(new Date(2026, 0, 1, {h}))")
    assert [home(2), home(9), home(23)] == ["Good night", "Good morning", "Good evening"]


class GlobeNet(FakeDirectoryNet):
    """Stations with positions: three worldwide, one more in Great Britain,
    and news and talk ones."""

    def __call__(self, url, user_agent, limit):
        if "/json/stations/search" not in url:
            return super().__call__(url, user_agent, limit)
        self.asked.append(url)
        q = parse_qs(urlparse(url).query)

        def st(name, lat, lon, cc, votes, tags="pop"):
            return {"stationuuid": f"uuid-{name}", "name": name, "url_resolved": f"http://127.0.0.1:9/{name.replace(' ', '')}",
                    "favicon": "", "tags": tags, "countrycode": cc, "codec": "MP3", "bitrate": 128, "votes": votes,
                    "geo_lat": lat, "geo_long": lon}
        tag, cc = q.get("tag", [""])[0], q.get("countrycode", [""])[0]
        if q.get("name"):  # the shipped hand-picked sport stations, looked up: none here
            items = []
        elif tag == "news":
            items = [st("World Service", 51.52, -0.14, "GB", 90, "news")]
        elif tag == "talk":
            items = [st("Talk Tokyo", 35.7, 139.7, "JP", 30, "talk")]
        elif tag == "football":
            items = [st("Football Live", 53.4, -2.98, "GB", 40, "football")]
        elif tag in ("sport", "sports", "soccer", "deportes", "esportes", "cricket", "racing"):
            items = []
        elif cc == "GB":
            items = [st("Manchester FM", 53.48, -2.24, "GB", 20), st("London FM", 51.5, -0.12, "GB", 80)]
        else:
            items = [st("London FM", 51.5, -0.12, "GB", 80), st("Tokyo FM", 35.68, 139.69, "JP", 70),
                     st("New York FM", 40.71, -74.0, "US", 60), {"name": "Nowhere", "url_resolved": "http://127.0.0.1:9/x"}]
        return json.dumps(items).encode(), "application/json"


def test_globe(page, player):
    app = player[0]
    net = GlobeNet()
    app.directory = Directory(fetch=net, servers=["api.example"], public=lambda host: True)
    page.locator(".nav-item[data-view=discover]").click()
    sync_api.expect(page.get_by_role("button", name="Globe", exact=True)).to_have_attribute("aria-pressed", "true")
    page.locator("#globe").focus()
    sync_api.expect(page.locator("#finder-tags")).to_be_hidden()
    status = page.locator("#globe-status")
    sync_api.expect(status).to_have_text("3 stations on the globe")
    assert any("has_geo_info=true" in u and "countrycode" not in u for u in net.asked)
    # Starts over home (en-US here), turning by itself.
    lon = page.evaluate("G.lon")
    assert -125 < lon < -60
    page.wait_for_function(f"() => G.lon !== {lon}")
    # Tap a dot: it plays, and comes first in the list.
    page.evaluate("globeStop(); G.lon = 0; G.lat = 50; G.zoom = 2; globeDraw()")
    spot = page.evaluate("""() => {
        const cv = $('#globe'), v = globeView(cv), [x, y] = v.at(-0.12, 51.5), dpr = window.devicePixelRatio || 1;
        return [x / dpr, y / dpr];
    }""")
    # Hovering names a dot.
    box = page.locator("#globe").bounding_box()
    page.mouse.move(box["x"] + spot[0], box["y"] + spot[1])
    page.wait_for_function("() => G.hover && G.hover.name === 'London FM'")
    # Tap a dot: it shows what it is, first in the list, without playing.
    page.locator("#globe").click(position={"x": spot[0], "y": spot[1]})
    first = page.locator("#globe-list .find-card").first
    sync_api.expect(first.locator(".name")).to_have_text("London FM")
    sync_api.expect(first).to_have_class(re.compile(r"\bpicked\b"))
    page.wait_for_timeout(300)
    assert page.evaluate("S.mode !== 'radio' || !S.station || S.station.name !== 'London FM'")
    # Tapped again, it plays.
    page.locator("#globe").click(position={"x": spot[0], "y": spot[1]})
    page.wait_for_function("() => S.mode === 'radio' && S.station.name === 'London FM'")
    sync_api.expect(page.locator("#globe-list .find-card").first.locator(".name")).to_have_text("London FM")
    # Zoomed in, it finds more in the country in the middle.
    page.locator("#globe").focus()
    page.keyboard.press("+")
    sync_api.expect(status).to_have_text("4 stations on the globe")
    assert page.evaluate("G.zoom") == 3
    assert any("countrycode=GB" in u for u in net.asked)
    sync_api.expect(page.locator("#globe-list")).to_contain_text("Manchester FM")
    page.keyboard.press("ArrowLeft")
    assert page.evaluate("G.lon") == -4
    # News and talk only.
    page.get_by_role("button", name="News", exact=True).click()
    sync_api.expect(status).to_have_text("2 news and talk stations on the globe")
    assert any("tag=talk" in u for u in net.asked)
    sync_api.expect(page.locator("#globe-genre")).to_be_hidden()
    # Sport only.
    page.get_by_role("button", name="Sport", exact=True).click()
    sync_api.expect(status).to_have_text("1 sport station on the globe")
    assert any("tag=football" in u for u in net.asked)
    sync_api.expect(page.locator("#globe-genre")).to_be_hidden()
    # Music: any (no talk), or one genre.
    page.get_by_role("button", name="Music", exact=True).click()
    sync_api.expect(status).to_have_text("3 music stations on the globe")
    sync_api.expect(page.get_by_role("button", name="News", exact=True)).to_have_attribute("aria-pressed", "false")
    page.locator("#globe-genre").select_option("jazz")
    sync_api.expect(status).to_have_text("3 jazz stations on the globe")
    assert "tag=jazz" in net.asked[-1]
    assert page.evaluate("localStorage.getItem('sp.globe-genre')") == "jazz"
    # The list comes back, and searching shows it too.
    page.get_by_role("button", name="List", exact=True).click()
    sync_api.expect(page.locator("#finder-tags")).to_be_visible()
    sync_api.expect(page.locator("#globe")).to_have_count(0)
    page.get_by_role("button", name="Globe", exact=True).click()
    page.locator("#finder-q").fill("fm")
    page.locator("#finder-q").press("Enter")
    sync_api.expect(page.locator("#finder-results")).to_be_visible()
    sync_api.expect(page.get_by_role("button", name="List", exact=True)).to_have_attribute("aria-pressed", "true")


def test_plain_player_hides_the_studio_tools(page, player):
    app = player[0]
    app.store.config.studio = False  # a new install without station-studio
    page.reload()
    page.wait_for_selector("#view tbody tr")
    assert page.evaluate("document.body.classList.contains('public')")
    for hidden in (".studio-badge", ".nav-item[data-view=recordings]", "#nav-itunes"):
        sync_api.expect(page.locator(hidden)).to_be_hidden()
    sync_api.expect(page.locator(".nav-item[data-view=folders], #nav-folders")).to_be_visible()  # your own music stays
    page.locator(".nav-item[data-view=radio]").click()
    sync_api.expect(page.locator(".card[data-url] .listen").first).to_be_visible()
    sync_api.expect(page.locator(".rec-btn").first).to_be_hidden()
    page.locator(".nav-item[data-view=settings]").click()
    sync_api.expect(page.locator("#tab-log")).to_have_count(0)
    page.locator(".nav-item[data-view=help]").click()
    sync_api.expect(page.locator("#help-record")).to_have_count(0)
    sync_api.expect(page.locator("#help-studio")).to_be_visible()
    # Turned on in Settings › App, everything is back.
    open_settings(page, "app")
    page.get_by_label("Show the studio tools").check()
    sync_api.expect(page.locator(".nav-item[data-view=recordings]")).to_be_visible()
    sync_api.expect(page.locator(".studio-badge")).to_have_text("Studio")
    sync_api.expect(page.locator("#tab-log")).to_be_visible()
    assert app.store.config.studio is True
    # ...and off again from the Recordings page, it goes to the radio.
    page.locator(".nav-item[data-view=recordings]").click()
    page.evaluate("api('/api/settings', { studio: false }).then(loadState).then(renderView)")
    sync_api.expect(page.locator(".view-head h1, .radio-head h1").first).to_be_visible()
    page.wait_for_function("() => S.view.type === 'radio'")
