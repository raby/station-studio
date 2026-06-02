"""Importer tests.

Build a synthetic iTunes plist at runtime, write it to a temp file, import it,
and verify the database state. No dependency on real iTunes exports.
"""
from __future__ import annotations

import plistlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, SourcePlaylist, SourcePlaylistEntry, Track
from engine.importer import import_source


# ---------- fixtures -----------------------------------------------------


@pytest.fixture
def db_session(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def make_itunes_plist(
    path: Path,
    playlist_name: str,
    tracks: list[dict],
) -> None:
    """Build a minimal iTunes XML plist and write it to `path`."""
    tracks_dict: dict[str, dict] = {}
    for t in tracks:
        tracks_dict[str(t["Track ID"])] = t
    data = {
        "Library Persistent ID": "0000000000000001",
        "Tracks": tracks_dict,
        "Playlists": [
            {
                "Name": playlist_name,
                "Playlist Items": [{"Track ID": t["Track ID"]} for t in tracks],
            }
        ],
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def track(track_id: int, pid: str, name: str, artist: str | None = None) -> dict:
    d: dict = {
        "Track ID": track_id,
        "Persistent ID": pid,
        "Name": name,
    }
    if artist:
        d["Artist"] = artist
    return d


# ---------- tests --------------------------------------------------------


def test_round_trip_imports_tracks_and_entries(db_session, tmp_path):
    xml_path = tmp_path / "sample.xml"
    make_itunes_plist(xml_path, "SAMPLE", [
        track(101, "AAAA0000000001AA", "One",   "Artist A"),
        track(102, "AAAA0000000002AA", "Two",   "Artist B"),
        track(103, "AAAA0000000003AA", "Three", "Artist C"),
    ])

    summary = import_source(db_session, xml_path, "SAMPLE")
    assert summary == {
        "name": "SAMPLE", "role": "content",
        "tracks_imported": 3, "entries_imported": 3,
    }

    sp = db_session.query(SourcePlaylist).filter_by(name="SAMPLE").one()
    assert sp.role == "content"  # default role
    entries = db_session.query(SourcePlaylistEntry).filter_by(source_playlist_id=sp.id).order_by(SourcePlaylistEntry.position).all()
    assert [e.position for e in entries] == [0, 1, 2]
    assert [e.track.name for e in entries] == ["One", "Two", "Three"]


def test_reimport_replaces_entries_but_reuses_tracks(db_session, tmp_path):
    """Re-importing the same pool with different entries must cleanly replace
    entries without duplicating the Track rows (dedup by Persistent ID)."""
    xml_path = tmp_path / "a.xml"
    make_itunes_plist(xml_path, "P", [
        track(1, "P0000000000000001", "One"),
        track(2, "P0000000000000002", "Two"),
    ])
    import_source(db_session, xml_path, "P")

    # Re-import with a third track added and one reordered
    xml_path_v2 = tmp_path / "a_v2.xml"
    make_itunes_plist(xml_path_v2, "P", [
        track(2, "P0000000000000002", "Two"),
        track(1, "P0000000000000001", "One"),
        track(3, "P0000000000000003", "Three"),
    ])
    import_source(db_session, xml_path_v2, "P")

    # Total Track rows: still only 3 unique persistent IDs
    assert db_session.query(Track).count() == 3

    # Only one SourcePlaylist named 'P'
    sps = db_session.query(SourcePlaylist).filter_by(name="P").all()
    assert len(sps) == 1

    entries = sorted(sps[0].entries, key=lambda e: e.position)
    assert [e.track.name for e in entries] == ["Two", "One", "Three"]


def test_tracks_shared_across_multiple_pools(db_session, tmp_path):
    """Two pools that share the same tracks (by Persistent ID) must reference
    the same Track rows, not duplicate them."""
    xml_a = tmp_path / "a.xml"
    xml_b = tmp_path / "b.xml"
    shared = [
        track(1, "SHARED001", "Shared One"),
        track(2, "SHARED002", "Shared Two"),
    ]
    make_itunes_plist(xml_a, "A", shared)
    make_itunes_plist(xml_b, "B", shared)

    import_source(db_session, xml_a, "A")
    import_source(db_session, xml_b, "B")

    # Two pools, two tracks total (not four)
    assert db_session.query(SourcePlaylist).count() == 2
    assert db_session.query(Track).count() == 2

    # Both pools have 2 entries referring to the same 2 tracks
    pools = {sp.name: sp for sp in db_session.query(SourcePlaylist).all()}
    a_track_ids = {e.track_id for e in pools["A"].entries}
    b_track_ids = {e.track_id for e in pools["B"].entries}
    assert a_track_ids == b_track_ids


# ---------- edge branches ----------------------------------------------


def test_datetime_fields_are_stored_with_type_marker(db_session, tmp_path):
    """plistlib produces datetime objects for <date> elements; the importer
    must wrap those in {'__type__': 'datetime', 'value': iso}."""
    xml_path = tmp_path / "dt.xml"
    dt = datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    make_itunes_plist(xml_path, "DT", [{
        "Track ID": 1, "Persistent ID": "DT000001",
        "Name": "With date", "Date Added": dt,
    }])
    import_source(db_session, xml_path, "DT")
    t = db_session.query(Track).one()
    assert t.itunes_metadata is not None
    stored = t.itunes_metadata["Date Added"]
    assert isinstance(stored, dict)
    assert stored["__type__"] == "datetime"
    assert stored["value"].startswith("2024-03-15")


def test_reimport_backfills_missing_itunes_track_id(db_session, tmp_path):
    """First-import-wins for itunes_track_id: if an existing Track has
    itunes_track_id=None, the next import must populate it."""
    # Seed a Track manually with itunes_track_id=None.
    existing = Track(
        persistent_id="BACK0001", itunes_track_id=None,
        name="Old Name", artist="Old",
    )
    db_session.add(existing)
    db_session.commit()

    # Import a plist that references the same Persistent ID with a Track ID.
    xml_path = tmp_path / "back.xml"
    make_itunes_plist(xml_path, "BACK", [
        track(555, "BACK0001", "New Name", "New"),
    ])
    import_source(db_session, xml_path, "BACK")

    db_session.refresh(existing)
    assert existing.itunes_track_id == 555
    assert existing.name == "New Name"      # mutable fields updated
    assert existing.artist == "New"


def test_playlist_item_referencing_unknown_track_id_is_skipped(
    db_session, tmp_path,
):
    """A Playlist Items entry pointing at a Track ID missing from the Tracks
    dict is silently skipped — it must not raise and must not create an entry."""
    xml_path = tmp_path / "skip.xml"
    # Hand-build the plist: 1 track, but Playlist Items references ID 999 too.
    data = {
        "Tracks": {
            "1": {"Track ID": 1, "Persistent ID": "SKIP001", "Name": "Only"},
        },
        "Playlists": [{
            "Name": "SKIP",
            "Playlist Items": [
                {"Track ID": 1},
                {"Track ID": 999},  # dangling — skipped by the importer
            ],
        }],
    }
    with open(xml_path, "wb") as f:
        plistlib.dump(data, f)

    summary = import_source(db_session, xml_path, "SKIP")
    assert summary["tracks_imported"] == 1
    assert summary["entries_imported"] == 1  # only the valid one


# ---------- §14.35 role parameter ---------------------------------------


def test_import_with_role_reference_persists_role_to_db(db_session, tmp_path):
    """Pools imported with role='reference' (e.g. weekly's MASTER) carry
    the role on SourcePlaylist.role. The engine never samples from
    them but the iTunes XML import path is identical."""
    xml_path = tmp_path / "master.xml"
    make_itunes_plist(xml_path, "MASTER", [
        track(1, "MAST000000000001A", "Track 1"),
        track(2, "MAST000000000002A", "Track 2"),
    ])
    summary = import_source(db_session, xml_path, "MASTER", role="reference")
    assert summary["role"] == "reference"
    assert summary["tracks_imported"] == 2
    assert summary["entries_imported"] == 2

    sp = db_session.query(SourcePlaylist).filter_by(name="MASTER").one()
    assert sp.role == "reference"


def test_import_with_invalid_role_raises(db_session, tmp_path):
    xml_path = tmp_path / "bad.xml"
    make_itunes_plist(xml_path, "BAD", [track(1, "BAD0000000000001A", "X")])
    with pytest.raises(ValueError, match="invalid role 'banana'"):
        import_source(db_session, xml_path, "BAD", role="banana")  # type: ignore[arg-type]


def test_reimport_can_change_role(db_session, tmp_path):
    """Re-importing the same pool name with a different role updates
    the role on the recreated SourcePlaylist row. (The existing
    idempotency drops + recreates the row; role flows through.)"""
    xml_path = tmp_path / "p.xml"
    make_itunes_plist(xml_path, "P", [track(1, "PPPP000000000001A", "X")])

    import_source(db_session, xml_path, "P", role="content")
    sp = db_session.query(SourcePlaylist).filter_by(name="P").one()
    assert sp.role == "content"

    # Re-import as reference — same name, different role.
    import_source(db_session, xml_path, "P", role="reference")
    sp = db_session.query(SourcePlaylist).filter_by(name="P").one()
    assert sp.role == "reference"


# ---------- §14.35 load_pools cross-check -------------------------------


def _minimal_template_dict(source_name: str, role: str = "content") -> dict:
    """Build a parse_template-valid template referencing a single source."""
    return {
        "station": "test",
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": source_name, "colour": "#aaa", "role": role}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {},
            "flat_slots": [
                {"source": source_name, "strategy": "weighted", "size": 1},
            ],
        },
    }


def test_load_pools_raises_when_db_pool_is_reference_but_template_says_content(
    db_session, tmp_path,
):
    """The dangerous-direction mismatch (§14.35). Engine would otherwise
    sample from a pool the importer explicitly marked not-for-sampling.

    Raises TemplateError specifically — distinct from the ValueError
    raised for missing-pool. Callers + API error mapping can
    distinguish "run the importer" from "fix the config drift".
    """
    from engine.persistence import load_pools
    from engine.template import parse_template, TemplateError

    xml_path = tmp_path / "pool.xml"
    make_itunes_plist(xml_path, "P", [track(1, "PPPP000000000099A", "T1")])
    # Import as reference …
    import_source(db_session, xml_path, "P", role="reference")
    # … but build a template that declares P as content (the dangerous
    # config drift).
    tmpl = parse_template(_minimal_template_dict("P", role="content"))

    with pytest.raises(TemplateError, match="role='reference'.*declares it as content"):
        load_pools(db_session, tmpl)


def test_load_pools_cross_check_fires_on_second_of_multiple_sources(
    db_session, tmp_path,
):
    """Multi-source variant: source A imports OK + content, source B
    is the dangerous-direction mismatch. Confirms the loop doesn't
    short-circuit on the first success or leave `pools` in a
    partial state."""
    from engine.persistence import load_pools
    from engine.template import parse_template, TemplateError

    a_path = tmp_path / "a.xml"
    make_itunes_plist(a_path, "A", [track(1, "AAAA000000000001A", "T1")])
    import_source(db_session, a_path, "A", role="content")

    b_path = tmp_path / "b.xml"
    make_itunes_plist(b_path, "B", [track(2, "BBBB000000000001A", "T2")])
    import_source(db_session, b_path, "B", role="reference")  # dangerous

    tmpl_dict = _minimal_template_dict("A", role="content")
    tmpl_dict["sources"].append({"name": "B", "colour": "#bbb", "role": "content"})
    tmpl_dict["layout"]["flat_slots"].append(
        {"source": "B", "strategy": "weighted", "size": 1},
    )
    tmpl = parse_template(tmpl_dict)

    with pytest.raises(TemplateError, match="'B'.*role='reference'"):
        load_pools(db_session, tmpl)


def test_load_pools_silent_when_db_pool_is_content_but_template_says_reference(
    db_session, tmp_path,
):
    """The benign-direction mismatch: template skips loading the pool
    (reference-role sources aren't in content_source_names), so the
    DB-side role is moot. load_pools returns the (empty) dict cleanly."""
    from engine.persistence import load_pools
    from engine.template import parse_template

    xml_path = tmp_path / "pool.xml"
    make_itunes_plist(xml_path, "P", [track(1, "PPPP00000000099BA", "T1")])
    import_source(db_session, xml_path, "P", role="content")
    # Template declares P as reference + uses no content sources. Need
    # a second source to satisfy parse_template (a layout with no
    # content blocks fails the empty-slots check).
    tmpl_dict = _minimal_template_dict("P", role="reference")
    # Swap the layout to use a different content source so P isn't referenced.
    tmpl_dict["sources"].append({"name": "Q", "colour": "#bbb", "role": "content"})
    tmpl_dict["layout"]["flat_slots"] = [
        {"source": "Q", "strategy": "weighted", "size": 1},
    ]
    # Q hasn't been imported — load_pools will raise on Q. But the
    # important thing is that it does NOT raise on the P role mismatch.
    # Easier test: just import Q too, then verify load_pools succeeds.
    q_path = tmp_path / "q.xml"
    make_itunes_plist(q_path, "Q", [track(2, "QQQQ000000000099A", "T1")])
    import_source(db_session, q_path, "Q", role="content")
    tmpl = parse_template(tmpl_dict)
    pools = load_pools(db_session, tmpl)
    # P is not in pools (template's content_source_names skips reference).
    assert "P" not in pools
    assert "Q" in pools


def test_load_pools_happy_path_when_roles_agree(db_session, tmp_path):
    """Sanity: roles aligned on both sides → load_pools just works."""
    from engine.persistence import load_pools
    from engine.template import parse_template

    xml_path = tmp_path / "p.xml"
    make_itunes_plist(xml_path, "P", [
        track(1, "PPPP000000000901A", "T1"),
        track(2, "PPPP000000000902A", "T2"),
    ])
    import_source(db_session, xml_path, "P", role="content")
    tmpl = parse_template(_minimal_template_dict("P", role="content"))
    pools = load_pools(db_session, tmpl)
    assert "P" in pools
    assert len(pools["P"]) == 2


# ---------- §16.13.t6 — validate_pool_name (hoisted from api/cli) ----------


class TestValidatePoolName:
    """`validate_pool_name` is the single source of truth across api/,
    station_studio/, and any future entry point. Pin the contract here
    so the API + CLI integration tests (which already grep for "empty
    or whitespace-only") can rely on the engine layer's behaviour."""

    def test_strips_surrounding_whitespace(self) -> None:
        from engine.importer import validate_pool_name
        assert validate_pool_name("  daytime  ") == "daytime"
        assert validate_pool_name("\tjingles\n") == "jingles"

    def test_passes_through_already_clean_names(self) -> None:
        from engine.importer import validate_pool_name
        assert validate_pool_name("DAYTIME") == "DAYTIME"
        # Internal whitespace is preserved — only surrounding is stripped.
        assert validate_pool_name("Night Owl") == "Night Owl"

    def test_rejects_empty_string(self) -> None:
        from engine.importer import validate_pool_name
        with pytest.raises(ValueError, match="empty or whitespace-only"):
            validate_pool_name("")

    def test_rejects_whitespace_only(self) -> None:
        """Whitespace-only inputs: spaces, tabs, newlines. All reject."""
        from engine.importer import validate_pool_name
        for w in ("   ", "\t", "\n", "  \t  \n"):
            with pytest.raises(ValueError, match="empty or whitespace-only"):
                validate_pool_name(w)

    def test_error_message_is_part_of_the_contract(self) -> None:
        """The exact error text is what both API and CLI surfaces grep
        for. Rephrasing breaks downstream tests; pin it here so any
        casual reword fails THIS test first (with a clear "you broke
        the contract" diagnostic)."""
        from engine.importer import validate_pool_name
        try:
            validate_pool_name("")
        except ValueError as e:
            assert str(e) == "name must not be empty or whitespace-only"
        else:
            raise AssertionError("expected ValueError")

    def test_rejects_none(self) -> None:
        """t6-review 🟡-2: a future caller passing None (JSON-optional
        field, default-None Click option) must hit ValueError — NOT
        AttributeError from `None.strip()`. Same error class + message
        so the contract callers catch ValueError against still works."""
        from engine.importer import validate_pool_name
        with pytest.raises(ValueError, match="empty or whitespace-only"):
            validate_pool_name(None)  # type: ignore[arg-type]
