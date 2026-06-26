"""Export a generated Playlist as an iTunes XML plist file.

Generalised from weekly/app/services/exporter.py. Differences:
  * Station-scoped: playlist name and Persistent ID derive from the station
    row, not a hardcoded "WEEKLY PLAYLIST NNN" convention.
  * Model names updated (Playlist / PlaylistEntry vs WeeklyPlaylist / ...).
  * All the XML-format tricks (key ordering, same-line key+value, Apple
    Computer DOCTYPE, synthetic Track IDs for trackless fixtures) are kept
    verbatim — that code has been battle-tested against real iTunes XML.
"""
from __future__ import annotations

import logging
import plistlib
import re
from collections import OrderedDict
from datetime import datetime
from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import Session, selectinload

from engine.models import (
    Playlist, PlaylistEntry, PlaylistVersion, Track, Config, Station,
)


logger = logging.getLogger(__name__)

# Known iTunes metadata keys that contain datetime values. Legacy fallback
# for data imported before the type-marker approach.
_DATETIME_KEYS = frozenset({
    "Date Added",
    "Date Modified",
    "Play Date UTC",
})

# Starting value for synthetic Track IDs assigned to tracks that have no
# original iTunes Track ID (e.g. test fixtures). High to avoid collisions
# with real iTunes IDs.
_SYNTHETIC_TRACK_ID_START = 100_000

# Fixed iTunes top-level key order.
_TOP_LEVEL_KEY_ORDER = [
    "Major Version",
    "Minor Version",
    "Application Version",
    "Features",
    "Show Content Ratings",
    "Music Folder",
    "Library Persistent ID",
    "Tracks",
    "Playlists",
]

# Fixed iTunes playlist dict key order.
_PLAYLIST_KEY_ORDER = [
    "Name",
    "Playlist ID",
    "Playlist Persistent ID",
    "All Items",
    "Playlist Items",
]

# Defaults used when no source file has been imported yet (tests, dev).
_DEFAULT_LIBRARY_PERSISTENT_ID = "STATION_STUDIO"
_DEFAULT_MUSIC_FOLDER = "file://localhost/"

# Default destination for export_playlist_to_disk when no Config override is
# set. Relative paths resolve against the process CWD (typically the repo
# root when running via scripts/* or the API).
_DEFAULT_OUTPUT_DIR = "out"


# ---------- helpers -------------------------------------------------------


def _restore_metadata_value(key: str, value):
    if isinstance(value, dict) and value.get("__type__") == "datetime":
        try:
            return datetime.fromisoformat(value["value"])
        except (ValueError, KeyError):
            return value.get("value", value)
    if isinstance(value, str) and key in _DATETIME_KEYS:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _track_to_itunes_dict(track: Track, itunes_track_id: int) -> OrderedDict:
    """Convert a Track row back into an ordered iTunes plist track dict.

    Preserves the original iTunes XML field order by iterating through
    itunes_metadata (which was stored insertion-ordered at import).
    Column values (name, artist, ...) override metadata copies so any
    post-import edits are reflected.
    """
    meta = track.itunes_metadata or {}

    if not meta:
        # Minimal fallback for tracks without stored metadata (fixtures).
        d = OrderedDict()
        d["Track ID"] = itunes_track_id
        d["Name"] = track.name
        if track.artist:
            d["Artist"] = track.artist
        if track.album:
            d["Album"] = track.album
        if track.genre:
            d["Genre"] = track.genre
        if track.total_time is not None:
            d["Total Time"] = track.total_time
        d["Persistent ID"] = track.persistent_id
        d["Track Type"] = "File"
        return d

    column_overrides = {
        "Track ID": itunes_track_id,
        "Name": track.name,
        "Artist": track.artist,
        "Album": track.album,
        "Genre": track.genre,
        "Total Time": track.total_time,
        "Persistent ID": track.persistent_id,
    }

    d = OrderedDict()
    for k, v in meta.items():
        if k in column_overrides:
            val = column_overrides[k]
            if val is not None:
                d[k] = val
        else:
            d[k] = _restore_metadata_value(k, v)

    # Inject any column overrides that weren't in metadata (legacy imports).
    for k in ("Track ID", "Name", "Artist", "Album", "Genre", "Total Time",
              "Persistent ID"):
        if k not in d:
            val = column_overrides.get(k)
            if val is not None:
                d[k] = val

    return d


def _playlist_display_name(station: Station, week: int) -> str:
    return f"{station.name} {week:03d}"


def _playlist_persistent_id(station: Station, week: int) -> str:
    """Stable, iTunes-acceptable Playlist Persistent ID.

    Uppercase, underscores only. 'rebirth-classics' + week 1 → 'REBIRTH_CLASSICS_001'.
    """
    slug = station.slug.upper().replace("-", "_")
    return f"{slug}_{week:03d}"


def _build_ordered_plist(
    tracks_dict: OrderedDict,
    playlist_items: list[dict],
    *,
    station: Station,
    week: int,
    library_persistent_id: str,
    music_folder: str,
) -> OrderedDict:
    playlist = OrderedDict()
    for key in _PLAYLIST_KEY_ORDER:
        if key == "Name":
            playlist[key] = _playlist_display_name(station, week)
        elif key == "Playlist ID":
            playlist[key] = 1
        elif key == "Playlist Persistent ID":
            playlist[key] = _playlist_persistent_id(station, week)
        elif key == "All Items":
            playlist[key] = True
        elif key == "Playlist Items":
            playlist[key] = playlist_items

    plist_data = OrderedDict()
    for key in _TOP_LEVEL_KEY_ORDER:
        if key == "Major Version":
            plist_data[key] = 1
        elif key == "Minor Version":
            plist_data[key] = 1
        elif key == "Application Version":
            plist_data[key] = "10.3"
        elif key == "Features":
            plist_data[key] = 5
        elif key == "Show Content Ratings":
            plist_data[key] = True
        elif key == "Music Folder":
            plist_data[key] = music_folder
        elif key == "Library Persistent ID":
            plist_data[key] = library_persistent_id
        elif key == "Tracks":
            plist_data[key] = tracks_dict
        elif key == "Playlists":
            plist_data[key] = [playlist]

    return plist_data


def _itunes_format_xml(xml_bytes: bytes) -> bytes:
    """Post-process plistlib output to match iTunes's exact formatting."""
    text = xml_bytes.decode("utf-8")

    # Fix DOCTYPE: Apple → Apple Computer (iTunes legacy).
    text = text.replace(
        "//Apple//DTD PLIST 1.0//EN",
        "//Apple Computer//DTD PLIST 1.0//EN",
    )

    # Merge <key>…</key> + following value onto the same line, but only for
    # leaf value elements (never <dict>, <array>, </dict>, </array>).
    text = re.sub(
        r"(<key>[^<]+</key>)\n\s*"
        r"(<(?:string|integer|real|date|data)>[^<]*</(?:string|integer|real|date|data)>|<(?:true|false)/>)",
        r"\1\2",
        text,
    )

    return text.encode("utf-8")


# ---------- public API ----------------------------------------------------


def export_playlist(
    db: Session,
    playlist_id: int,
    *,
    version_no: int | None = None,
) -> bytes:
    """Export a generated Playlist as iTunes XML bytes.

    §16.4.t2 — `version_no` opt-in selects a HISTORICAL
    PlaylistVersion (default None = current, reads from PlaylistEntry
    as pre-§16.4.t2). The historical path reads track ids from
    `PlaylistVersion.slots` and resolves each to a Track via FK
    lookup. The current-version path stays on PlaylistEntry — same
    bytes, lower SQL surface (no per-slot Track query) for the hot
    path.

    Raises ValueError if:
      * the playlist doesn't exist
      * the requested version_no doesn't exist for this playlist
      * a slot references a track_id that's since been deleted
        (historical path only — the current path can't hit this
        since PlaylistEntry FKs are CASCADE-enforced)
    """
    p = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if p is None:
        raise ValueError(f"Playlist {playlist_id} not found")

    station = p.station

    # Build the (track, slot_meta) iteration source. Current path reads
    # PlaylistEntry; historical path reads PlaylistVersion.slots +
    # resolves track_id → Track.
    iter_entries: list[tuple[Track, int]] = []  # (track, position)
    if version_no is None:
        for entry in (
            db.query(PlaylistEntry)
            .filter(PlaylistEntry.playlist_id == p.id)
            .order_by(PlaylistEntry.position)
            .all()
        ):
            iter_entries.append((entry.track, entry.position))
    else:
        version = (
            db.query(PlaylistVersion)
            .filter_by(playlist_id=p.id, version_no=version_no)
            .first()
        )
        if version is None:
            raise ValueError(
                f"Playlist {playlist_id} has no version_no={version_no}"
            )
        # Resolve track_ids in bulk to amortise the N+1 risk on a
        # 3000-slot trace. Order the result by slot position so the
        # exported XML carries the same play order as the trace.
        track_ids_in_order: list[int | None] = [
            s.get("track_id") for s in (version.slots or [])
        ]
        unique_ids = {tid for tid in track_ids_in_order if tid is not None}
        tracks_by_id = {
            t.id: t
            for t in db.query(Track).filter(Track.id.in_(unique_ids)).all()
        } if unique_ids else {}
        for pos, tid in enumerate(track_ids_in_order, start=1):
            if tid is None:
                # Dry-run / un-resolved slot — shouldn't appear in
                # production traces but defensible if a future
                # generator path emits one.
                raise ValueError(
                    f"Playlist {playlist_id} v{version_no} slot at "
                    f"position {pos}: track_id is null; cannot export."
                )
            track = tracks_by_id.get(tid)
            if track is None:
                raise ValueError(
                    f"Playlist {playlist_id} v{version_no} slot at "
                    f"position {pos}: track {tid} has been deleted; "
                    f"export of this historical version is no longer "
                    f"possible.",
                )
            iter_entries.append((track, pos))

    # Library metadata: read from Config, fall back to defaults.
    lib_pid_row = db.query(Config).filter(Config.key == "library_persistent_id").first()
    music_folder_row = db.query(Config).filter(Config.key == "music_folder").first()
    library_persistent_id = lib_pid_row.value if lib_pid_row else _DEFAULT_LIBRARY_PERSISTENT_ID
    music_folder = music_folder_row.value if music_folder_row else _DEFAULT_MUSIC_FOLDER

    # Build Tracks dict (unique tracks) and Playlist Items (in-order references).
    tracks_dict: OrderedDict = OrderedDict()
    playlist_items: list[dict] = []
    pid_to_id: dict[str, int] = {}
    next_synthetic_id = _SYNTHETIC_TRACK_ID_START

    for track, _position in iter_entries:
        if track.persistent_id not in pid_to_id:
            if track.itunes_track_id is not None:
                itunes_id = track.itunes_track_id
            else:
                itunes_id = next_synthetic_id
                next_synthetic_id += 1
            pid_to_id[track.persistent_id] = itunes_id
            tracks_dict[str(itunes_id)] = _track_to_itunes_dict(track, itunes_id)
        else:
            itunes_id = pid_to_id[track.persistent_id]
        playlist_items.append({"Track ID": itunes_id})

    # iTunes orders the Tracks dict by numeric Track ID ascending.
    tracks_dict = OrderedDict(
        sorted(tracks_dict.items(), key=lambda kv: int(kv[0]))
    )

    plist_data = _build_ordered_plist(
        tracks_dict,
        playlist_items,
        station=station,
        week=p.week,
        library_persistent_id=library_persistent_id,
        music_folder=music_folder,
    )

    buf = BytesIO()
    plistlib.dump(plist_data, buf, fmt=plistlib.FMT_XML, sort_keys=False)

    xml_bytes = _itunes_format_xml(buf.getvalue())

    logger.info(
        "Exported playlist %d (%s week %d): %d entries, %d unique tracks, %d bytes",
        p.id, station.slug, p.week, len(playlist_items), len(tracks_dict), len(xml_bytes),
    )
    return xml_bytes


def _resolve_output_dir(db: Session) -> Path:
    """Resolve the configured disk-export directory to an absolute path.

    Reads Config key 'playlist_output_dir' if set, else uses _DEFAULT_OUTPUT_DIR.
    User-supplied paths may be absolute, relative, or '~'-prefixed; all are
    normalised to an absolute Path.
    """
    row = db.query(Config).filter(Config.key == "playlist_output_dir").first()
    raw = row.value if row and row.value else _DEFAULT_OUTPUT_DIR
    return Path(raw).expanduser().resolve()


def export_playlist_to_disk(
    db: Session,
    playlist_id: int,
    *,
    version_no: int | None = None,
    versioned_filename: bool = False,
) -> Path:
    """Export a playlist as iTunes XML and write it to disk.

    Filename matrix:

      * `version_no=None, versioned_filename=False` (default): `{output_dir}/
        {station_slug}/{week:03d}.xml`. Overwrites silently — re-exporting
        the same week is idempotent and mirrors how the underlying playlist
        is regenerated. Pre-§16.4.t6 behavior.
      * `version_no=None, versioned_filename=True` (§16.4.t6 archival
        opt-in): `{output_dir}/{station_slug}/{week:03d}-v{current}.xml`.
        Current playlist exported with the current's version_no in the
        filename — each regen accumulates its own file rather than
        overwriting. Operator's OneDrive folder grows linearly.
      * `version_no=N` (§16.4.t2 historical export): `{output_dir}/
        {station_slug}/{week:03d}-v{N}.xml`. The version suffix is
        SAFETY-CRITICAL — without it, exporting v2 would silently
        clobber the current v3's `{week:03d}.xml`, sending the wrong
        content to the operator's OneDrive / iTunes box. The
        `versioned_filename` flag is redundant on this path (already
        suffixed); behavior unchanged whether the flag is true or false.

    Raises ValueError if the playlist doesn't exist or (with
    `versioned_filename=True` + `version_no=None`) if no
    PlaylistVersion rows exist yet for this playlist.

    Creates parent directories as needed. Returns the absolute path written.
    """
    p = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if p is None:
        raise ValueError(f"Playlist {playlist_id} not found")

    xml_bytes = export_playlist(db, playlist_id, version_no=version_no)

    # §16.4.t6 — resolve the version_no for the filename when the
    # operator opted in but didn't pass an explicit `v`. Read the
    # current version's number from PlaylistVersion (the same
    # source-of-truth used by /versions).
    effective_version_no = version_no
    if version_no is None and versioned_filename:
        current = (
            db.query(PlaylistVersion)
            .filter_by(playlist_id=playlist_id)
            .order_by(PlaylistVersion.version_no.desc())
            .first()
        )
        if current is None:
            raise ValueError(
                f"Playlist {playlist_id} has no versions; cannot "
                f"determine the suffix for versioned-filename export.",
            )
        effective_version_no = current.version_no

    out_dir = _resolve_output_dir(db)
    station_dir = out_dir / p.station.slug
    station_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{p.week:03d}.xml" if effective_version_no is None
        else f"{p.week:03d}-v{effective_version_no}.xml"
    )
    target = station_dir / filename

    existed = target.exists()
    target.write_bytes(xml_bytes)

    logger.info(
        "Wrote playlist %d%s to %s (%s, %d bytes)",
        playlist_id,
        f" v{effective_version_no}" if effective_version_no is not None else "",
        target,
        "overwrote" if existed else "created",
        len(xml_bytes),
    )
    return target


# §16.16 — disk-export prune helper.
#
# `out/{slug}/{week:03d}.xml` and `out/{slug}/{week:03d}-v{N}.xml`
# accumulate over time as playlists are deleted (cascades drop the DB
# rows but the on-disk XML files are untouched — they're operator-
# managed once written). Fork doesn't clean up either. This helper
# scans the configured output dir, classifies every `.xml` file as
# "valid" (DB row exists) or "orphaned" (no DB row matches), and
# (when `confirm=True`) deletes the orphaned ones.
#
# Defense-in-depth: every candidate path is resolved + verified to be
# UNDER the configured output dir before deletion. Symlinks are
# skipped entirely (avoids a symlink pointing outside the dir).

# `001.xml` (week-only) OR `001-v3.xml` (week + version) — both
# patterns produced by `export_playlist_to_disk`. The version suffix
# is §16.4.t2 (historical export) + §16.4.t6 (opt-in current archive).
# The week group is `\d{3,}` (not `\d{3}`) because `f'{week:03d}'`
# pads to a *minimum* of 3 digits — week=1000 writes `1000.xml`, so
# the reader must accept >=3 digits to stay in lockstep.
_PRUNE_FILENAME_RE = re.compile(
    r"^(?P<week>\d{3,})(?:-v(?P<version>\d+))?\.xml$",
)


def prune_export_directory(
    db: Session,
    *,
    confirm: bool = False,
    station_slug: str | None = None,
) -> dict:
    """Scan the configured output dir for orphaned export files.

    Returns a dict shaped:

        {
          "scanned": int,
          "orphans": [{"path": str, "reason": str}, ...],
          "deleted": int,                                # 0 unless confirm=True
          "skipped": [{"path": str, "reason": str}],     # not unlinked
        }

    Args:
      * `confirm=True` actually deletes the orphans. Default
        (False) is dry-run.
      * `station_slug` restricts the scan to a single station's
        subdirectory. Default scans all stations. Raises
        `ValueError` if the slug does not match any Station (catches
        operator typos that would otherwise look like "nothing to
        prune").

    Orphan classification (checked in priority order so the most
    specific reason wins):

    * Subdir slugs that don't match any Station — every file under
      them is orphaned.
    * Unexpected filenames (not matching the `001.xml` /
      `001-v3.xml` patterns) — orphaned (the operator dropped a
      stray file we can't classify).
    * `{slug}/{week:03d}.xml` — orphaned when no `Playlist` row exists
      for (Station.slug=slug, week=week).
    * `{slug}/{week:03d}-v{N}.xml` — first checks whether the parent
      Playlist row exists; if not, the reason is "no Playlist for
      (slug, week)" rather than the more specific version-miss. If
      Playlist exists but the version_no doesn't, reason is "no
      PlaylistVersion ..." (regen + retention cap can prune older
      versions; the disk file outlives the DB row).
    * Stray `.xml` files at the output-dir root (not inside any slug
      subdir) — orphaned with reason "outside any slug subdir".

    Safety:

    * Symlinks are skipped at every level (top-level subdirs AND
      inside subdirs).
    * Every delete target is canonicalized + verified to be under
      `_resolve_output_dir`, then `lstat`'d immediately before
      unlink to defeat TOCTOU symlink-swap attempts.

    Caveat: the canonicalization only protects against escapes from
    a benign output dir — if `playlist_output_dir` itself is set to a
    sensitive location (e.g. `~`), prune treats unknown subdirs there
    as orphans. The endpoint trusts the operator's Config setting.

    Idempotent: re-running with confirm=True after a successful
    prune finds no orphans (deleted files are gone; remaining files
    have valid DB matches).
    """
    out_dir = _resolve_output_dir(db)

    # Build the DB-side lookup tables once. selectinload collapses
    # the Station → Playlist → PlaylistVersion walk into 3 queries
    # instead of 1 + N + N*M; cheap insurance for >20 stations.
    stations_q = db.query(Station).options(
        selectinload(Station.playlists).selectinload(Playlist.versions),
    )
    if station_slug is not None:
        stations_q = stations_q.filter(Station.slug == station_slug)
    stations = stations_q.all()
    if station_slug is not None and not stations:
        raise ValueError(f"unknown station slug '{station_slug}'")
    known_slugs = {s.slug for s in stations}

    valid_week_keys: set[tuple[str, int]] = set()
    valid_version_keys: set[tuple[str, int, int]] = set()
    for s in stations:
        for p in s.playlists:
            valid_week_keys.add((s.slug, p.week))
            for v in p.versions:
                valid_version_keys.add((s.slug, p.week, v.version_no))

    orphans: list[dict] = []
    skipped: list[dict] = []
    scanned = 0

    if not out_dir.is_dir():
        return {"scanned": 0, "orphans": [], "deleted": 0, "skipped": []}

    # Walk each slug-subdir. If station_slug is set, restrict to
    # that subdir (still filter symlinks — the all-stations branch's
    # symlink guard must apply here too); else iterate every
    # immediate child of the output dir.
    if station_slug is not None:
        candidate = out_dir / station_slug
        subdirs = (
            [candidate]
            if candidate.is_dir() and not candidate.is_symlink()
            else []
        )
    else:
        try:
            subdirs = [
                p for p in out_dir.iterdir()
                if p.is_dir() and not p.is_symlink()
            ]
        except OSError as e:
            logger.warning("prune: cannot list %s: %s", out_dir, e)
            return {"scanned": 0, "orphans": [], "deleted": 0, "skipped": []}

        # Also catch stray .xml files dropped directly at the
        # output-dir root (no slug subdir). They never match the
        # writer's layout, so they're orphans by definition.
        try:
            for entry in out_dir.iterdir():
                if entry.is_file() and not entry.is_symlink():
                    scanned += 1
                    orphans.append({
                        "path": str(entry),
                        "reason": "outside any slug subdir",
                    })
        except OSError as e:
            logger.warning("prune: cannot list %s: %s", out_dir, e)

    for subdir in subdirs:
        subdir_slug = subdir.name
        is_known_station = subdir_slug in known_slugs
        try:
            entries = list(subdir.iterdir())
        except OSError as e:
            logger.warning("prune: cannot list %s: %s", subdir, e)
            continue
        for entry in entries:
            # Skip non-files (subdirs, sockets) + symlinks (defense
            # in depth; resolve()+relative_to is the unlink-time check).
            if not entry.is_file() or entry.is_symlink():
                continue
            scanned += 1
            if not is_known_station:
                orphans.append({
                    "path": str(entry),
                    "reason": f"slug '{subdir_slug}' is not a Station",
                })
                continue
            match = _PRUNE_FILENAME_RE.match(entry.name)
            if match is None:
                orphans.append({
                    "path": str(entry),
                    "reason": f"unexpected filename '{entry.name}'",
                })
                continue
            week = int(match.group("week"))
            v_str = match.group("version")
            # Check the broader (slug, week) miss first: a versioned
            # file whose Playlist row is gone should report "no
            # Playlist" not "no PlaylistVersion" — the parent miss
            # is the real diagnosis.
            if (subdir_slug, week) not in valid_week_keys:
                orphans.append({
                    "path": str(entry),
                    "reason": (
                        f"no Playlist for (slug={subdir_slug!r}, "
                        f"week={week})"
                    ),
                })
                continue
            if v_str is not None:
                version_no = int(v_str)
                if (subdir_slug, week, version_no) not in valid_version_keys:
                    orphans.append({
                        "path": str(entry),
                        "reason": (
                            f"no PlaylistVersion for (slug={subdir_slug!r}, "
                            f"week={week}, version_no={version_no})"
                        ),
                    })

    deleted = 0
    if confirm:
        remaining: list[dict] = []
        for orph in orphans:
            target = Path(orph["path"])
            # Canonicalize + verify the target is under the configured
            # output dir. Without this, a symlink-traversal trick or a
            # crafted `playlist_output_dir` could make us delete files
            # outside the intended dir.
            try:
                resolved = target.resolve()
                resolved.relative_to(out_dir)
            except (OSError, ValueError):
                logger.warning(
                    "prune: skipping %s (resolves outside %s)", target, out_dir,
                )
                skipped.append({
                    "path": orph["path"],
                    "reason": f"{orph['reason']} (skipped: outside output_dir)",
                })
                continue
            # TOCTOU guard: re-lstat the ORIGINAL path immediately
            # before unlink. A concurrent process could swap a plain
            # file for a symlink between scan and delete; if so we
            # bail rather than follow the symlink to its target.
            try:
                if target.is_symlink():
                    skipped.append({
                        "path": orph["path"],
                        "reason": (
                            f"{orph['reason']} (skipped: "
                            f"became symlink after scan)"
                        ),
                    })
                    continue
                target.unlink()
                deleted += 1
            except OSError as e:
                logger.warning("prune: unlink failed for %s: %s", target, e)
                skipped.append({
                    "path": orph["path"],
                    "reason": f"{orph['reason']} (skipped: {e})",
                })
        # Strip successfully-deleted entries from `orphans` so the
        # response shows what was found vs what's still on disk.
        skipped_paths = {s["path"] for s in skipped}
        remaining = [o for o in orphans if o["path"] in skipped_paths]
        orphans = remaining

    return {
        "scanned": scanned,
        "orphans": orphans,
        "deleted": deleted,
        "skipped": skipped,
    }
