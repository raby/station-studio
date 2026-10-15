from pathlib import Path

from conftest import write_itunes_xml, write_wav
from station_player.library import (
    Library, apply_remaps, location_to_path, title_from_filename,
)


def test_location_to_path_mac_and_percent_encoding():
    p = location_to_path("file:///Users/rob/Music/Some%20Artist/01%20Song%20%26%20Co.mp3")
    assert p == Path("/Users/rob/Music/Some Artist/01 Song & Co.mp3")


def test_location_to_path_windows_drive():
    p = location_to_path("file://localhost/C:/Users/rob/Music/a.mp3")
    assert p.as_posix() == "C:/Users/rob/Music/a.mp3"


def test_location_to_path_ignores_remote_urls():
    assert location_to_path("http://example.com/a.mp3") is None
    assert location_to_path("") is None


def test_apply_remaps_ignores_case_and_unicode_form():
    remaps = [{"from": "c:\\users\\rob\\music", "to": "D:/Music"}]
    assert apply_remaps(Path("C:/Users/Rob/Music/A/b.mp3"), remaps).as_posix() == "D:/Music/A/b.mp3"
    nfd = [{"from": "/Users/Rene\u0301/Music", "to": "/mnt/m"}]
    assert apply_remaps(Path("/Users/Ren\u00e9/Music/x.mp3"), nfd) == Path("/mnt/m/x.mp3")
    assert apply_remaps(Path("/Users/Rob/Music"), [{"from": "/users/rob/music", "to": "/m"}]) == Path("/m")


def test_apply_remaps_only_on_whole_segments():
    remaps = [{"from": "/Users/rob/Music", "to": "/mnt/music"}]
    assert apply_remaps(Path("/Users/rob/Music/a/b.mp3"), remaps) == Path("/mnt/music/a/b.mp3")
    assert apply_remaps(Path("/Users/rob/Musical/b.mp3"), remaps) == Path("/Users/rob/Musical/b.mp3")


def test_title_from_filename():
    assert title_from_filename(Path("01 - Artist Name - The Song.mp3")) == ("The Song", "Artist Name")
    assert title_from_filename(Path("07 Just A Title.flac")) == ("Just A Title", None)
    assert title_from_filename(Path("1999.mp3")) == ("1999", None)
    assert title_from_filename(Path("3. Third.mp3")) == ("Third", None)
    assert title_from_filename(Path("A2 - Side Two.mp3")) == ("Side Two", None)
    # Titles that merely start with a number keep it.
    assert title_from_filename(Path("99 Luftballons.mp3")) == ("99 Luftballons", None)
    assert title_from_filename(Path("7 Seconds.mp3")) == ("7 Seconds", None)
    assert title_from_filename(Path("2Pac - Changes.mp3")) == ("Changes", "2Pac")


def _track(tid, name, location=None, **extra):
    t = {"Track ID": tid, "Name": name, "Persistent ID": f"PID{tid:04d}", "Artist": "A", "Total Time": 1000}
    if location:
        t["Location"] = location
    t.update(extra)
    return t


def test_loads_station_studio_style_playlist_in_order(tmp_path):
    a = write_wav(tmp_path / "music" / "a.wav")
    b = write_wav(tmp_path / "music" / "b.wav")
    xml = write_itunes_xml(
        tmp_path / "exports" / "rebirth-radio" / "001.xml",
        [_track(1, "Song A", a.as_uri()), _track(2, "Song B", b.as_uri()), _track(3, "Gone", "file:///nope/c.mp3")],
        [{"Name": "Rebirth Radio 001", "Playlist Persistent ID": "X1",
          "Playlist Items": [{"Track ID": 2}, {"Track ID": 1}, {"Track ID": 3}, {"Track ID": 2}]}],
    )
    lib = Library()
    lib.load([str(xml.parent.parent)], [])
    [pl] = lib.playlist_summaries()
    assert pl["name"] == "Rebirth Radio 001"
    assert pl["count"] == 4 and pl["missing"] == 1
    detail = lib.playlist_detail(pl["id"])
    assert [t["name"] for t in detail["tracks"]] == ["Song B", "Song A", "Gone", "Song B"]
    assert [t["available"] for t in detail["tracks"]] == [True, True, False, True]


def test_skips_master_and_smart_kinds_and_folders(tmp_path):
    a = write_wav(tmp_path / "a.wav")
    xml = write_itunes_xml(tmp_path / "lib.xml", [_track(1, "S", a.as_uri())], [
        {"Name": "Library", "Master": True, "Playlist Items": [{"Track ID": 1}]},
        {"Name": "Music", "Distinguished Kind": 4, "Playlist Items": [{"Track ID": 1}]},
        {"Name": "My folder", "Folder": True, "Playlist Items": [{"Track ID": 1}]},
        {"Name": "Keepers", "Playlist Items": [{"Track ID": 1}]},
    ])
    lib = Library()
    lib.load([str(xml)], [])
    assert [p["name"] for p in lib.playlist_summaries()] == ["Keepers"]


def test_library_with_no_playlists_offers_all_tracks(tmp_path):
    a = write_wav(tmp_path / "a.wav")
    xml = write_itunes_xml(tmp_path / "whole.xml", [_track(1, "S", a.as_uri())], [])
    lib = Library()
    lib.load([str(xml)], [])
    [pl] = lib.playlist_summaries()
    assert pl["name"] == "whole" and pl["count"] == 1


def test_moved_files_found_by_remap_then_by_filename(tmp_path):
    remapped = write_wav(tmp_path / "pc" / "Music" / "x.wav")
    by_name = write_wav(tmp_path / "elsewhere" / "deep" / "y.wav")
    xml = write_itunes_xml(tmp_path / "p.xml", [
        _track(1, "X", "file:///Users/rob/Music/x.wav"),
        _track(2, "Y", "file:///Users/rob/Old/y.wav"),
    ], [{"Name": "P", "Playlist Items": [{"Track ID": 1}, {"Track ID": 2}]}])
    lib = Library()
    lib.load([str(xml)], [str(tmp_path / "elsewhere")],
             [{"from": "/Users/rob/Music", "to": str(tmp_path / "pc" / "Music")}])
    paths = {t.name: t.path for t in lib.tracks.values()}
    assert paths["X"] == remapped
    assert paths["Y"] == by_name


def test_music_folder_becomes_playlist(tmp_path):
    folder = tmp_path / "Albums"
    write_wav(folder / "Disc" / "02 - Band - Second.wav")
    write_wav(folder / "Disc" / "01 - Band - First.wav")
    (folder / "notes.txt").write_text("not audio")
    (folder / "._hidden.wav").write_bytes(b"")
    lib = Library()
    lib.load([], [str(folder)])
    [pl] = lib.playlist_summaries()
    assert pl["kind"] == "folder" and pl["name"] == "Albums"
    tracks = lib.playlist_detail(pl["id"])["tracks"]
    assert [(t["name"], t["artist"], t["album"]) for t in tracks] == [
        ("First", "Band", "Disc"), ("Second", "Band", "Disc")]


def test_bad_sources_become_errors_not_crashes(tmp_path):
    junk = tmp_path / "junk.xml"
    junk.write_text("<not a plist")
    lib = Library()
    lib.load([str(junk), str(tmp_path / "missing.xml")], [str(tmp_path / "nofolder")])
    assert len(lib.errors) == 3
    assert lib.playlist_summaries() == []


def test_reads_bundled_station_studio_starter_pool():
    xml = Path(__file__).resolve().parents[2] / "stations" / "starter-content.itunes.xml"
    lib = Library()
    lib.load([str(xml)], [])
    [pl] = lib.playlist_summaries()
    assert pl["count"] == 50
    assert pl["missing"] == 50  # the starter pool is synthetic: no audio files


def test_moved_file_namesakes_are_disambiguated_by_folder_or_left_missing(tmp_path):
    a = write_wav(tmp_path / "lib" / "Album A" / "01 Intro.wav")
    write_wav(tmp_path / "lib" / "Album B" / "01 Intro.wav")
    xml = write_itunes_xml(tmp_path / "p.xml", [
        _track(1, "A intro", "file:///old/Album%20A/01%20Intro.wav"),
        _track(2, "Unknown intro", "file:///old/Album%20C/01%20Intro.wav"),
    ], [{"Name": "P", "Playlist Items": [{"Track ID": 1}, {"Track ID": 2}]}])
    lib = Library()
    lib.load([str(xml)], [str(tmp_path / "lib")])
    paths = {t.name: t.path for t in lib.tracks.values() if t.name.endswith("intro")}
    assert paths["A intro"] == a
    assert paths["Unknown intro"] is None


def test_wrongly_shaped_xml_is_reported_not_fatal(tmp_path):
    import plistlib
    good_audio = write_wav(tmp_path / "a.wav")
    bad_tracks = tmp_path / "bad_tracks.xml"
    bad_tracks.write_bytes(plistlib.dumps({"Tracks": ["not", "a", "dict"]}))
    odd_items = tmp_path / "odd_items.xml"
    odd_items.write_bytes(plistlib.dumps({
        "Tracks": {"1": {"Track ID": 1, "Name": 1999, "Album": 7, "Total Time": "long",
                         "Location": good_audio.as_uri()}},
        "Playlists": [
            {"Name": "Strings", "Playlist Items": "oops"},
            {"Name": "Mixed", "Playlist Items": ["x", {"Track ID": 1}, {"Track ID": [1]}]},
        ],
    }))
    lib = Library()
    lib.load([str(bad_tracks), str(odd_items)], [])
    assert lib.errors == ["bad_tracks.xml is not an iTunes library XML"]
    [pl] = lib.playlist_summaries()
    detail = lib.playlist_detail(pl["id"])
    assert pl["name"] == "Mixed"
    assert [(t["name"], t["album"], t["duration_ms"]) for t in detail["tracks"]] == [("1999", "7", None)]

    # The same files found by scanning a folder are skipped quietly.
    lib = Library()
    lib.load([str(tmp_path)], [])
    assert lib.errors == []


def test_overlapping_music_folders_still_find_moved_files(tmp_path):
    moved = write_wav(tmp_path / "Music" / "Albums" / "Song.wav")
    xml = write_itunes_xml(tmp_path / "p.xml", [_track(1, "S", "file:///old/place/Song.wav")],
                           [{"Name": "P", "Playlist Items": [{"Track ID": 1}]}])
    lib = Library()
    lib.load([str(xml)], [str(tmp_path / "Music"), str(tmp_path / "Music" / "Albums")])
    [track] = [t for t in lib.tracks.values() if t.name == "S"]
    assert track.path == moved


def test_only_audio_locations_are_playable(tmp_path):
    secret = tmp_path / "id_rsa"
    secret.write_text("KEY")
    page = tmp_path / "x.html"
    page.write_text("<script>")
    xml = write_itunes_xml(tmp_path / "p.xml", [
        _track(1, "Key", secret.as_uri()),
        _track(2, "Page", page.as_uri()),
    ], [{"Name": "P", "Playlist Items": [{"Track ID": 1}, {"Track ID": 2}]}])
    lib = Library()
    lib.load([str(xml)], [str(tmp_path)])
    assert all(t.path is None for t in lib.tracks.values())


def test_moved_file_found_despite_case_and_unicode_form(tmp_path):
    moved = write_wav(tmp_path / "lib" / "Ren\u00e9" / "Caf\u00e9.wav")
    xml = write_itunes_xml(tmp_path / "p.xml", [
        _track(1, "C", "file:///old/RENE%CC%81/Cafe%CC%81.WAV"),
    ], [{"Name": "P", "Playlist Items": [{"Track ID": 1}]}])
    lib = Library()
    lib.load([str(xml)], [str(tmp_path / "lib")])
    [track] = [t for t in lib.tracks.values() if t.name == "C"]
    assert track.path == moved
