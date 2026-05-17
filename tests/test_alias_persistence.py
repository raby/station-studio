"""Persistence round-trip for the §14.26 ArtistAlias table.

Verifies that DB rows go in and come back out the other side in the
shape `engine.artist_match.resolve_artist_keys` consumes (lowercase
keys + canonical self-mapping).
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from engine.artist_match import resolve_artist_keys
from engine.database import make_engine, make_sessionmaker
from engine.models import ArtistAlias, Base
from engine.persistence import load_alias_map


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker:
    engine = make_engine(f"sqlite:///{tmp_path}/alias.db")
    Base.metadata.create_all(engine)
    return make_sessionmaker(engine)


def test_load_alias_map_empty_table_returns_empty_dict(session_factory):
    Session = session_factory
    with Session() as db:
        assert load_alias_map(db) == {}


def test_load_alias_map_lowercases_and_self_maps_canonical(session_factory):
    Session = session_factory
    with Session() as db:
        db.add(ArtistAlias(alias_name="Plastikman", canonical_name="Richie Hawtin"))
        db.commit()

        alias_map = load_alias_map(db)
        assert alias_map == {
            "plastikman": "richie hawtin",
            "richie hawtin": "richie hawtin",
        }


def test_load_alias_map_multiple_aliases_to_same_canonical(session_factory):
    Session = session_factory
    with Session() as db:
        db.add(ArtistAlias(alias_name="Plastikman", canonical_name="Richie Hawtin"))
        db.add(ArtistAlias(alias_name="F.U.S.E.", canonical_name="Richie Hawtin"))
        db.commit()

        alias_map = load_alias_map(db)
        assert alias_map["plastikman"] == "richie hawtin"
        assert alias_map["f.u.s.e."] == "richie hawtin"
        assert alias_map["richie hawtin"] == "richie hawtin"


def test_loaded_alias_map_works_with_resolve_artist_keys(session_factory):
    """End-to-end: DB → load_alias_map → resolve_artist_keys."""
    Session = session_factory
    with Session() as db:
        db.add(ArtistAlias(alias_name="Plastikman", canonical_name="Richie Hawtin"))
        db.commit()

        alias_map = load_alias_map(db)
        assert resolve_artist_keys("Plastikman", alias_map) == frozenset({"richie hawtin"})
        assert resolve_artist_keys("Plastikman & Someone", alias_map) == frozenset(
            {"richie hawtin", "someone"}
        )
        # Canonical resolves to itself.
        assert resolve_artist_keys("Richie Hawtin", alias_map) == frozenset({"richie hawtin"})


def test_alias_name_uniqueness_enforced(session_factory):
    """Schema-level: alias_name has UNIQUE constraint."""
    from sqlalchemy.exc import IntegrityError

    Session = session_factory
    with Session() as db:
        db.add(ArtistAlias(alias_name="Plastikman", canonical_name="Richie Hawtin"))
        db.commit()

        db.add(ArtistAlias(alias_name="Plastikman", canonical_name="Someone Else"))
        with pytest.raises(IntegrityError):
            db.commit()
