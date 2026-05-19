"""Backfill PlaylistEntry.slot_type for playlists generated before §14.31.

The §14.31 column is nullable so existing rows remained valid post-
migration. This script walks every PlaylistTrace, looks up its slots'
strategy values by position, and writes them into the matching
PlaylistEntry rows. Idempotent: rows whose slot_type is already
populated are skipped.

Usage
-----
    # Backfill every playlist with at least one null slot_type
    python -m scripts.backfill_slot_types

    # Dry run — print what WOULD be updated without writing
    python -m scripts.backfill_slot_types --dry-run

    # Restrict to one station's playlists
    python -m scripts.backfill_slot_types --station rebirth-classics

Exit code 0 always (idempotent reruns); count of updated rows printed
to stdout. If a playlist's trace is missing or its slots don't align
with the entries (position mismatch, count mismatch), the playlist is
skipped with a warning — these are unusual states the script
shouldn't try to invent fixes for.
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy.orm import joinedload

from engine.database import make_engine, make_sessionmaker
from engine.models import Playlist, PlaylistEntry, PlaylistTrace, Station


logger = logging.getLogger(__name__)


def backfill(
    session_factory,
    *,
    station_slug: str | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Walk every playlist (optionally filtered by station_slug) and
    populate slot_type for any entry whose value is currently null.

    Returns (playlists_touched, rows_updated).
    """
    with session_factory() as db:
        q = (
            db.query(Playlist)
            .options(joinedload(Playlist.entries), joinedload(Playlist.trace))
        )
        if station_slug is not None:
            station = db.query(Station).filter(Station.slug == station_slug).first()
            if station is None:
                logger.error("station %r not found", station_slug)
                return (0, 0)
            q = q.filter(Playlist.station_id == station.id)

        playlists_touched = 0
        rows_updated = 0
        for pl in q.all():
            trace: PlaylistTrace | None = pl.trace
            if trace is None or not trace.slots:
                logger.warning(
                    "playlist %d (station %d, week %d): no trace; skipping",
                    pl.id, pl.station_id, pl.week,
                )
                continue

            # Slots persist as a list of dicts in the JSON column. They
            # carry `slot` (1-indexed position) + `strategy`. Build a
            # position→strategy map; the trace's slot order must match
            # the entry's position order, but use the dict lookup
            # defensively in case ordering ever diverges.
            slot_strategies: dict[int, str] = {
                int(s["slot"]): s["strategy"] for s in trace.slots
            }

            entries: list[PlaylistEntry] = pl.entries
            updated_this_playlist = 0
            for entry in entries:
                if entry.slot_type is not None:
                    continue  # already populated — idempotent skip
                strategy = slot_strategies.get(entry.position)
                if strategy is None:
                    logger.warning(
                        "playlist %d entry position %d: no matching trace "
                        "slot; skipping entry",
                        pl.id, entry.position,
                    )
                    continue
                if not dry_run:
                    entry.slot_type = strategy
                updated_this_playlist += 1

            if updated_this_playlist:
                playlists_touched += 1
                rows_updated += updated_this_playlist
                logger.info(
                    "playlist %d (week %d): %s %d entries",
                    pl.id, pl.week,
                    "would update" if dry_run else "updated",
                    updated_this_playlist,
                )

        if not dry_run:
            db.commit()

        return (playlists_touched, rows_updated)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument(
        "--station",
        help="restrict to one station slug (default: every playlist)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes without writing",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="enable INFO-level logging",
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        format="%(levelname)s %(name)s: %(message)s",
        level=logging.INFO if args.verbose else logging.WARNING,
    )

    engine = make_engine()
    SessionLocal = make_sessionmaker(engine)
    playlists, rows = backfill(
        SessionLocal,
        station_slug=args.station,
        dry_run=args.dry_run,
    )
    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {rows} entries across {playlists} playlists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
