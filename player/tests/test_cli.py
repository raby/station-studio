import json
import socket
import time
import urllib.request
from pathlib import Path

from conftest import write_itunes_xml, write_wav
from station_player import __main__ as cli


def test_cli_classifies_sources_and_scans_once(tmp_path, monkeypatch):
    exports = tmp_path / "exports"
    write_itunes_xml(exports / "rebirth-radio" / "001.xml", [], [])  # nested, like station-studio
    music = tmp_path / "Music"
    write_wav(music / "a.wav")
    single = write_itunes_xml(tmp_path / "lib.xml", [], [])

    scans = []
    real_load = cli.PlayerApp._rebuild

    def counting(self):
        scans.append(1)
        return real_load(self)

    monkeypatch.setattr(cli.PlayerApp, "_rebuild", counting)
    monkeypatch.setattr(cli, "_running_instance", lambda url: False)
    started = []

    class FakeServer:
        def serve_forever(self):
            started.append(1)
            raise KeyboardInterrupt

        def server_close(self):
            pass

    def fake_make_server(app, port):
        started.append(app)
        return FakeServer()

    monkeypatch.setattr(cli, "make_server", fake_make_server)
    threads = []
    real_bg = cli.PlayerApp.scan_in_background
    monkeypatch.setattr(cli.PlayerApp, "scan_in_background", lambda self, on_done=None: threads.append(real_bg(self, on_done)))

    rc = cli.main([str(exports), str(music), str(single), str(music / "a.wav"),
                   str(tmp_path / "missing"), "--no-browser"])
    assert rc == 0
    for t in threads:
        t.join(5)
    assert len(scans) == 1  # one scan, after the server is up, with every source

    saved = json.loads((tmp_path / "player-home" / "config.json").read_text())
    assert saved["playlist_sources"] == [str(exports.resolve()), str(single.resolve())]
    assert saved["music_folders"] == [str(music.resolve())]


def test_cli_registers_an_itunes_folder_as_both(tmp_path, monkeypatch):
    itunes = tmp_path / "iTunes"
    write_itunes_xml(itunes / "iTunes Library.xml", [], [])
    write_wav(itunes / "iTunes Media" / "Music" / "a.wav")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(cli, "_running_instance", lambda url: False)
    monkeypatch.setattr(cli, "make_server", lambda app, port: (_ for _ in ()).throw(OSError("stop")))
    cli.main([str(itunes), str(empty), "--no-browser"])
    saved = json.loads((tmp_path / "player-home" / "config.json").read_text())
    assert saved["playlist_sources"] == [str(itunes.resolve())]
    assert saved["music_folders"] == [str(itunes.resolve()), str(empty.resolve())]


def test_cli_hands_sources_to_a_running_copy_instead_of_saving(tmp_path, monkeypatch):
    music = tmp_path / "Music"
    write_wav(music / "a.wav")
    sent, opened = [], []
    monkeypatch.setattr(cli, "_running_instance", lambda url: True)
    monkeypatch.setattr(cli, "_send_sources", lambda url, sources: sent.extend(sources))
    monkeypatch.setattr(cli, "open_ui", lambda url, home, own_window=True: opened.append(url))
    monkeypatch.setattr(cli, "make_server", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no second server")))
    assert cli.main([str(music)]) == 0
    assert sent == [("music", str(music.resolve()))]
    assert opened == ["http://127.0.0.1:8765/"]
    # The running copy owns the settings file; this one must not write it.
    assert not (tmp_path / "player-home" / "config.json").exists()


def test_running_instance_check_against_a_real_server(tmp_path, monkeypatch):
    # A system proxy with no localhost exception must not hide the copy.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    import threading
    from station_player.config import ConfigStore
    from station_player.server import PlayerApp, make_server
    server = make_server(PlayerApp(ConfigStore()), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        assert cli._running_instance(f"http://127.0.0.1:{port}/")
        cli._send_sources(f"http://127.0.0.1:{port}/", [("music", str(tmp_path))])
        saved = json.loads((tmp_path / "player-home" / "config.json").read_text())
        assert saved["music_folders"] == [str(tmp_path.resolve())]
    finally:
        server.shutdown()
        server.server_close()
    assert not cli._running_instance(f"http://127.0.0.1:{port}/")


def test_cli_shortcut_flags(monkeypatch):
    calls = []

    class FakeDesktop:
        def set_shortcut(self, on):
            calls.append(("shortcut", on))

        def set_login(self, on):
            calls.append(("login", on))

        def remove_all(self):
            calls.append(("remove",))

    monkeypatch.setattr(cli, "Desktop", FakeDesktop)
    assert cli.main(["--add-shortcut", "--start-at-login"]) == 0
    assert cli.main(["--remove-shortcuts"]) == 0
    assert calls == [("shortcut", True), ("login", True), ("remove",)]


def test_app_mode_logs_to_a_file(tmp_path, monkeypatch):
    import logging
    monkeypatch.setattr(cli, "_running_instance", lambda url: False)
    monkeypatch.setattr(cli, "make_server", lambda app, port: (_ for _ in ()).throw(OSError("port busy")))
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        assert cli.main(["--app", "--no-browser"]) == 1
        for h in root.handlers:
            h.flush()
        log = (tmp_path / "player-home" / "station-player.log").read_text()
        assert "port busy" in log
    finally:
        for h in root.handlers:
            if h not in saved:
                h.close()
        root.handlers[:] = saved


def test_mac_style_launch_detaches_and_reuses_the_running_copy(tmp_path, monkeypatch):
    # Real processes: the first launch starts a detached player and exits;
    # the second finds it running. (macOS needs this; it runs anywhere.)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"
    opened = []
    monkeypatch.setattr(cli, "_detach_needed", lambda: True)
    monkeypatch.setattr(cli, "open_ui", lambda url, home, own_window=True: opened.append(url))
    monkeypatch.setenv("PYTHONPATH", str(Path(cli.__file__).resolve().parents[1]))
    try:
        assert cli.main(["--app", "--port", str(port)]) == 0
        assert opened == [url]
        assert cli._running_instance(url)
        assert cli.main(["--app", "--port", str(port)]) == 0  # second click
        assert opened == [url, url]
    finally:
        quit_req = urllib.request.Request(url + "api/quit", data=b"{}",
                                          headers={"X-Station-Player": "1"}, method="POST")
        try:
            urllib.request.urlopen(quit_req, timeout=5).close()
        except OSError:
            pass
    deadline = time.monotonic() + 5
    while cli._running_instance(url) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not cli._running_instance(url)


def test_app_mode_shows_the_copy_that_won_a_startup_race(monkeypatch):
    answers = iter([False, False, True])
    opened = []
    monkeypatch.setattr(cli, "_running_instance", lambda url: next(answers))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "open_ui", lambda url, home, own_window=True: opened.append(url))
    monkeypatch.setattr(cli, "make_server", lambda app, port: (_ for _ in ()).throw(OSError("in use")))
    assert cli.main(["--app"]) == 0
    assert opened == ["http://127.0.0.1:8765/"]


def test_scan_problems_are_reported_once_the_scan_finishes(tmp_path, monkeypatch, capsys):
    store_missing = tmp_path / "gone"
    monkeypatch.setattr(cli, "_running_instance", lambda url: False)
    threads = []
    real_bg = cli.PlayerApp.scan_in_background
    monkeypatch.setattr(cli.PlayerApp, "scan_in_background",
                        lambda self, on_done=None: threads.append(real_bg(self, on_done)))

    class FakeServer:
        def serve_forever(self):
            for t in threads:
                t.join(5)
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(cli, "make_server", lambda app, port: FakeServer())
    home = tmp_path / "player-home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"music_folders": [str(store_missing)]}))
    assert cli.main(["--no-browser"]) == 0
    assert "Music folder not found" in capsys.readouterr().out


def test_audio_files_are_opened_not_added(tmp_path):
    from conftest import write_wav
    song = write_wav(tmp_path / "a song.wav")
    assert cli._classify(str(song)) == [("open", str(song.resolve()))]
    (tmp_path / "notes.txt").write_text("x")
    assert "audio file" in cli._classify(str(tmp_path / "notes.txt"))


def test_open_with_plays_in_the_running_copy(tmp_path, monkeypatch):
    song = write_wav(tmp_path / "song.wav")
    sent, shown = [], []
    monkeypatch.setattr(cli, "_running_instance", lambda url: True)
    monkeypatch.setattr(cli, "open_ui", lambda url, home, own_window=True: shown.append(url))
    window = {"open": True}
    monkeypatch.setattr(cli, "_send_open", lambda url, files: (sent.append(files), window["open"])[1])
    assert cli.main(["--app", str(song)]) == 0
    assert sent == [[str(song.resolve())]] and shown == []  # its window plays it
    window["open"] = False
    assert cli.main(["--app", str(song)]) == 0
    assert shown == ["http://127.0.0.1:8765/"]  # no window open: open one


def test_send_open_against_a_real_server(tmp_path):
    import threading
    from station_player.config import ConfigStore
    from station_player.server import PlayerApp, make_server
    app = PlayerApp(ConfigStore())
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        song = write_wav(tmp_path / "song.wav")
        assert cli._send_open(f"http://127.0.0.1:{server.server_address[1]}/", [str(song)]) is False
        assert app.open_request["seq"] > 0
    finally:
        server.shutdown()
        server.server_close()


def test_station_icon_plays_in_the_running_copy(monkeypatch):
    sent, shown = [], []
    monkeypatch.setattr(cli, "_running_instance", lambda url: True)
    monkeypatch.setattr(cli, "open_ui", lambda url, home, own_window=True: shown.append(url))
    window = {"open": True}
    monkeypatch.setattr(cli, "_send_station", lambda url, st: (sent.append(st), window["open"])[1])
    assert cli.main(["--app", "--station", "https://s.example/live"]) == 0
    assert sent == ["https://s.example/live"] and shown == []
    window["open"] = False
    assert cli.main(["--app", "--station", "https://s.example/live"]) == 0
    assert shown == ["http://127.0.0.1:8765/"]


def test_station_icon_starts_the_player_playing(monkeypatch):
    from station_player.config import DEFAULT_STATIONS
    monkeypatch.setattr(cli, "_running_instance", lambda url: False)
    monkeypatch.setattr(cli, "_detach_needed", lambda: False)
    apps = []

    class FakeServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(cli, "make_server", lambda app, port: (apps.append(app), FakeServer())[1])
    monkeypatch.setattr(cli.PlayerApp, "scan_in_background", lambda self, on_done=None: None)
    monkeypatch.setattr(cli.PlayerApp, "keep_checking_for_updates", lambda self: None)
    url = DEFAULT_STATIONS[1]["url"]
    assert cli.main(["--no-browser", "--station", url]) == 0
    assert [(c["cmd"], c["hold"]) for c in apps[0].remote_commands] == [("station", True)]


def test_send_station_against_a_real_server(capsys):
    import threading
    from station_player.config import DEFAULT_STATIONS, ConfigStore
    from station_player.server import PlayerApp, make_server
    app = PlayerApp(ConfigStore())
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        assert cli._send_station(base, DEFAULT_STATIONS[0]["url"]) is False  # no window open
        assert len(app.remote_commands) == 1
        assert cli._send_station(base, "https://gone.example/") is False
        assert "isn't one of your stations any more" in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()


def test_station_player_link_starts_without_a_window(monkeypatch):
    shown = []
    monkeypatch.setattr(cli, "_running_instance", lambda url: True)
    monkeypatch.setattr(cli, "open_ui", lambda url, home, own_window=True: shown.append(url))
    assert cli.main(["station-player://start", "-psn_0_12345"]) == 0
    assert shown == []  # the page that asked is the window


def test_bundled_app_always_runs_as_an_app(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "bundled", lambda: True)
    monkeypatch.setattr(cli, "_log_to_file", lambda: seen.append("log"))
    monkeypatch.setattr(cli, "_running_instance", lambda url: True)
    monkeypatch.setattr(cli, "open_ui", lambda url, home, own_window=True: seen.append("show"))
    assert cli.main([]) == 0
    assert seen == ["log", "show"]
