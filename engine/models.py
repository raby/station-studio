"""SQLAlchemy ORM models.

Generalised from weekly/app/models.py. Key differences:
  * Playlist + PlaylistEntry are station-scoped.
  * PlaylistTrace table holds the persisted trace keyed by trace_id.
  * Station template JSON lives on the Station row.
  * MixUsage shipped in §14.25 (cross-week mix dedup, station-scoped via
    playlist_id rather than weekly's weekly_playlist_id).
  * ArtistAlias shipped in §14.26 (kept global rather than station-scoped;
    see the model's docstring for the rationale).
  * Rule shipped in §14.29 (per-station config overrides; weekly's
    equivalent is global — station-studio is multi-station).
  * PlaylistEntry.slot_type added in §14.31 (denormalised strategy tag;
    nullable for pre-§14.31 rows, backfillable from PlaylistTrace.slots).
"""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Text, DateTime, ForeignKey, JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from engine.database import Base


class Station(Base):
    """A station is a named template plus its live cursor state."""
    __tablename__ = "station"

    id = Column(Integer, primary_key=True)
    slug = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=False)
    template = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ORM-level cascade ensures playlists are deleted even on sessions that
    # don't have PRAGMA foreign_keys=ON (e.g. direct SQLite connections).
    playlists = relationship(
        "Playlist",
        back_populates="station",
        cascade="all, delete-orphan",
    )


class SourcePlaylist(Base):
    """A named pool of tracks imported from an iTunes XML file.

    `role` distinguishes pools the engine samples from (`content`) from
    pools imported only for cross-reference (`reference`, §14.35). The
    canonical example is weekly's MASTER pool — its iTunes Persistent
    IDs let the exporter resolve PIDs across pools, but the playlist
    generator never samples it. Templates' `sources[].role` field
    carries the same information at the layout level; the DB column
    is the authoritative source-of-truth at generation time, and a
    consistency check raises TemplateError when the two disagree in
    the dangerous direction (layout=content, DB=reference).

    Default is `content` so existing rows + non-reference imports
    require no changes; only the explicit-reference path is new.
    """
    __tablename__ = "source_playlist"

    id = Column(Integer, primary_key=True)
    name = Column(Text, unique=True, nullable=False)
    role = Column(Text, nullable=False, server_default="content", default="content")
    imported_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    entries = relationship(
        "SourcePlaylistEntry",
        back_populates="source_playlist",
        cascade="all, delete-orphan",
        order_by="SourcePlaylistEntry.position",
    )


class Track(Base):
    """A track, deduplicated on iTunes Persistent ID across all pools."""
    __tablename__ = "track"

    id = Column(Integer, primary_key=True)
    persistent_id = Column(Text, unique=True, nullable=False)
    itunes_track_id = Column(Integer, nullable=True)
    name = Column(Text, nullable=False)
    artist = Column(Text, nullable=True)
    album = Column(Text, nullable=True)
    genre = Column(Text, nullable=True)
    total_time = Column(Integer, nullable=True)
    itunes_metadata = Column(JSON, nullable=True)


class SourcePlaylistEntry(Base):
    """Ordered entry: pool -> position -> track."""
    __tablename__ = "source_playlist_entry"

    id = Column(Integer, primary_key=True)
    source_playlist_id = Column(
        Integer, ForeignKey("source_playlist.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    track_id = Column(
        Integer, ForeignKey("track.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    position = Column(Integer, nullable=False)

    source_playlist = relationship("SourcePlaylist", back_populates="entries")
    track = relationship("Track")

    __table_args__ = (
        UniqueConstraint("source_playlist_id", "position"),
    )


class Playlist(Base):
    """A generated playlist for one (station, week) pair."""
    __tablename__ = "playlist"

    id = Column(Integer, primary_key=True)
    station_id = Column(
        Integer, ForeignKey("station.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    week = Column(Integer, nullable=False)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    seed = Column(Integer, nullable=True)
    trace_id = Column(Text, nullable=False)  # UUID; links to PlaylistTrace

    station = relationship("Station", back_populates="playlists")
    entries = relationship(
        "PlaylistEntry",
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistEntry.position",
    )
    # ORM-level cascade for the trace mirrors the Station→Playlist setup:
    # the FK already has ondelete=CASCADE, but ORM cascade is belt-and-
    # braces against any future Session created without the
    # PRAGMA foreign_keys=ON event listener. one-to-one => uselist=False.
    trace = relationship(
        "PlaylistTrace",
        back_populates="playlist",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        UniqueConstraint("station_id", "week"),
    )


class PlaylistEntry(Base):
    __tablename__ = "playlist_entry"

    id = Column(Integer, primary_key=True)
    playlist_id = Column(
        Integer, ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    track_id = Column(
        Integer, ForeignKey("track.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    position = Column(Integer, nullable=False)
    source_name = Column(Text, nullable=True)  # which pool supplied this slot
    # §14.31 — per-entry strategy tag (window/pick/sequential/jingle/mix/
    # weighted/prev_ref). Denormalises the trace's slot.strategy onto the
    # entry so queries like "show me all mix entries from week 12" don't
    # need a JOIN with playlist_trace.slots JSON. Nullable so existing
    # rows (pre-§14.31 playlists) remain valid pre-backfill; new rows
    # always populate via save_playlist. Run scripts/backfill_slot_types.py
    # to populate historical entries from their persisted traces.
    slot_type = Column(Text, nullable=True)

    playlist = relationship("Playlist", back_populates="entries")
    track = relationship("Track")

    __table_args__ = (
        UniqueConstraint("playlist_id", "position"),
    )


class PlaylistTrace(Base):
    """Persisted per-slot trace keyed by trace_id."""
    __tablename__ = "playlist_trace"

    trace_id = Column(Text, primary_key=True)
    playlist_id = Column(
        Integer, ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    station_slug = Column(Text, nullable=False)
    week = Column(Integer, nullable=False)
    rng = Column(Text, nullable=False)
    seed = Column(Integer, nullable=True)
    generated_at = Column(Text, nullable=False)  # ISO8601
    slots = Column(JSON, nullable=False)         # list[TraceSlot as dict]

    playlist = relationship("Playlist", back_populates="trace")


class Config(Base):
    """Simple key/value store for library-level settings.

    Currently used to persist iTunes library metadata captured during import
    (library_persistent_id, music_folder) so the exporter can reproduce an
    XML file that iTunes will accept. General-purpose — other system-wide
    settings can live here too.
    """
    __tablename__ = "config"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=True)


class CursorState(Base):
    """Per-station cursor state (if a station wants to persist it)."""
    __tablename__ = "cursor_state"

    id = Column(Integer, primary_key=True)
    station_id = Column(
        Integer, ForeignKey("station.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = Column(Text, nullable=False)
    position = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("station_id", "name"),
    )


class ArtistAlias(Base):
    """Maps an alias artist name to a canonical artist name (§14.26).

    Used by the artist-dedup subsystem ported in §14.26: if "Plastikman"
    is an alias for "Richie Hawtin", a track tagged with either name is
    treated as the same artist in the §17.1 commercial dedup window
    (which sub-slice #11 `weighted` consumes via engine.artist_match).

    Global (not station-scoped). The premise is that artist identity is a
    library-wide fact — "Plastikman is Richie Hawtin" doesn't change
    between Classics and Rebirth Radio. If a future station genuinely
    wants to treat the two as separate, scope per-station via a new
    column (punt; revisit only on real demand).

    Both names stored as-is for display; matching is done case-insensitively
    at runtime via the alias_map's lowercase keys (see persistence.
    load_alias_map and engine.artist_match.build_alias_map).
    """
    __tablename__ = "artist_alias"

    id = Column(Integer, primary_key=True)
    alias_name = Column(Text, nullable=False, unique=True)
    canonical_name = Column(Text, nullable=False)


class MixUsage(Base):
    """Per-playlist record of which mix tracks were used + their genre tag.

    Powers the §14.25 `mix` strategy's cross-week dedup: when generating week
    W of a station, the exclusion set is the union of MixUsage.track_id over
    that station's last N playlists (N = block.exclusion_window). Genre is
    redundant with template knowledge but stored so historical exclusion
    analysis works without re-resolving the template version that produced
    the row. Mirrors weekly's `mix_usage` table; station-scoped via
    playlist_id rather than weekly's weekly_playlist_id since station-studio
    is multi-station.

    No `created_at` column. The parent Playlist's `generated_at` is the
    authoritative timestamp for "when was this MixUsage row written" — the
    two are written in the same transaction. Add a column here only when a
    feature genuinely needs per-row write time (e.g. partial-regeneration
    scenarios that touch some MixUsage rows but not others — none today).

    `genre` is a point-in-time snapshot, not a live join with Track.genre.
    If a track's genre is corrected in the Track table after a MixUsage row
    is written, this row keeps the genre that was *used* for exclusion when
    that historical playlist was generated. Analysis tools should treat
    MixUsage.genre as the source of truth for "which genre slot did this
    track fill?" — do NOT join with Track.genre, which can drift.
    """
    __tablename__ = "mix_usage"

    id = Column(Integer, primary_key=True)
    playlist_id = Column(
        Integer, ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    track_id = Column(
        Integer, ForeignKey("track.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    genre = Column(Text, nullable=False)


class Rule(Base):
    """Per-station configuration overrides (§14.29).

    Stores ONLY user overrides. The full set of valid rule keys + their
    default values lives in `engine.rules.RULE_DEFAULTS`. Stations with
    no rows in this table fall back to defaults exactly; only divergence
    from defaults is persisted.

    Station-scoped (composite UNIQUE on station_id + key) rather than
    weekly's global single-key shape — station-studio is multi-station
    and per-station overrides are the whole reason this slice exists.
    Classics could keep defaults while Rebirth Radio overrides
    `artist_dedup_window`, etc.

    Value stored as TEXT so the column can later carry non-int rules
    (string enums, list-of-int durations, etc.) without a schema
    migration. The engine.rules accessor parses to int for the current
    four rules; future rule types parse to whatever shape they declare
    in RULE_DEFAULTS.
    """
    __tablename__ = "rule"

    id = Column(Integer, primary_key=True)
    station_id = Column(
        Integer, ForeignKey("station.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    key = Column(Text, nullable=False)
    value = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("station_id", "key"),
    )
