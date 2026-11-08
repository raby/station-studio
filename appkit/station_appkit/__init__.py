"""What Station Studio, Station Player and Station Server share as desktop
apps: the new-version check (updates), the self-updater (selfupdate), the
app window (window, and on the Mac macwindow), start at login (login) and
the log file (applog).

Each app describes itself once, as an `App`, and hands that in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__version__ = "1.1.0"

REPO = "raby/station-studio"


@dataclass(frozen=True)
class App:
    """One of the three apps, as the shared code needs to know it."""

    name: str
    """In words: "Station Studio"."""
    slug: str
    """Without the space: "StationStudio". Names the downloads
    (StationStudio-1.2.0-windows-setup.exe), the user agent and the
    Mac window's user agent (StationStudioApp/1.2.0)."""
    tag_prefix: str
    """Its releases' tags: "studio-v"."""
    version: str
    """The version running."""
    port: int
    """The port it serves on unless told otherwise."""
    mac_helper: str = ""
    """The Mac app's helper (Contents/MacOS/<this>), which runs the server;
    the app the Finder opens is its window."""
    env_prefix: str = ""
    """"STATION_STUDIO": STATION_STUDIO_INSPECT, STATION_STUDIO_SELFTEST."""
    window_size: tuple[int, int] = (1280, 860)
    """The app window's first size."""
    browser_args: tuple[str, ...] = field(default=())
    """More flags for the Chrome or Edge app window."""
    repo: str = REPO

    @property
    def default_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def releases_api(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/releases?per_page=100"

    def release_api(self, version: str) -> str:
        return f"https://api.github.com/repos/{self.repo}/releases/tags/{self.tag_prefix}{version}"

    def release_page(self, version: str) -> str:
        return f"https://github.com/{self.repo}/releases/tag/{self.tag_prefix}{version}"

    @property
    def download_prefix(self) -> str:
        """Only files from the project's own releases are used."""
        return f"https://github.com/{self.repo}/releases/download/"

    @property
    def temp_prefix(self) -> str:
        """"station-studio": for temporary folders."""
        return self.name.lower().replace(" ", "-")
