import pytest

from station_appkit import App


@pytest.fixture
def app():
    return App(name="Station Test", slug="StationTest", tag_prefix="test-v", version="1.2.0", port=8123,
               mac_helper="station-test-server", env_prefix="STATION_TEST")
