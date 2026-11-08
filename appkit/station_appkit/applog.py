"""The app's log file. The downloaded apps (and anything started at login
or from a shortcut) have no terminal, so they log to a file beside their
data: <name>.log, kept to a few megabytes (the last full one is kept as
<name>.log.1, and so on)."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_to_file(folder: Path, name: str, max_bytes: int = 2_000_000, backups: int = 2) -> Path | None:
    """Send the app's logging to folder/<name>.log. Returns the file, or
    None when it can't be written (then logging stays as it was)."""
    path = folder / f"{name}.log"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups,
                                                       encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return path
