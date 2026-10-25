# PyInstaller recipe for the Mac and Windows downloads: Station Server with
# its own Python and its own ffmpeg, so it installs without a terminal.
#
#   pip install pyinstaller ../player .
#   (put an ffmpeg build at packaging/ffmpeg/ffmpeg or ffmpeg.exe)
#   pyinstaller packaging/station-server.spec
#
# Makes dist/Station Server.app (Mac) or dist/Station Server/ (Windows),
# which .github/workflows/server-installers.yml turns into a .dmg or a
# setup .exe.
import sys
import tomllib
from pathlib import Path

HERE = Path(SPECPATH)
ROOT = HERE.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
STATIC = ROOT / "station_server" / "static"
NAME = "Station Server"
HELPER = "station-server-helper"  # keep in step with desktop.MAC_HELPER
MAC = sys.platform == "darwin"
FFMPEG = HERE / "ffmpeg" / ("ffmpeg" if sys.platform != "win32" else "ffmpeg.exe")
FFMPEG_LICENSE = HERE / "ffmpeg" / "LICENSE.txt"

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[(str(FFMPEG), "ffmpeg")] if FFMPEG.is_file() else [],
    datas=[(str(STATIC), "station_server/static"), (str(ROOT / "LICENSE"), ".")]
          + ([(str(FFMPEG_LICENSE), "ffmpeg")] if FFMPEG_LICENSE.is_file() else []),
    excludes=["tkinter", "unittest", "pydoc", "test"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,  # no terminal window: Station Server's own window is the UI
    icon=str(STATIC / ("icon.icns" if MAC else "icon.ico")),
)
# On the Mac, the app the Finder opens starts this helper and exits: while
# a process macOS counts as the running app lives, later clicks on the app
# go to it and do nothing. The helper runs Station Server; start at login
# runs it too.
helper = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=HELPER,
    console=False,
) if MAC else None
coll = COLLECT(exe, *([helper] if helper else []), a.binaries, a.datas, name=NAME)

if MAC:
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=str(STATIC / "icon.icns"),
        bundle_identifier="io.github.raby.StationServer",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            # The UI is a browser window, so the app itself needs no Dock icon.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
