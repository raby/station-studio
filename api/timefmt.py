"""Shared timestamp formatting for API responses.

Hoisted in §16.19 c1 review (🟢-2): the editor + stations routers had
byte-identical `_iso` helpers. One home now.
"""
from __future__ import annotations

from datetime import datetime


def iso(dt: datetime | None) -> str | None:
    """Full-precision ISO-8601 string for a datetime column, or None.

    Microsecond precision is intentional — it backs the §16.2.3
    optimistic-concurrency token (where exactness gates the 409) and
    the §16.19 station-overview timestamps. (The jobs router's
    whole-second `_iso_utc` is a separate, display-only formatter and
    stays where it is.)
    """
    return dt.isoformat() if dt is not None else None
