import pytest

from conftest import station_export, write_itunes_xml, write_wav
from station_server.playlist import PlaylistError, load_queue, now_playing


def test_keeps_station_order(tmp_path):
    files = [write_wav(tmp_path / "music" / f"{n}.wav") for n in ("c", "a", "b")]
    xml = station_export(tmp_path, files)
    queue = load_queue(xml)
    assert queue.name == "Rebirth Classics - Week 12"
    assert [t.path.name for t in queue.tracks] == ["c.wav", "a.wav", "b.wav"]
    assert queue.missing == []


def test_missing_tracks_are_listed_and_left_out(tmp_path):
    files = [write_wav(tmp_path / "music" / f"{n}.wav") for n in ("a", "b")]
    xml = station_export(tmp_path, files)
    files[0].unlink()
    queue = load_queue(xml)
    assert [t.path.name for t in queue.tracks] == ["b.wav"]
    assert [t.name for t in queue.missing] == ["Song 1"]


def test_moved_music_is_found_through_a_music_folder(tmp_path):
    old = write_wav(tmp_path / "old" / "Album" / "a.wav")
    xml = station_export(tmp_path, [old])
    new = tmp_path / "new" / "Album" / "a.wav"
    new.parent.mkdir(parents=True)
    old.rename(new)
    queue = load_queue(xml, music_folders=[str(tmp_path / "new")])
    assert queue.tracks[0].path == new


def test_remap_rewrites_the_library_path(tmp_path):
    old = write_wav(tmp_path / "mac" / "Music" / "a.wav")
    xml = station_export(tmp_path, [old])
    (tmp_path / "pc").mkdir()
    (tmp_path / "mac" / "Music").rename(tmp_path / "pc" / "Music")
    queue = load_queue(xml, remaps=[{"from": str(tmp_path / "mac"), "to": str(tmp_path / "pc")}])
    assert queue.tracks[0].path == tmp_path / "pc" / "Music" / "a.wav"


def test_several_playlists_need_a_choice(tmp_path):
    a = write_wav(tmp_path / "a.wav")
    track = {"Track ID": 1, "Name": "A", "Location": a.resolve().as_uri()}
    xml = write_itunes_xml(tmp_path / "lib.xml", [track], [
        {"Name": "Radio", "Playlist Items": [{"Track ID": 1}]},
        {"Name": "Classics", "Playlist Items": [{"Track ID": 1}]},
    ])
    with pytest.raises(PlaylistError, match="--playlist"):
        load_queue(xml)
    assert load_queue(xml, playlist="classics").name == "Classics"
    with pytest.raises(PlaylistError, match="it has: Classics, Radio"):
        load_queue(xml, playlist="Jazz")


def test_nothing_playable_is_an_error(tmp_path):
    a = write_wav(tmp_path / "a.wav")
    xml = station_export(tmp_path, [a])
    a.unlink()
    with pytest.raises(PlaylistError, match="could be found"):
        load_queue(xml)


def test_missing_xml(tmp_path):
    with pytest.raises(PlaylistError, match="not found"):
        load_queue(tmp_path / "nope.xml")


def test_now_playing_title(tmp_path):
    files = [write_wav(tmp_path / "a.wav")]
    queue = load_queue(station_export(tmp_path, files, titles=[("Inner City", "Good Life")]))
    assert now_playing(queue.tracks[0]) == "Inner City - Good Life"
    queue.tracks[0].artist = None
    assert now_playing(queue.tracks[0]) == "Good Life"
