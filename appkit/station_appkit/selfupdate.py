"""Update a downloaded app from inside it (Update now), so nobody needs a
terminal or a fresh download.

The app fetches the new version's installer from its GitHub release and
uses it only when its size and SHA-256 match what GitHub publishes for
that file, and only from the project's own releases. On the Mac the new
app replaces the old one once the app has quit (the old one is kept until
the new one is in place); on Windows the setup runs silently and starts
the new version itself. Then the open page reloads into the new version.

Nothing here touches the app's data folder.
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

from station_appkit import App, updates

logger = logging.getLogger(__name__)

MAX_DOWNLOAD = 300 * 1024 * 1024
_TIMEOUT = 30


class UpdateError(Exception):
    """What went wrong, in words for the person updating."""


def bundled() -> bool:
    """True inside the downloaded Mac or Windows app (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def install_kind(platform_name: str | None = None, frozen: bool | None = None) -> str | None:
    """"mac" or "windows" for the downloaded apps, None otherwise (a pip,
    pipx or Docker install updates the way it was installed)."""
    if not (bundled() if frozen is None else frozen):
        return None
    platform_name = platform_name or sys.platform
    if platform_name == "darwin":
        return "mac"
    if platform_name == "win32":
        return "windows"
    return None


def asset_name(app: App, version: str, kind: str, machine: str | None = None) -> str:
    if kind == "windows":
        return f"{app.slug}-{version}-windows-setup.exe"
    machine = machine or platform.machine()
    # The same build as this one: an Intel app under Rosetta stays Intel.
    arch = "apple-silicon" if machine == "arm64" else "intel"
    return f"{app.slug}-{version}-mac-{arch}.dmg"


def fetch_json(url: str, user_agent: str = "station-appkit"):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read(5_000_000))


def release_asset(app: App, version: str, name: str, get_json=None) -> tuple[str, str, int]:
    """(download url, sha256, size) of one file of a release: only one the
    app's own release for that version serves, with its hash."""
    get_json = get_json or (lambda url: fetch_json(url, app.slug))
    try:
        release = get_json(app.release_api(version))
    except Exception as exc:
        raise UpdateError(f"Couldn't reach GitHub to download {version} ({exc}). Try again later.") from exc
    for asset in release.get("assets", []) if isinstance(release, dict) else []:
        if asset.get("name") != name:
            continue
        url, digest, size = asset.get("browser_download_url", ""), asset.get("digest") or "", asset.get("size")
        own = f"{app.download_prefix}{app.tag_prefix}{version}/"
        if not url.startswith(own) or not digest.startswith("sha256:") or not isinstance(size, int):
            break
        return url, digest.split(":", 1)[1].lower(), size
    raise UpdateError(f"The {version} download for this computer isn't ready yet. Try again in an hour or so.")


def download(url: str, dest: Path, sha256: str, size: int, progress=lambda done, total: None,
             opener=urllib.request.urlopen, user_agent: str = "station-appkit") -> None:
    """Fetch url to dest, refusing anything but the expected file."""
    if size > MAX_DOWNLOAD:
        raise UpdateError("The download is larger than expected, so it wasn't used.")
    digest = hashlib.sha256()
    done = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
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
        dest.unlink(missing_ok=True)
        raise UpdateError(f"The download stopped ({exc}). Try again.") from exc
    if done != size or digest.hexdigest() != sha256:
        dest.unlink(missing_ok=True)
        raise UpdateError("The download didn't match the release, so it wasn't used. Try again.")


def mac_app(app: App, executable: str | None = None) -> Path:
    """The running .app, which must be somewhere it can be replaced
    (Applications, not the disk image)."""
    exe = Path(executable or sys.executable)
    bundle = exe.parents[2] if len(exe.parents) > 2 else exe
    if bundle.suffix != ".app" or str(bundle).startswith("/Volumes/") or "/AppTranslocation/" in str(bundle):
        raise UpdateError(f"Move {app.name} into your Applications folder first, then update from there.")
    if not os.access(bundle.parent, os.W_OK) or not os.access(bundle, os.W_OK):
        raise UpdateError(f"{app.name} can't replace itself in {bundle.parent}. "
                          "Download the new version and drag it into Applications instead.")
    return bundle


def mac_unpack(app: App, dmg: Path, bundle: Path, version: str, run=subprocess.run) -> Path:
    """Copy the new app out of the disk image to beside the old one (so
    swapping them is a rename), and check it's the version expected."""
    work = Path(tempfile.mkdtemp(prefix=f"{app.temp_prefix}-dmg-"))
    mount = work / "mnt"
    mount.mkdir()
    new = bundle.with_name(f".{bundle.stem} {version}.app")
    shutil.rmtree(new, ignore_errors=True)
    try:
        run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount), str(dmg)],
            check=True, capture_output=True, timeout=120)
        try:
            run(["ditto", str(mount / f"{app.name}.app"), str(new)], check=True, capture_output=True, timeout=300)
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


def mac_swap_script(pid: int, bundle: Path, new: Path, relaunch: list[str], tidy: Path) -> str:
    """Shell script run once the app has quit: puts the new app in the old
    one's place (putting the old one back if that fails), then starts
    whichever is there."""
    q = shlex.quote
    old = bundle.with_name(f".{bundle.stem} old.app")
    return f"""#!/bin/sh
i=0
while kill -0 {pid} 2>/dev/null && [ $i -lt 300 ]; do sleep 0.2; i=$((i+1)); done
rm -rf {q(str(old))}
if mv {q(str(bundle))} {q(str(old))}; then
  if mv {q(str(new))} {q(str(bundle))}; then rm -rf {q(str(old))}; else mv {q(str(old))} {q(str(bundle))}; fi
fi
rm -rf {q(str(new))} {q(str(tidy))}
{" ".join(q(a) for a in relaunch)} >/dev/null 2>&1 &
"""


def windows_setup_command(setup: Path, log: Path) -> list[str]:
    # The setup closes what's left of the app, installs over it and starts
    # the new version (see each app's packaging/windows/installer.iss).
    return [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/LOG={log}"]


def detached(cmd: list[str], env: dict | None = None, **kwargs) -> None:
    """Start `cmd` on its own, to outlive this process."""
    env = {**os.environ, **(env or {})}
    if bundled():
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # the new app starts afresh
    flags = {}
    if sys.platform == "win32":
        flags["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, env=env, **flags, **kwargs)


class Updater:
    """Runs one update at a time in the background; `status()` is what the
    page shows. `quit` stops the app.

    `mac_relaunch(bundle, port)` is the command that starts the new Mac app
    (its helper) once it's in place; `child_env` goes in the environment of
    whatever is started. A subclass can add a way to update (Station
    Player's pip): an `update_<kind>(version)` method, listed in `KINDS`."""

    KINDS = ("mac", "windows")

    def __init__(self, app: App, quit, mac_relaunch=None, port: int | None = None, kind: str | None = None,
                 child_env: dict | None = None, get_json=None, opener=urllib.request.urlopen,
                 run=subprocess.run, launch=None) -> None:
        self.app = app
        self.quit = quit
        self.port = app.port if port is None else port
        self.kind = kind or install_kind()
        self.mac_relaunch = mac_relaunch or (lambda bundle, port: [
            f"{bundle}/Contents/MacOS/{app.mac_helper}", "--no-browser", "--port", str(port)])
        self.child_env = dict(child_env or {})
        self._get_json = get_json or (lambda url: fetch_json(url, app.slug))
        self._opener, self._run = opener, run
        self._launch = launch or (lambda cmd: detached(cmd, env=self.child_env))
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
        if self.kind not in self.KINDS:
            raise ValueError("Only the downloaded Mac and Windows apps can update themselves")
        if not updates.is_newer(version, self.app.version):
            raise ValueError(f"{self.app.name} {self.app.version} is already up to date")
        with self._lock:
            if self._status and self._status.get("stage") not in ("failed", None):
                raise ValueError(f"{self.app.name} is already updating")
            self._status = {"version": version, "stage": "downloading", "percent": 0, "error": None,
                            "page": self.app.release_page(version)}
        self.thread = threading.Thread(target=self._work, args=(version,), name="update", daemon=True)
        self.thread.start()

    def _work(self, version: str) -> None:
        try:
            getattr(self, f"update_{self.kind}")(version)
        except UpdateError as exc:
            logger.warning("Update to %s failed: %s", version, exc)
            self._set(stage="failed", error=str(exc))
        except Exception as exc:  # never leave the page waiting
            logger.exception("Update to %s failed", version)
            self._set(stage="failed", error=f"The update didn't work ({exc}).")

    def _fetch(self, version: str, folder: Path) -> Path:
        name = asset_name(self.app, version, self.kind)
        url, sha256, size = release_asset(self.app, version, name, self._get_json)
        dest = folder / name
        download(url, dest, sha256, size, opener=self._opener, user_agent=self.app.slug,
                 progress=lambda done, total: self._set(percent=int(done * 100 / max(total, 1))))
        return dest

    # Separate, so a test can stand in for the parts that need a real Mac.
    def mac_app(self) -> Path:
        return mac_app(self.app)

    def mac_unpack(self, dmg: Path, bundle: Path, version: str) -> Path:
        return mac_unpack(self.app, dmg, bundle, version, run=self._run)

    def update_mac(self, version: str) -> None:
        bundle = self.mac_app()
        folder = Path(tempfile.mkdtemp(prefix=f"{self.app.temp_prefix}-update-"))
        dmg = self._fetch(version, folder)
        self._set(stage="installing")
        new = self.mac_unpack(dmg, bundle, version)
        script = folder / "update.sh"
        script.write_text(mac_swap_script(os.getpid(), bundle, new, self.mac_relaunch(bundle, self.port), folder),
                          encoding="utf-8")
        self._set(stage="restarting")
        self._launch(["/bin/sh", str(script)])
        self.quit()

    def update_windows(self, version: str) -> None:
        folder = Path(tempfile.mkdtemp(prefix=f"{self.app.temp_prefix}-update-"))
        setup = self._fetch(version, folder)
        self._set(stage="restarting")
        self._launch(windows_setup_command(setup, folder / "setup.log"))
        self.quit()
