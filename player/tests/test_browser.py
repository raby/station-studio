"""The player page in a real browser (Chromium via Playwright): gapless
playback, the queue, resuming, library search, volume levelling and the
new-version notice. Skipped when Playwright isn't installed."""
import json
import math
import re
import os
import struct
import threading
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

    def __call__(self, url, user_agent, limit):
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
    page.locator(".nav-item[data-view=radio]").click()
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
    sync_api.expect(page.locator(".card[data-url='http://127.0.0.1:9/jazz']")).to_be_visible()
    sync_api.expect(jazz.get_by_role("button", name="Jazz Cafe Radio is in your stations")).to_be_disabled()
    assert any(s["url"] == "http://127.0.0.1:9/jazz" and s["name"] == "Jazz Cafe Radio" for s in app.store.config.stations)
    sync_api.expect(page.locator("#finder-q")).to_have_value("cafe")  # still there after the redraw


def test_find_stations_offline(page):
    page.locator(".nav-item[data-view=radio]").click()
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
    page.locator(".nav-item[data-view=radio]").click()
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
    recent = page.locator("#recent .recent-card")
    sync_api.expect(recent).to_have_count(1)
    sync_api.expect(recent.locator(".facts")).to_have_text("Not saved")
    sync_api.expect(recent.locator("img.logo")).to_have_attribute("src", re.compile(r"^/logo/[0-9a-f]{16}\.png$"))
    assert app.store.config.recent_stations[0]["url"] == "http://127.0.0.1:9/s0"
    # The logo shows in the now-playing bar and the system's media panel;
    # live radio has no seek bar.
    sync_api.expect(page.locator("#now-art")).to_have_attribute("src", re.compile(r"/api/directory/logo\?u="))
    assert page.evaluate("navigator.mediaSession.metadata.artwork.length") == 1
    sync_api.expect(page.locator(".bar .seek")).to_be_hidden()
    sync_api.expect(page.locator("#finder-q")).to_have_value("sta")  # typing survived the update
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
    # Shift-click picks a run, Cmd/Ctrl-click one more or one less.
    rows.nth(2).click(modifiers=["Shift"])
    sync_api.expect(bar.locator(".count")).to_have_text("3 tracks selected")
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
