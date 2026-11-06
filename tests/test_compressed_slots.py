"""`PlaylistVersion.slots` stored compressed (engine.compressed_json),
read back the same, older plain rows still read, and the migration's
downgrade unpacking every row for older code."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from engine.compressed_json import PREFIX, pack, unpack
from engine.database import make_engine, make_sessionmaker
from engine.models import Playlist, PlaylistVersion, Station

ROOT = Path(__file__).resolve().parent.parent
SLOTS = [
    {"slot": i, "track_id": i, "track_name": f"Söng {i}", "reason": "weighted",
     "excluded": [{"pool_index": j, "track_name": "Ü", "reason": "artist played"} for j in range(40)]}
    for i in range(1, 30)
]


def test_pack_round_trips_and_is_small():
    packed = pack(SLOTS)
    assert packed.startswith(PREFIX)
    assert unpack(packed) == SLOTS
    assert len(packed) * 10 < len(json.dumps(SLOTS))
    # Values written before (plain JSON) come back as they are.
    assert unpack(SLOTS) == SLOTS and unpack(None) is None and unpack("text") == "text"


def _alembic(db_url: str, *args: str) -> None:
    env = {**os.environ, "STATION_STUDIO_DB_URL": db_url}
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                   check=True, capture_output=True)


def test_stored_compressed_old_rows_read_and_downgrade_unpacks(tmp_path):
    db_path = tmp_path / "m.db"
    url = f"sqlite:///{db_path}"
    _alembic(url, "upgrade", "head")
    Session = make_sessionmaker(make_engine(url))
    with Session() as db:
        st = Station(slug="m", name="m", template={})
        db.add(st)
        db.flush()
        p = Playlist(station_id=st.id, week=1, trace_id="t1")
        db.add(p)
        db.flush()
        for n in (1, 2):
            db.add(PlaylistVersion(
                playlist_id=p.id, version_no=n, trace_id=f"t{n}", station_slug="m",
                week=1, rng="off", generated_at="x", slots=SLOTS,
            ))
        db.commit()
    con = sqlite3.connect(db_path)
    raw = [r[0] for r in con.execute("SELECT slots FROM playlist_version ORDER BY id")]
    assert all(json.loads(r).startswith(PREFIX) for r in raw)
    # A row as written before: plain JSON.
    con.execute("UPDATE playlist_version SET slots = ? WHERE version_no = 1", (json.dumps(SLOTS),))
    con.commit()
    con.close()
    with Session() as db:
        assert [v.slots for v in db.query(PlaylistVersion).order_by(PlaylistVersion.id)] == [SLOTS, SLOTS]
        # Listing a week's versions doesn't load their songs.
        v = db.query(PlaylistVersion).first()
        assert "slots" not in v.__dict__
    make_engine(url).dispose()

    _alembic(url, "downgrade", "-1")
    con = sqlite3.connect(db_path)
    assert [json.loads(r[0]) for r in con.execute("SELECT slots FROM playlist_version")] == [SLOTS, SLOTS]
    con.close()
