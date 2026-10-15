"""The player page in a real browser (Chromium via Playwright): gapless
playback, the queue, resuming, library search, volume levelling and the
new-version notice. Skipped when Playwright isn't installed."""
import json
import math
import os
import struct
import threading
import wave
from pathlib import Path

import pytest

# CI sets this so a broken browser install fails instead of skipping.
REQUIRED = os.environ.get("STATION_PLAYER_REQUIRE_BROWSER") == "1"
if REQUIRED:
    import playwright.sync_api as sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

from station_player.config import ConfigStore  # noqa: E402
from station_player.server import PlayerApp, make_server  # noqa: E402

RATE = 22050


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
    store.config.music_folders = [str(music)]
    store.config.stations = [{"name": "Test FM", "url": "http://127.0.0.1:9/stream"}]
    app = PlayerApp(store)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{server.server_address[1]}/", tmp_path
    server.shutdown()
    server.server_close()


@pytest.fixture
def page(browser, player):
    ctx = browser.new_context()
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(player[1])
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
    page.locator(".nav-item[data-view=settings]").click()
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
    # Measurements are remembered by the server for next time.
    saved = json.loads((tmp / "home" / "loudness.json").read_text())
    assert len(saved["entries"]) >= 2


def test_new_version_notice(page, player):
    app, url, _ = player
    app.latest_version = "99.0.0"
    page.reload()
    note = page.locator("#update")
    note.wait_for(state="visible")
    assert "99.0.0" in note.inner_text() and "pipx upgrade station-player" in note.inner_text()
    note.locator("button").click()
    assert note.is_hidden()
    page.reload()
    page.wait_for_selector("#view tbody tr")
    assert note.is_hidden()  # dismissed for this version


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
