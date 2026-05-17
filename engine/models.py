"""SQLAlchemy ORM models.

Generalised from weekly/app/models.py. Key differences:
  * No station-specific tables (no MixUsage, no ArtistAlias in v1 — added per station
    as the engine grows new primitives).
  * Playlist + PlaylistEntry are station-scoped.
  * PlaylistTrace table holds the persisted trace keyed by trace_id.
  * Station template JSON lives on the Station row.
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
    """A named pool of tracks imported from an iTunes XML file."""
    __tablename__ = "source_playlist"

    id = Column(Integer, primary_key=True)
    name = Column(Text, unique=True, nullable=False)
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
