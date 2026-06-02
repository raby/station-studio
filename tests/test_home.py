"""§16.13 c3 — app-home path resolution tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from station_studio import home


def test_get_home_uses_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`$STATION_STUDIO_HOME` wins over the default. Lets users
    pick their own location (multi-instance setups, XDG strictness,
    tests pointing at tmp_path)."""
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("STATION_STUDIO_HOME", str(custom))
    assert home.get_home() == custom


def test_get_home_tilde_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`~` in the env var expands to the user's home dir. Without
    this a user setting `STATION_STUDIO_HOME=~/data` would get a
    literal `~/data` directory in their cwd."""
    monkeypatch.setenv("STATION_STUDIO_HOME", "~/customssdir")
    resolved = home.get_home()
    assert "~" not in str(resolved)
    assert str(resolved).endswith("customssdir")


def test_get_home_default_under_user_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No env var → `~/.station-studio/`. The conventional dot-dir
    under the user's home; XDG strictness lives in t-followups."""
    monkeypatch.delenv("STATION_STUDIO_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert home.get_home() == tmp_path / ".station-studio"


def test_get_home_empty_env_var_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty/whitespace `STATION_STUDIO_HOME` is treated as unset
    (matches how the API endpoint env-override pattern works in
    api/deps.py). Without this an accidental `STATION_STUDIO_HOME=`
    in a wrapper script would create a literal-named directory."""
    monkeypatch.setenv("STATION_STUDIO_HOME", "   ")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert home.get_home() == tmp_path / ".station-studio"


def test_ensure_home_creates_dir_and_subdirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ensure_home() is the side-effect step — creates the dirs.
    Idempotent: calling it twice is a no-op for the second call."""
    target = tmp_path / "new-home"
    monkeypatch.setenv("STATION_STUDIO_HOME", str(target))

    result = home.ensure_home()
    assert result == target
    assert target.is_dir()
    assert (target / "stations").is_dir()
    assert (target / "exports").is_dir()

    # Second call: idempotent.
    home.ensure_home()


def test_paths_resolve_inside_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """db_path / db_url / stations_dir / exports_dir all hang off
    get_home()'s result. If a future commit accidentally hardcoded a
    Path.home() reference, the env override would silently miss
    those callers."""
    custom = tmp_path / "ss-test"
    monkeypatch.setenv("STATION_STUDIO_HOME", str(custom))
    assert home.db_path() == custom / "station_studio.db"
    assert home.db_url() == f"sqlite:///{custom / 'station_studio.db'}"
    assert home.stations_dir() == custom / "stations"
    assert home.exports_dir() == custom / "exports"
