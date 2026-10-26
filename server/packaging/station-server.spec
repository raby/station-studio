# PyInstaller recipe for the Mac and Windows downloads: Station Server with
# its own Python and its own ffmpeg, so it installs without a terminal.
#
#   pip install pyinstaller certifi ../player .
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
    argv_emulation=MAC,  # station-server: links arrive as arguments
    icon=str(STATIC / ("icon.icns" if MAC else "icon.ico")),
)
# On the Mac, the app the Finder opens is Station Server's window, with its
# Dock icon and menus (macwindow.py), and this helper runs Station Server,
# so closing the window leaves the stations on air. Start at login runs
# only the helper.
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
            "NSHighResolutionCapable": True,
            # station-server://start, from the installed app's window when
            # Station Server isn't running (see __main__.URL_SCHEME).
            "CFBundleURLTypes": [{"CFBundleURLName": "io.github.raby.StationServer",
                                  "CFBundleURLSchemes": ["station-server"]}],
        },
    )
