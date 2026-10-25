"""Update the downloaded Station Server app from inside it (the Update now
button), so nobody needs a terminal or a fresh download.

The app fetches the new version's installer from its GitHub release and
uses it only when its SHA-256 matches the one GitHub publishes for that
file. On the Mac the new app replaces the old one once Station Server has
quit (the old one is kept until the new one is in place); on Windows the
setup runs silently and starts the new version itself. Then the open page
reloads into the new version.

Nothing here touches the data folder (~/.station-server): stations, play
logs and settings stay where they are. Stations that were on air go back
on air when the new version starts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

from station_server import __version__, updates
from station_server.desktop import CHILD_ENV, MAC_HELPER, bundled

logger = logging.getLogger(__name__)

REPO = updates.REPO
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/tags/{updates.TAG_PREFIX}{{version}}"
RELEASE_PAGE = f"https://github.com/{REPO}/releases/tag/{updates.TAG_PREFIX}{{version}}"
# Only files from the project's own releases are used.
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
MAX_DOWNLOAD = 300 * 1024 * 1024
APP_NAME = "Station Server"
_TIMEOUT = 30


class UpdateError(Exception):
    """What went wrong, in words for the person updating."""


def install_kind(platform_name: str | None = None) -> str | None:
    """"mac" or "windows" for the downloaded apps, None otherwise (a pip,
    pipx or Docker install updates the way it was installed)."""
    if not bundled():
        return None
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return "mac"
    if platform_name == "win32":
        return "windows"
    return None


def asset_name(version: str, kind: str, machine: str | None = None) -> str:
    if kind == "windows":
        return f"StationServer-{version}-windows-setup.exe"
    machine = machine or platform.machine()
    # The same build as this one: an Intel app under Rosetta stays Intel.
    arch = "apple-silicon" if machine == "arm64" else "intel"
    return f"StationServer-{version}-mac-{arch}.dmg"


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "StationServer"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read(5_000_000))


def release_asset(version: str, name: str, get_json=_get_json) -> tuple[str, str, int]:
    """(download url, sha256, size) of one file of a release."""
    try:
        release = get_json(RELEASE_API.format(version=version))
    except Exception as exc:
        raise UpdateError(f"Couldn't reach GitHub to download {version} ({exc}). Try again later.") from exc
    for asset in release.get("assets", []) if isinstance(release, dict) else []:
        if asset.get("name") != name:
            continue
        url, digest, size = asset.get("browser_download_url", ""), asset.get("digest") or "", asset.get("size")
        if not url.startswith(DOWNLOAD_PREFIX) or not digest.startswith("sha256:") or not isinstance(size, int):
            break
        return url, digest.split(":", 1)[1].lower(), size
    raise UpdateError(f"The {version} download for this computer isn't ready yet. Try again in an hour or so.")


def download(url: str, dest: Path, sha256: str, size: int, progress=lambda done, total: None,
             opener=urllib.request.urlopen) -> None:
    """Fetch url to dest, refusing anything but the expected file."""
    if size > MAX_DOWNLOAD:
        raise UpdateError("The download is larger than expected, so it wasn't used.")
    digest = hashlib.sha256()
    done = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StationServer"})
        with opener(req, timeout=_TIMEOUT) as resp, open(dest, "wb") as out:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                done += len(chunk)
                if done > size:
                    break
                digest.update(chunk)
                out.write(chunk)
                progress(done, size)
    except OSError as exc:
        raise UpdateError(f"The download stopped ({exc}). Try again.") from exc
    if done != size or digest.hexdigest() != sha256:
        dest.unlink(missing_ok=True)
        raise UpdateError("The download didn't match the release, so it wasn't used. Try again.")


def mac_app(executable: str | None = None) -> Path:
    """The running Station Server.app, which must be somewhere it can be
    replaced (Applications, not the disk image)."""
    exe = Path(executable or sys.executable)
    app = exe.parents[2] if len(exe.parents) > 2 else exe
    if app.suffix != ".app" or str(app).startswith("/Volumes/") or "/AppTranslocation/" in str(app):
        raise UpdateError("Move Station Server into your Applications folder first, then update from there.")
    if not os.access(app.parent, os.W_OK) or not os.access(app, os.W_OK):
        raise UpdateError(f"Station Server can't replace itself in {app.parent}. "
                          "Download the new version and drag it into Applications instead.")
    return app


def mac_unpack(dmg: Path, app: Path, version: str, run=subprocess.run) -> Path:
    """Copy the new app out of the disk image to beside the old one (so
    swapping them is a rename), and check it's the version expected."""
    work = Path(tempfile.mkdtemp(prefix="station-server-dmg-"))
    mount = work / "mnt"
    mount.mkdir()
    new = app.with_name(f".{app.stem} {version}.app")
    shutil.rmtree(new, ignore_errors=True)
    try:
        run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount), str(dmg)],
            check=True, capture_output=True, timeout=120)
        try:
            run(["ditto", str(mount / f"{APP_NAME}.app"), str(new)], check=True, capture_output=True, timeout=300)
        finally:
            run(["hdiutil", "detach", str(mount), "-force"], capture_output=True, timeout=60)
        with open(new / "Contents" / "Info.plist", "rb") as f:
            found = plistlib.load(f).get("CFBundleShortVersionString")
        if found != version:
            raise UpdateError(f"The download held version {found}, not {version}, so it wasn't used.")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        shutil.rmtree(new, ignore_errors=True)
        raise UpdateError(f"Couldn't open the download ({exc}). Try again.") from exc
    except UpdateError:
        shutil.rmtree(new, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return new


def mac_swap_script(pid: int, app: Path, new: Path, relaunch: list[str], tidy: Path) -> str:
    """Shell script run once Station Server has quit: puts the new app in
    the old one's place (putting the old one back if that fails), then
    starts whichever is there."""
    q = shlex.quote
    old = app.with_name(f".{app.stem} old.app")
    return f"""#!/bin/sh
i=0
while kill -0 {pid} 2>/dev/null && [ $i -lt 300 ]; do sleep 0.2; i=$((i+1)); done
rm -rf {q(str(old))}
if mv {q(str(app))} {q(str(old))}; then
  if mv {q(str(new))} {q(str(app))}; then rm -rf {q(str(old))}; else mv {q(str(old))} {q(str(app))}; fi
fi
rm -rf {q(str(new))} {q(str(tidy))}
{" ".join(q(a) for a in relaunch)} >/dev/null 2>&1 &
"""


def windows_setup_command(setup: Path, log: Path) -> list[str]:
    # The setup closes what's left of Station Server, installs over it and
    # starts the new version (see packaging/windows/installer.iss).
    return [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/LOG={log}"]


def mac_relaunch_command(app: Path, port: int) -> list[str]:
    # The helper inside the app serves; the page that's open reloads into it.
    return [str(app / "Contents" / "MacOS" / MAC_HELPER), "app", "--no-window", "--port", str(port)]


def _detached(cmd: list[str]) -> None:
    env = {**os.environ, CHILD_ENV: "1", "PYINSTALLER_RESET_ENVIRONMENT": "1"}  # the new app starts afresh
    flags = {}
    if sys.platform == "win32":
        flags["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, env=env, **flags)


class Updater:
    """Runs one update at a time in the background; `status()` is what
    the page shows. `quit` stops Station Server."""

    def __init__(self, quit, port: int = 8770, kind: str | None = None, get_json=_get_json,
                 opener=urllib.request.urlopen, run=subprocess.run, launch=_detached) -> None:
        self.quit = quit
        self.port = port
        self.kind = kind or install_kind()
        self._get_json, self._opener, self._run, self._launch = get_json, opener, run, launch
        self._lock = threading.Lock()
        self._status: dict | None = None
        self.thread: threading.Thread | None = None

    def status(self) -> dict | None:
        with self._lock:
            return dict(self._status) if self._status else None

    def _set(self, **fields) -> None:
        with self._lock:
            self._status = {**(self._status or {}), **fields}

    def start(self, version: str) -> None:
        """Raises ValueError when there's nothing to update to."""
        if self.kind not in ("mac", "windows"):
            raise ValueError("Only the downloaded Mac and Windows apps can update themselves")
        if not updates.is_newer(version, __version__):
            raise ValueError(f"Station Server {__version__} is already up to date")
        with self._lock:
            if self._status and self._status.get("stage") not in ("failed", None):
                raise ValueError("Station Server is already updating")
            self._status = {"version": version, "stage": "downloading", "percent": 0, "error": None,
                            "page": RELEASE_PAGE.format(version=version)}
        self.thread = threading.Thread(target=self._work, args=(version,), name="update", daemon=True)
        self.thread.start()

    def _work(self, version: str) -> None:
        try:
            {"mac": self._mac, "windows": self._windows}[self.kind](version)
        except UpdateError as exc:
            logger.warning("Update to %s failed: %s", version, exc)
            self._set(stage="failed", error=str(exc))
        except Exception as exc:  # never leave the page waiting
            logger.exception("Update to %s failed", version)
            self._set(stage="failed", error=f"The update didn't work ({exc}).")

    def _fetch(self, version: str, folder: Path) -> Path:
        name = asset_name(version, self.kind)
        url, sha256, size = release_asset(version, name, self._get_json)
        dest = folder / name
        download(url, dest, sha256, size, opener=self._opener,
                 progress=lambda done, total: self._set(percent=int(done * 100 / max(total, 1))))
        return dest

    def _mac(self, version: str) -> None:
        app = mac_app()
        folder = Path(tempfile.mkdtemp(prefix="station-server-update-"))
        dmg = self._fetch(version, folder)
        self._set(stage="installing")
        new = mac_unpack(dmg, app, version, run=self._run)
        script = folder / "update.sh"
        script.write_text(mac_swap_script(os.getpid(), app, new, mac_relaunch_command(app, self.port), folder),
                          encoding="utf-8")
        self._set(stage="restarting")
        self._launch(["/bin/sh", str(script)])
        self.quit()

    def _windows(self, version: str) -> None:
        folder = Path(tempfile.mkdtemp(prefix="station-server-update-"))
        setup = self._fetch(version, folder)
        self._set(stage="restarting")
        self._launch(windows_setup_command(setup, folder / "setup.log"))
        self.quit()
