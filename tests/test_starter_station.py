"""§16.14 c1 — pin the bundled starter station.

The starter is what every new contributor / hobbyist reads first.
These tests pin the contract so a future template tweak (typo, slot-
count adjustment, jingle-cadence change) is caught immediately:

  * starter.json parses + reports 168 slots/week
  * The synthetic pools import cleanly via engine.importer
  * End-to-end generate produces exactly 168 picks (weighted +
    jingle interleaved at the documented 1-jingle-per-8-slot cadence)
  * The pool XMLs are byte-identical to what
    `scripts/generate_starter_pools.py` produces — catches manual
    edits drifting away from the generator's deterministic output
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


REPO_ROOT = Path(__file__).resolve().parent.parent
STARTER_JSON = REPO_ROOT / "stations" / "starter.json"
STARTER_CONTENT_XML = REPO_ROOT / "stations" / "starter-content.itunes.xml"
STARTER_JINGLES_XML = REPO_ROOT / "stations" / "starter-jingles.itunes.xml"
STARTER_REFERENCE_XML = REPO_ROOT / "stations" / "starter-reference.itunes.xml"
STARTER_SIDECAR = REPO_ROOT / "stations" / "starter.editor.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- template ----------

def test_starter_template_parses_with_168_slots() -> None:
    """Per §16.14 D3: 168 slots/week, weekly cadence, flat_slots form."""
    from engine.template import parse_template

    raw = json.loads(STARTER_JSON.read_text())
    tmpl = parse_template(raw)
    assert tmpl.station == "starter"
    assert tmpl.cadence == "weekly"
    assert tmpl.slots_per_week() == 168
    assert tmpl.cursor.persist is False  # D2: stateless


def test_starter_template_strategy_mix_matches_design() -> None:
    """Per §16.14 D1: only `weighted` + `jingle` appear. Pins the
    "two-strategy" budget and catches a future strategy creep."""
    raw = json.loads(STARTER_JSON.read_text())
    strategies = {s["strategy"] for s in raw["layout"]["flat_slots"]}
    assert strategies == {"weighted", "jingle"}, (
        f"starter should only use weighted+jingle; got {strategies}"
    )


def test_starter_template_jingle_cadence_is_every_8th_slot() -> None:
    """Per §16.14 D1 + the 168/8 math: 21 jingles, 147 weighted,
    jingle at positions 0, 8, 16, ... 160."""
    raw = json.loads(STARTER_JSON.read_text())
    flat = raw["layout"]["flat_slots"]
    jingle_positions = [
        i for i, s in enumerate(flat) if s["strategy"] == "jingle"
    ]
    assert len(jingle_positions) == 21
    assert jingle_positions == list(range(0, 168, 8))


# ---------- pools ----------

def test_starter_content_pool_has_50_tracks_10_artists() -> None:
    """Per §16.14 D4: STARTER_CONTENT is 50 tracks across 10 artists
    (5 each). Variety target so weighted artist-dedup has room to
    breathe."""
    import plistlib

    data = plistlib.loads(STARTER_CONTENT_XML.read_bytes())
    tracks = list(data["Tracks"].values())
    assert len(tracks) == 50
    assert len({t["Artist"] for t in tracks}) == 10
    # 5 tracks per artist:
    from collections import Counter
    by_artist = Counter(t["Artist"] for t in tracks)
    assert all(c == 5 for c in by_artist.values()), by_artist


def test_starter_jingles_pool_is_genre_jingle() -> None:
    """Per §16.14 D4: STARTER_JINGLES has 5 tracks, all Genre=Jingle.
    The jingle strategy filters by exactly this genre string."""
    import plistlib

    data = plistlib.loads(STARTER_JINGLES_XML.read_bytes())
    tracks = list(data["Tracks"].values())
    assert len(tracks) == 5
    assert {t["Genre"] for t in tracks} == {"Jingle"}


# ---------- generator determinism ----------

def test_starter_pools_match_generator_output() -> None:
    """Per §16.14 D5: committed XMLs are deterministically regenerated
    by `scripts/generate_starter_pools.py`. Running the generator
    produces byte-identical files. If this test fails, either:
      (a) someone hand-edited the XMLs and they drifted from the
          generator — run `make refresh-starter-pools` to reset, or
      (b) the generator changed and the committed XMLs are stale —
          run `make refresh-starter-pools` to bring them up to date.
    Either way, the action is the same: run the Make target.

    c1-review 🟡-1: generator switched to plistlib.dumps so the
    output is bytes (escaped against unsafe chars in track titles)
    — compare bytes vs bytes.
    """
    from scripts.generate_starter_pools import (
        build_content_pool, build_jingles_pool,
    )

    assert build_content_pool() == STARTER_CONTENT_XML.read_bytes(), (
        "starter-content.itunes.xml diverged from the generator — "
        "run `make refresh-starter-pools`"
    )
    assert build_jingles_pool() == STARTER_JINGLES_XML.read_bytes(), (
        "starter-jingles.itunes.xml diverged from the generator — "
        "run `make refresh-starter-pools`"
    )


def test_starter_pool_durations_vary_per_track() -> None:
    """c1-review 🟢-1: variance pin. Pre-fix all 55 tracks had
    `Total Time = 210000` (3:30 flat) which looked unintentional in
    the XML diff. Now each track's duration is keyed off `tid % 7`,
    giving 7 distinct duration buckets across the pool. Engine
    doesn't care; this just keeps the synthetic data looking like
    a real library."""
    import plistlib

    data = plistlib.loads(STARTER_CONTENT_XML.read_bytes())
    durations = {t["Total Time"] for t in data["Tracks"].values()}
    assert len(durations) == 7, (
        f"expected 7 distinct durations (tid % 7 buckets); got "
        f"{sorted(durations)}"
    )


# ---------- editor sidecar ----------

def test_starter_sidecar_committed_and_well_formed() -> None:
    """Per §16.14 D8: the sidecar lives next to the template so the
    visualiser is "interesting on first paint." Pin that it's
    committed + parseable via the editor_metadata parser + describes
    the documented section/macro counts."""
    from engine.editor_metadata import parse_sidecar, validate_sidecar

    assert STARTER_SIDECAR.is_file(), "sidecar must be committed"
    raw_template = json.loads(STARTER_JSON.read_text())
    sidecar = parse_sidecar(STARTER_SIDECAR, station_template=raw_template)
    # Doesn't raise on a valid sidecar/template pair.
    validate_sidecar(sidecar, station_template=raw_template)
    # `derive_sidecar` reports "1 sections, 21 macros" for the starter.
    # Macros are top-level (one per detected weighted run between
    # jingles), blocks are atomic (jingle) + macro-ref (weighted run).
    assert len(sidecar.get("sections", [])) >= 1
    assert len(sidecar.get("macros", [])) == 21, (
        f"expected 21 macros (one per weighted run between jingles), "
        f"got {len(sidecar.get('macros', []))}"
    )


def test_starter_sidecar_atomic_blocks_at_jingle_positions() -> None:
    """c1-review 🟡-2: pin POSITIONS, not just counts. The sidecar's
    atomic blocks must point at slot_indices [0, 8, 16, ... 160] —
    exactly the jingle positions in starter.json's flat_slots. A
    future engine bug misaligning the macro→slot binding would slip
    through a count-only check but fail here."""
    from engine.editor_metadata import parse_sidecar

    raw_template = json.loads(STARTER_JSON.read_text())
    sidecar = parse_sidecar(STARTER_SIDECAR, station_template=raw_template)

    blocks = sidecar["sections"][0]["blocks"]
    atomic_indices = [
        b["slot_index"] for b in blocks if b["kind"] == "atomic"
    ]
    assert atomic_indices == list(range(0, 168, 8)), (
        f"atomic blocks must hit every 8th slot starting at 0; got "
        f"{atomic_indices}"
    )

    # Belt-and-suspenders: blocks alternate atomic → macro-ref pairs
    # (21 atomic + 21 macro = 42), preserving template-slot order.
    kinds = [b["kind"] for b in blocks]
    assert kinds == ["atomic", "macro"] * 21, (
        f"sidecar blocks should alternate atomic→macro 21 times; got "
        f"{kinds[:6]}..."
    )


# ---------- end-to-end generate ----------

def test_starter_station_generates_168_picks_end_to_end() -> None:
    """Per §16.14 D6 (auto-import in c2 + manual import in tutorial):
    import the synthetic pools, seed the template, generate week 1 —
    168 picks land. End-to-end pin against grammar / pool / engine
    drift."""
    from engine.importer import import_source
    from engine.models import Base
    from engine.persistence import generate_chain
    from engine.seeding import seed_station

    with tempfile.TemporaryDirectory() as tmp:
        db_url = f"sqlite:///{tmp}/starter.db"
        eng = create_engine(db_url)
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng)
        with Session() as db:
            import_source(
                db, STARTER_CONTENT_XML, "STARTER_CONTENT", role="content",
            )
            import_source(
                db, STARTER_JINGLES_XML, "STARTER_JINGLES", role="content",
            )
            # §16.14.t5 c2: seed_station now sets the
            # cross_pool_dedup_sources=["REFERENCE_ARCHIVE"] rule on
            # the starter station, so REFERENCE_ARCHIVE must be
            # imported before generation (otherwise
            # load_excluded_track_pids fails loud per D5).
            import_source(
                db, STARTER_REFERENCE_XML, "REFERENCE_ARCHIVE",
                role="reference",
            )
            seed_station(db, STARTER_JSON)
            db.commit()

            picks_by_week = generate_chain(
                db, station_slug="starter", weeks=[1], seed_base=42,
            )

        assert len(picks_by_week) == 1
        assert len(picks_by_week[1]) == 168


def test_starter_station_jingle_positions_match_template() -> None:
    """The 21 jingle positions in the generated playlist must match
    the template's jingle-strategy slot positions (0, 8, 16, ...).
    Catches a future engine bug that misaligns flat_slots ordering."""
    from engine.importer import import_source
    from engine.models import Base, PlaylistEntry, Playlist, Track
    from engine.persistence import generate_chain
    from engine.seeding import seed_station

    with tempfile.TemporaryDirectory() as tmp:
        db_url = f"sqlite:///{tmp}/starter.db"
        eng = create_engine(db_url)
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng)
        with Session() as db:
            import_source(
                db, STARTER_CONTENT_XML, "STARTER_CONTENT", role="content",
            )
            import_source(
                db, STARTER_JINGLES_XML, "STARTER_JINGLES", role="content",
            )
            # §16.14.t5 c2: REFERENCE_ARCHIVE required by the auto-
            # seeded cross_pool_dedup_sources rule (see end-to-end
            # demo test below).
            import_source(
                db, STARTER_REFERENCE_XML, "REFERENCE_ARCHIVE",
                role="reference",
            )
            seed_station(db, STARTER_JSON)
            db.commit()

            generate_chain(
                db, station_slug="starter", weeks=[1], seed_base=42,
            )

            playlist = db.query(Playlist).one()
            entries = (
                db.query(PlaylistEntry)
                .filter_by(playlist_id=playlist.id)
                .order_by(PlaylistEntry.position)
                .all()
            )

        # Position is 1-indexed in PlaylistEntry; slot 0 in the
        # template is position 1 in the playlist.
        jingle_entries = [e for e in entries if e.slot_type == "jingle"]
        assert len(jingle_entries) == 21
        jingle_positions = {e.position for e in jingle_entries}
        expected = {i + 1 for i in range(0, 168, 8)}
        assert jingle_positions == expected


# ---------- §16.14.t3 — REFERENCE_ARCHIVE pool + cross-check pin ----------


def test_starter_declares_reference_archive_with_role_reference() -> None:
    """§16.14.t3 D1 lock: the starter template carries a
    REFERENCE_ARCHIVE source with role: reference. The pool is the
    teaching annotation — declared but unused in any slot block.
    Engine `content_source_names` MUST exclude it (the load_pools
    short-circuit that makes "reference pool" meaningful)."""
    from engine.template import parse_template

    raw = json.loads(STARTER_JSON.read_text())
    sources_by_name = {s["name"]: s for s in raw["sources"]}
    assert "REFERENCE_ARCHIVE" in sources_by_name
    assert sources_by_name["REFERENCE_ARCHIVE"]["role"] == "reference"

    tmpl = parse_template(raw)
    # The engine's content-source enumeration must NOT include
    # REFERENCE_ARCHIVE — otherwise it'd be loaded + sampled like
    # any content pool, defeating the role's whole purpose.
    assert "REFERENCE_ARCHIVE" not in tmpl.content_source_names
    # But the content pools ARE in there (sanity). content_source_names
    # is a list (preserves order); compare via set.
    assert set(tmpl.content_source_names) == {
        "STARTER_CONTENT", "STARTER_JINGLES",
    }


def test_reference_pool_has_9_archive_plus_3_content_echoes() -> None:
    """§16.14.t3 D2 + §16.14.t5 c2 lock: 12 tracks total = 9 archive-
    only (3 archive artists × 3 titles each, Genre=Archive) PLUS 3
    content echoes (byte-identical copies of STARTER_CONTENT entries
    at counters 1/25/50, Genre=Pop, PIDs in the STCONT namespace).

    Two-genre pin is intentional: the demo's whole point is that the
    REFERENCE_ARCHIVE pool can share PIDs with STARTER_CONTENT, and
    the c2 integration test below proves those 3 PIDs get excluded
    from the weighted strategy's pick set when the starter station's
    `cross_pool_dedup_sources` rule is set.
    """
    import plistlib

    from scripts.generate_starter_pools import CONTENT_ECHOED_PERSISTENT_IDS

    starter_ref_xml = (
        REPO_ROOT / "stations" / "starter-reference.itunes.xml"
    )
    data = plistlib.loads(starter_ref_xml.read_bytes())
    tracks = data["Tracks"]
    # Total = 9 archive-only + 3 content-echoes
    assert len(tracks) == 12

    by_genre: dict[str, list] = {}
    for t in tracks.values():
        by_genre.setdefault(t["Genre"], []).append(t)
    # Archive-only subset matches the original §16.14.t3 contract.
    assert len(by_genre["Archive"]) == 9
    archive_artists = {t["Artist"] for t in by_genre["Archive"]}
    assert len(archive_artists) == 3

    # Content-echo subset: 3 tracks, Genre=Pop, PIDs in STCONT namespace
    # and listed in scripts.generate_starter_pools.CONTENT_ECHOED_PERSISTENT_IDS.
    assert len(by_genre["Pop"]) == 3
    echo_pids = {t["Persistent ID"] for t in by_genre["Pop"]}
    assert echo_pids == set(CONTENT_ECHOED_PERSISTENT_IDS)
    assert all(p.startswith("STCONT") for p in echo_pids)


def test_template_parse_rejects_content_block_pointing_at_reference_source() -> None:
    """§16.14.t3 D4a (c1-review 🟡-2 cleaner shape, first guard): the
    engine has TWO cross-check guards on the reference role. The
    FIRST fires at `parse_template()` time: a slot block can't
    reference a source whose `role: reference` is declared in the
    template's own `sources[]`. This is the user-facing-first guard
    — the c2 docs page will surface this as the safety rail most
    hobbyists actually hit.

    Reproduces the hobbyist mistake the c2 docs will warn against:
    take the canonical starter template (which declares
    REFERENCE_ARCHIVE with role: reference) and edit ONE slot block
    to point a `weighted` content strategy at it. The parser
    refuses, no DB needed.

    Loose error match (c1-review 🟢-1): `match="reference"` instead
    of the precise message phrasing.
    """
    from engine.template import TemplateError, parse_template

    raw = json.loads(STARTER_JSON.read_text())
    # The hobbyist mistake: edit one slot to source from
    # REFERENCE_ARCHIVE with a content sampler.
    raw["layout"]["flat_slots"][0] = {
        "source": "REFERENCE_ARCHIVE",
        "strategy": "weighted",
        "size": 1,
    }
    with pytest.raises(TemplateError, match="reference"):
        parse_template(raw)


def test_engine_load_pools_rejects_db_role_mismatch(tmp_path) -> None:
    """§16.14.t3 D4b: the SECOND guard fires at generation time, when
    `load_pools` cross-checks the template's declared source roles
    against the actual DB rows from the importer.

    Specifically: if the template declares STARTER_CONTENT as content
    (and uses it in slot blocks) but someone re-imported the pool
    with role=reference, the engine refuses to load — even though
    the template ITSELF is valid. This catches the case where the
    user fixed their template after a typo-then-import but didn't
    re-import the pool with the corrected role.

    Pinned separately from the parse-time guard because the two
    safety rails fire on DIFFERENT failure modes:
      * parse_template: template internally inconsistent.
      * load_pools: template fine, DB row from import mismatches.

    Loose error match (c1-review 🟢-1): `match="(?i)reference"`.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from engine.importer import import_source
    from engine.models import Base
    from engine.persistence import load_pools
    from engine.template import TemplateError, parse_template

    eng = create_engine(f"sqlite:///{tmp_path}/x.db")
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    db = Session()
    try:
        # Force the DB-vs-template mismatch: import STARTER_CONTENT
        # with role=reference even though the template declares it
        # content. The template itself is unedited (and valid).
        import_source(
            db,
            REPO_ROOT / "stations" / "starter-content.itunes.xml",
            "STARTER_CONTENT",
            role="reference",
        )
        db.commit()

        raw = json.loads(STARTER_JSON.read_text())
        tmpl = parse_template(raw)
        with pytest.raises(TemplateError, match="(?i)reference"):
            load_pools(db, tmpl)
    finally:
        db.close()


def test_starter_sidecar_stays_stable_after_t3_amendment() -> None:
    """c1-review 🟢-2: declared-but-unused sources must NOT cause the
    sidecar to drift. The canonical regen-match pin lives in
    `tests/test_committed_sidecars.py::
    test_starter_committed_sidecar_matches_derive_output` — that
    test passing after §16.14.t3 amended `starter.json` is the real
    proof. This test makes the implicit-pin EXPLICIT so a future
    reader of test_starter_station.py finds the connection without
    grepping. Re-runs the derive helper inline + asserts the
    committed file matches.
    """
    from scripts.derive_sidecar import derive_sidecar

    raw = json.loads(STARTER_JSON.read_text())
    derived = (
        json.dumps(derive_sidecar(raw), indent=2, sort_keys=False) + "\n"
    )
    assert derived == STARTER_SIDECAR.read_text(), (
        "starter.editor.json drifted after a starter.json edit — "
        "the §16.14.t3 amendment added a declared-but-unused source "
        "(REFERENCE_ARCHIVE), which the derive helper should ignore. "
        "If this test fails, the derive helper now considers "
        "declared-but-unused sources somehow; surface that in DESIGN "
        "or re-run `python -m scripts.derive_sidecar` + commit."
    )


# ---------- §16.14.t5 c2 — cross-pool dedup end-to-end demo ----------


def test_starter_seed_sets_cross_pool_dedup_sources_rule() -> None:
    """§16.14.t5 c2 + D8: seeding the starter station imperatively
    sets `cross_pool_dedup_sources=["REFERENCE_ARCHIVE"]` via
    `engine.seeding._STATION_LIST_RULES`. Pin so a future refactor
    that drops the rule-seeding logic gets caught."""
    from engine.models import Base
    from engine.rules import get_rule_list_value
    from engine.models import Station
    from engine.seeding import seed_station

    with tempfile.TemporaryDirectory() as tmp:
        db_url = f"sqlite:///{tmp}/starter.db"
        eng = create_engine(db_url)
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng)
        with Session() as db:
            seed_station(db, STARTER_JSON)
            db.commit()
            station = db.query(Station).filter_by(slug="starter").one()
            assert get_rule_list_value(
                db, station_id=station.id,
                key="cross_pool_dedup_sources",
            ) == ["REFERENCE_ARCHIVE"]


def test_starter_generation_excludes_content_echo_pids_end_to_end() -> None:
    """§16.14.t5 c2 end-to-end demo: with the starter pools imported
    + the cross_pool_dedup_sources rule auto-seeded, the 3 STCONT
    PIDs that REFERENCE_ARCHIVE echoes (counters 1, 25, 50) must
    NEVER appear in the generated weekly playlist.

    Proves the full §16.14.t5 wiring: list-rule seeding (c2) →
    load_excluded_track_pids (c1) → apply_weighted exclusion (c1) →
    persisted PlaylistEntry rows. Catches any future regression
    along the chain.
    """
    from engine.importer import import_source
    from engine.models import (
        Base, Playlist, PlaylistEntry, Station, Track,
    )
    from engine.persistence import generate_chain
    from engine.seeding import seed_station

    from scripts.generate_starter_pools import (
        CONTENT_ECHOED_PERSISTENT_IDS,
    )

    with tempfile.TemporaryDirectory() as tmp:
        db_url = f"sqlite:///{tmp}/starter.db"
        eng = create_engine(db_url)
        Base.metadata.create_all(eng)
        Session = sessionmaker(bind=eng)
        with Session() as db:
            # Auto-import order matches engine.seeding._FIRST_BOOT_POOLS:
            # content + jingles + reference. The reference XML's content
            # echoes reuse the Track rows already created by STARTER_CONTENT
            # (importer dedups by Persistent ID).
            import_source(
                db, STARTER_CONTENT_XML, "STARTER_CONTENT", role="content",
            )
            import_source(
                db, STARTER_JINGLES_XML, "STARTER_JINGLES", role="content",
            )
            import_source(
                db, STARTER_REFERENCE_XML, "REFERENCE_ARCHIVE",
                role="reference",
            )
            seed_station(db, STARTER_JSON)
            db.commit()

            # Sanity: starter station has the rule set + the 3 echoed
            # Track rows are reachable from REFERENCE_ARCHIVE (importer
            # didn't accidentally drop them).
            station = db.query(Station).filter_by(slug="starter").one()
            echoed_tracks = (
                db.query(Track)
                .filter(Track.persistent_id.in_(CONTENT_ECHOED_PERSISTENT_IDS))
                .all()
            )
            assert len(echoed_tracks) == 3

            generate_chain(
                db, station_slug="starter", weeks=[1], seed_base=42,
            )

            playlist = db.query(Playlist).filter_by(
                station_id=station.id, week=1,
            ).one()
            entries = (
                db.query(PlaylistEntry)
                .filter_by(playlist_id=playlist.id)
                .all()
            )
            picked_pids = {
                e.track.persistent_id for e in entries
                if e.track is not None
            }

        # The whole point of the demo: NONE of the 3 echoed PIDs
        # made it into the weighted picks.
        overlap = picked_pids & set(CONTENT_ECHOED_PERSISTENT_IDS)
        assert overlap == set(), (
            f"cross_pool_dedup_sources did not exclude the echoed PIDs; "
            f"overlap = {sorted(overlap)}"
        )
        # And the playlist isn't empty — the rule isn't accidentally
        # excluding the entire weighted pool.
        assert len(entries) == 168
