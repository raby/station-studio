"""The computer's own-name lookup happens in the background (hostaddr):
on some Macs it waits on mDNS for over a minute, and Settings, the
Station Studio link and the live input's instructions list addresses
while answering a request."""
import socket
import threading
import time

import pytest

from station_server import hostaddr, live, upnp


@pytest.fixture
def slow_lookup(monkeypatch):
    """gethostbyname_ex takes as long as the test says, then answers."""
    release = threading.Event()
    calls = []

    def lookup(name):
        calls.append(name)
        release.wait(5)
        return (name, [], ["192.168.50.7", "10.0.0.4"])

    hostaddr._reset_for_tests()
    monkeypatch.setattr(socket, "gethostbyname_ex", lookup)
    yield release, calls
    release.set()
    hostaddr._reset_for_tests()


def test_addresses_come_back_at_once_while_the_lookup_is_slow(slow_lookup):
    release, calls = slow_lookup
    for find in (live.lan_addresses, upnp.lan_addresses):
        started = time.monotonic()
        find()
        assert time.monotonic() - started < 1
    assert hostaddr.host_addresses() == []
    assert len(calls) == 1  # one lookup, however often it's asked

    release.set()
    for _ in range(100):
        if hostaddr.host_addresses():
            break
        time.sleep(0.01)
    assert hostaddr.host_addresses() == ["192.168.50.7", "10.0.0.4"]
    assert {"192.168.50.7", "10.0.0.4"} <= set(live.lan_addresses())
    assert {"192.168.50.7", "10.0.0.4"} <= set(upnp.lan_addresses())
    assert len(calls) == 1


def test_a_lookup_that_fails_gives_nothing(monkeypatch):
    def fail(name):
        raise socket.gaierror("no such name")

    hostaddr._reset_for_tests()
    monkeypatch.setattr(socket, "gethostbyname_ex", fail)
    hostaddr.host_addresses()
    for _ in range(100):
        if hostaddr._found is not None:
            break
        time.sleep(0.01)
    assert hostaddr.host_addresses() == []
    hostaddr._reset_for_tests()
