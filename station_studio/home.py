"""§16.13 c3 — `$STATION_STUDIO_HOME` resolution.

Locked design D6 (and the c1 working-dir banner that flagged this as
queued for c3): an installed-wheel user can't write into site-
packages, so the DB / stations / exports all live under an XDG-style
app-home dir. By default `~/.station-studio/`; overridable via the
`STATION_STUDIO_HOME` env var (handy for tests + multi-instance
setups).

This module ONLY resolves paths + creates dirs. The actual
DB-migration + stations-copy steps live in `station_studio.bootstrap`
— this module is intentionally side-effect-free apart from
`ensure_home()`'s `mkdir`.
"""
from __future__ import annotations

import os
from pathlib import Path


ENV_VAR = "STATION_STUDIO_HOME"
DEFAULT_DIRNAME = ".station-studio"


def get_home() -> Path:
    """Return the app-home path. Does NOT create it.

    Resolution order:
      1. `$STATION_STUDIO_HOME` (explicit override; tilde-expanded).
      2. `~/.station-studio/` (the conventional default).
    """
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DIRNAME


def ensure_home() -> Path:
    """Create the app-home + its standard subdirs if missing.

    Always-created subdirs:
      * `stations/` — committed station JSONs + sidecars
      * `exports/`  — generated iTunes XMLs from the export endpoint

    Idempotent — calling on an existing app-home is a no-op for the
    dirs that already exist.
    """
    home = get_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "stations").mkdir(exist_ok=True)
    (home / "exports").mkdir(exist_ok=True)
    return home


def db_path() -> Path:
    """Path to the SQLite DB file inside the app-home."""
    return get_home() / "station_studio.db"


def db_url() -> str:
    """SQLAlchemy URL for the app-home's SQLite DB."""
    return f"sqlite:///{db_path()}"


def stations_dir() -> Path:
    """The stations dir inside the app-home."""
    return get_home() / "stations"


def exports_dir() -> Path:
    """The exports dir inside the app-home."""
    return get_home() / "exports"
