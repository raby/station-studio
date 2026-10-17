# PyInstaller recipe for the Mac and Windows downloads: Station Player
# with its own Python, so it installs without a terminal.
#
#   pip install pyinstaller certifi
#   pyinstaller player/packaging/station-player.spec
#
# Makes dist/Station Player.app (Mac) or dist/Station Player/ (Windows),
# which .github/workflows/player-installers.yml turns into a .dmg or a
# setup .exe.
import sys
import tomllib
from pathlib import Path

HERE = Path(SPECPATH)
ROOT = HERE.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
STATIC = ROOT / "station_player" / "static"
NAME = "Station Player"
HELPER = "station-player-server"  # keep in step with desktop.MAC_HELPER
MAC = sys.platform == "darwin"

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(STATIC), "station_player/static")],
    excludes=["tkinter", "unittest", "pydoc", "test"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,  # no terminal window: the player's own window is the UI
    argv_emulation=MAC,  # files from Open With, station-player: links
    icon=str(STATIC / ("icon.icns" if MAC else "icon.ico")),
)
# On the Mac, the app the Finder opens only hands over to this helper and
# exits: a process macOS counts as the running app would swallow the next
# click on the app. The helper runs the player; it takes no Apple events,
# so macOS never counts it as the app. Login, desktop icons and the
# station-player: link run it too.
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
        bundle_identifier="io.github.raby.StationPlayer",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            # The UI is a browser window, so the app itself needs no Dock icon.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
            "CFBundleURLTypes": [{"CFBundleURLName": "io.github.raby.StationPlayer",
                                  "CFBundleURLSchemes": ["station-player"]}],
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Audio",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",  # offered in Open With, never made the default
                "LSItemContentTypes": ["public.audio"],
            }],
        },
    )
