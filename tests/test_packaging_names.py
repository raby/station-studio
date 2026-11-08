"""Each app's names, as its code knows them (appinfo.py), match what its
packaging builds and releases. Update now looks for exactly the files the
installer workflows make (StationPlayer-0.24.0-mac-apple-silicon.dmg), in
the release the publish workflow tags (player-v0.24.0), and the Mac app's
helper is the one the PyInstaller spec builds: a rename in one place and
not the other would only show when someone pressed Update now."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from station_appkit.selfupdate import asset_name

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

APPS = {
    # app module, its folder, spec, installer, installers workflow, release workflow
    "studio": ("station_studio", ROOT, "packaging/station-studio.spec", "packaging/windows/installer.iss",
               "studio-installers.yml", "publish-studio-app.yml"),
    "player": ("station_player", ROOT / "player", "player/packaging/station-player.spec",
               "player/packaging/windows/installer.iss", "player-installers.yml", "publish-player.yml"),
    "server": ("station_server", ROOT / "server", "server/packaging/station-server.spec",
               "server/packaging/windows/installer.iss", "server-installers.yml", "publish-server.yml"),
}


def app_of(module: str):
    return __import__(f"{module}.appinfo", fromlist=["APP"]).APP


@pytest.mark.parametrize("key", APPS)
def test_version_is_the_same_everywhere(key):
    module, folder, *_ = APPS[key]
    app = app_of(module)
    pyproject = tomllib.loads((folder / "pyproject.toml").read_text())
    assert app.version == pyproject["project"]["version"] == __import__(module).__version__


@pytest.mark.parametrize("key", APPS)
def test_the_mac_app_and_its_helper_are_what_the_spec_builds(key):
    module, _, spec, *_ = APPS[key]
    app = app_of(module)
    text = (ROOT / spec).read_text()
    assert re.search(r'^NAME = "([^"]+)"', text, re.M).group(1) == app.name  # unpacked from the .dmg by name
    assert re.search(r'^HELPER = "([^"]+)"', text, re.M).group(1) == app.mac_helper


@pytest.mark.parametrize("key", APPS)
def test_downloads_are_named_as_update_now_looks_for_them(key):
    module, _, _, iss, installers, _ = APPS[key]
    app = app_of(module)
    setup = (ROOT / iss).read_text()
    assert f"OutputBaseFilename={asset_name(app, '{#Version}', 'windows').removesuffix('.exe')}" in setup
    workflow = (WORKFLOWS / installers).read_text()
    version, arch = "${{ steps.version.outputs.version }}", "${{ matrix.arch }}"
    dmg = asset_name(app, version, "mac", "arm64").replace("apple-silicon", arch)
    assert f'"out/{dmg}"' in workflow
    # The matrix's Mac arches are the ones asset_name gives (arm64 → apple-silicon, else intel).
    assert {asset_name(app, "1", "mac", m).split("-mac-")[1] for m in ("arm64", "x86_64")} == \
        {"apple-silicon.dmg", "intel.dmg"}
    assert re.search(r"^\s+arch: apple-silicon$", workflow, re.M) and re.search(r"^\s+arch: intel$", workflow, re.M)
    # The Mac window's self-test looks for the user agent the window sets.
    assert f'"{app.slug}App/" in report["agent"]' in workflow


@pytest.mark.parametrize("key", APPS)
def test_releases_are_tagged_as_the_update_check_reads_them(key):
    module, *_, publish = APPS[key]
    app = app_of(module)
    assert f'gh release create "{app.tag_prefix}$VERSION"' in (WORKFLOWS / publish).read_text()


def test_the_three_apps_never_share_a_name_port_or_tag():
    apps = [app_of(m) for m, *_ in APPS.values()]
    for attr in ("name", "slug", "tag_prefix", "port", "mac_helper", "env_prefix"):
        values = [getattr(a, attr) for a in apps]
        assert len(set(values)) == len(values), attr
