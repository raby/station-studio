import json

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
    monkeypatch.setattr(cli, "make_server", lambda app, port: (_ for _ in ()).throw(OSError("stop")))

    rc = cli.main([str(exports), str(music), str(single), str(music / "a.wav"),
                   str(tmp_path / "missing"), "--no-browser"])
    assert rc == 1  # stopped at make_server, after setup
    assert len(scans) == 1

    saved = json.loads((tmp_path / "player-home" / "config.json").read_text())
    assert saved["playlist_sources"] == [str(exports.resolve()), str(single.resolve())]
    assert saved["music_folders"] == [str(music.resolve())]


def test_cli_registers_an_itunes_folder_as_both(tmp_path, monkeypatch):
    itunes = tmp_path / "iTunes"
    write_itunes_xml(itunes / "iTunes Library.xml", [], [])
    write_wav(itunes / "iTunes Media" / "Music" / "a.wav")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(cli, "make_server", lambda app, port: (_ for _ in ()).throw(OSError("stop")))
    cli.main([str(itunes), str(empty), "--no-browser"])
    saved = json.loads((tmp_path / "player-home" / "config.json").read_text())
    assert saved["playlist_sources"] == [str(itunes.resolve())]
    assert saved["music_folders"] == [str(itunes.resolve()), str(empty.resolve())]
