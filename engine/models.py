"""SQLAlchemy ORM models.

Generalised from weekly/app/models.py. Key differences:
  * Playlist + PlaylistEntry are station-scoped.
  * PlaylistVersion table (§16.4) holds multi-row trace history per
    Playlist; `Playlist.trace_id` denormalizes the current version's
    trace_id as a fast read shortcut. Pre-§16.4 this was the
    single-row `PlaylistTrace`.
  * Station template JSON lives on the Station row.
  * MixUsage shipped in §14.25 (cross-week mix dedup, station-scoped via
    playlist_id rather than weekly's weekly_playlist_id).
  * ArtistAlias shipped in §14.26 (kept global rather than station-scoped;
    see the model's docstring for the rationale).
  * Rule shipped in §14.29 (per-station config overrides; weekly's
    equivalent is global — station-studio is multi-station).
  * PlaylistEntry.slot_type added in §14.31 (denormalised strategy tag;
    nullable for pre-§14.31 rows, backfillable from the current
    PlaylistVersion's slots JSON via `scripts/backfill_slot_types.py`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Text, DateTime, ForeignKey, JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from engine.database import Base
# §16.2.5.t13 — `SidecarJSON` lives in the leaf `engine.sidecar_types`
# module so this file (loaded by every ORM consumer) doesn't pull in
# the 1.5K-LOC `engine.editor_metadata` and its full drift-rule graph.
from engine.sidecar_types import SidecarJSON


class Station(Base):
    """A station is a named template plus its live cursor state."""
    __tablename__ = "station"

    id = Column(Integer, primary_key=True)
    slug = Column(Text, unique=True, nullable=False)
    name = Column(Text, nullable=False)
    template = Column(JSON, nullable=False)
    # §16.2.5 D1 — sidecar in DB (materialised). Nullable; NULL = "no
    # committed sidecar" (legacy stations pre-migration OR un-authored).
    # GET /sidecar falls back to the on-disk file then to a synthesised
    # trivial sidecar (Decision E from §16.2.2 — frontend never handles
    # "no sidecar"). Mutation endpoints write to this column; the
    # on-disk file becomes the export artifact (Decision A3 symmetry
    # with stations/{slug}.json for the template).
    #
    # §16.2.5.t4 — `SidecarJSON` is a `TypeDecorator` over `JSON` that
    # runs `validate_sidecar_grammar` on every ORM-attribute bind so
    # the shape check fires whether the write comes through
    # `_persist_sidecar`, a test fixture, or a future endpoint that
    # forgets the shared helper. NULL passes through; reads stay
    # pass-through (lenient `_resolve_sidecar` handles legacy data).
    # The underlying SQL column type stays `JSON` — no schema migration
    # needed.
    #
    # NOT covered: any future alembic data migration that constructs
    # an ad-hoc `sa.table('station', sa.column('sidecar', sa.JSON()))`
    # to avoid importing engine code. Those writes bypass the
    # validator by design + the runtime read path degrades via
    # `_resolve_sidecar(strict=False)` warning banner.
    #
    # c2 review #3 finding #2: dropped the conditional
    # `SidecarJSON if SidecarJSON is not None else JSON` fallback. The
    # fallback silently downgraded to plain JSON if the SidecarJSON
    # import EVER failed (a future circular import, a partial
    # SQLAlchemy install) — no log, no warning, validation gone. Fail
    # loud at import time instead.
    sidecar = Column(SidecarJSON, nullable=True)
    # §16.2.5.t7 — persisted error reason for the broken-disk-sidecar
    # banner UX. Set by `populate_db_sidecars` when an on-disk
    # `.editor.json` fails parse/validation and the column had to fall
    # back to `derive_sidecar(template)`. Cleared on a successful
    # disk-load (so the operator's "fix the file + restart" recipe
    # naturally removes the stale banner). Read by `_resolve_sidecar`'s
    # GET path and surfaced in `EditorSidecarResponse.sidecar_error`.
    #
    # Plain `Text` (not `SidecarJSON`) — it's a free-form operator-
    # readable string, not a structured sidecar payload. No TypeDecorator
    # validation needed.
    sidecar_error = Column(Text, nullable=True)
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

    The mismatch check lives in `engine.persistence.load_pools`
    (raises `TemplateError` on the dangerous direction). Default is
    `content` so existing rows + non-reference imports require no
    changes; only the explicit-reference path is new.
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
    # §16.4 — pointer to the CURRENT version's `trace_id`. Pre-§16.4
    # this was the single trace's id; post-§16.4 it's a denormalized
    # shortcut so `PlaylistOut.trace_id` (and any caller that doesn't
    # care about history) doesn't need a join. `save_playlist` flips
    # this on every regen to point at the new max-version row.
    trace_id = Column(Text, nullable=False)

    station = relationship("Station", back_populates="playlists")
    entries = relationship(
        "PlaylistEntry",
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistEntry.position",
    )
    # §16.4 — multi-row trace history. ORM cascade mirrors the
    # Station→Playlist setup (the FK already has ondelete=CASCADE,
    # but ORM cascade is belt-and-braces against any future Session
    # created without the PRAGMA foreign_keys=ON event listener).
    # `uselist=True, order_by="PlaylistVersion.version_no"` so
    # `playlist.versions` iterates oldest → newest; `current_version()`
    # below returns the max.
    versions = relationship(
        "PlaylistVersion",
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistVersion.version_no",
    )

    __table_args__ = (
        UniqueConstraint("station_id", "week"),
    )

    def current_version(self) -> "PlaylistVersion | None":
        """Return the latest `PlaylistVersion` row, or None if no
        version has been written yet.

        §16.4 — single helper so callers don't sprinkle `versions[-1]`
        across the codebase. Returns None defensively for un-flushed /
        freshly-constructed Playlists (the runtime shape always has
        ≥1 version post-`save_playlist`, but legacy DB rows in the
        wild during migration could in theory have none).

        **Perf note:** triggers a lazy load of `versions` (the full
        list, ordered by version_no) on first access. Acceptable
        today (Classics's regen rate is hand-driven; even hundreds of
        versions per playlist load in milliseconds). If retention
        ever drives N to thousands and call sites multiply, replace
        with a per-call `session.query(PlaylistVersion).filter_by(
        playlist_id=...).order_by(version_no.desc()).first()` to
        avoid the full-list materialization.
        """
        return self.versions[-1] if self.versions else None


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
    # need a JOIN with playlist_version.slots JSON. Nullable so existing
    # rows (pre-§14.31 playlists) remain valid pre-backfill; new rows
    # always populate via save_playlist. Run scripts/backfill_slot_types.py
    # to populate historical entries from their persisted traces.
    slot_type = Column(Text, nullable=True)

    playlist = relationship("Playlist", back_populates="entries")
    track = relationship("Track")

    __table_args__ = (
        UniqueConstraint("playlist_id", "position"),
    )


class PlaylistVersion(Base):
    """One generated trace for a Playlist (§16.4 trace versioning v1).

    Replaces the prior single-row `PlaylistTrace`. Each regenerate
    appends a new row with `version_no = max + 1`; the prior version
    stays addressable for the trace viewer's history picker.

    Identity:
      * `id` — synthetic PK (was `trace_id` PK on PlaylistTrace —
        switched to a normal autoincrement integer so the
        `(playlist_id, version_no)` natural key can carry the unique
        constraint cleanly).
      * `trace_id` — still UNIQUE (the engine generates a fresh UUID
        per generation; external callers identify a specific trace by
        this id).
      * `(playlist_id, version_no)` — UNIQUE; the natural key.

    `Playlist.trace_id` points at the CURRENT version's `trace_id`
    as a denormalized shortcut; `save_playlist` updates it in lockstep.
    """
    __tablename__ = "playlist_version"

    id = Column(Integer, primary_key=True)
    playlist_id = Column(
        Integer, ForeignKey("playlist.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # 1-indexed. version_no=1 is the first generation; subsequent
    # regenerations append version_no=2, 3, ...
    version_no = Column(Integer, nullable=False)
    trace_id = Column(Text, nullable=False, unique=True)
    station_slug = Column(Text, nullable=False)
    week = Column(Integer, nullable=False)
    rng = Column(Text, nullable=False)
    seed = Column(Integer, nullable=True)
    generated_at = Column(Text, nullable=False)  # ISO8601
    slots = Column(JSON, nullable=False)         # list[TraceSlot as dict]
    # §16.4.t5 — operator-applied annotation tags (e.g.
    # "sent-to-iTunes", "jon-approved"). Linear history is preserved;
    # tags are pure annotations, not branches. Validated at the
    # PATCH endpoint (max 10 tags, 1-40 chars each, no commas).
    # `default=list` provides an empty list for newly-constructed
    # rows; the migration backfills existing rows via server_default.
    tags = Column(JSON, nullable=False, default=list)
    # The persistent cursor positions this generation started from
    # ({name: position}), for templates with cursor.persist. Lets a past
    # week be regenerated from where its rules were then, not from the
    # station's live cursors (which have moved on with later weeks).
    # NULL for stateless templates and for versions made before 1.12.
    cursors = Column(JSON(none_as_null=True), nullable=True)

    playlist = relationship("Playlist", back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "playlist_id", "version_no",
            name="uq_playlist_version_playlist_id_version_no",
        ),
    )


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
