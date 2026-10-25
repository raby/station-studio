# PyInstaller recipe for the Mac and Windows downloads: Station Studio
# with its own Python and its built web pages, so it installs without a
# terminal, Python or Docker.
#
#   cd web && npm ci && npm run build && cd ..
#   pip install . pyinstaller certifi
#   pyinstaller packaging/station-studio.spec
#
# Makes dist/Station Studio.app (Mac) or dist/Station Studio/ (Windows),
# which .github/workflows/studio-installers.yml turns into a .dmg or a
# setup .exe. The bundled stations, migrations and web pages go where the
# wheel puts them (station_studio/_bundled/, see pyproject.toml), so
# bootstrap and the page mount find them the same way.
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
NAME = "Station Studio"
HELPER = "station-studio-server"  # keep in step with selfupdate.MAC_HELPER
MAC = sys.platform == "darwin"
WEB = ROOT / "web" / "build"
if not (WEB / "index.html").is_file():
    raise SystemExit("Build the web pages first: cd web && npm ci && npm run build")

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(WEB), "station_studio/_bundled/web"),
        (str(ROOT / "stations"), "station_studio/_bundled/stations"),
        (str(ROOT / "migrations"), "station_studio/_bundled/migrations"),
        (str(ROOT / "alembic.ini"), "station_studio/_bundled"),
    ],
    # Loaded by name at run time (routers, uvicorn's choices, the
    # migrations' imports), so PyInstaller can't follow them on its own.
    hiddenimports=[
        *collect_submodules("api"),
        *collect_submodules("engine"),
        *collect_submodules("station_studio"),
        *collect_submodules("uvicorn"),
        *collect_submodules("alembic", filter=lambda name: ".testing" not in name),
        "sqlalchemy.dialects.sqlite",
        "multipart",
        "python_multipart",
    ],
    excludes=["tkinter", "pytest", "test", "uvloop", "httptools", "watchfiles", "websockets"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,  # no terminal window: the browser is the UI
    icon=str(HERE / ("icon.icns" if MAC else "icon.ico")),
)
# On the Mac, the app the Finder opens only hands over to this helper and
# exits: a process macOS counts as the running app would swallow the next
# click on the app. The helper runs the server; it takes no Apple events,
# so macOS never counts it as the app. Updates restart it too.
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
        icon=str(HERE / "icon.icns"),
        bundle_identifier="io.github.raby.StationStudio",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            # The UI is the browser, so the app itself needs no Dock icon.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
