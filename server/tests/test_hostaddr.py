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
    _wait_for(lambda: len(calls) == 1)  # started in the background...
    hostaddr.host_addresses()
    assert len(calls) == 1  # ...and only once, however often it's asked

    release.set()
    for _ in range(100):
        if hostaddr.host_addresses():
            break
        time.sleep(0.01)
    assert hostaddr.host_addresses() == ["192.168.50.7", "10.0.0.4"]
    assert {"192.168.50.7", "10.0.0.4"} <= set(live.lan_addresses())
    assert {"192.168.50.7", "10.0.0.4"} <= set(upnp.lan_addresses())
    assert len(calls) == 1


def test_a_minute_on_the_addresses_are_looked_up_again_in_the_background(monkeypatch):
    """A laptop moves between networks: the list is refreshed, and the
    old one is given meanwhile rather than waiting."""
    answers = iter([["192.168.1.5"], ["10.1.1.9"]])
    calls = []
    gate = threading.Event()

    def lookup(name):
        calls.append(name)
        if len(calls) == 2:
            gate.wait(5)
        return (name, [], next(answers))

    hostaddr._reset_for_tests()
    monkeypatch.setattr(socket, "gethostbyname_ex", lookup)
    hostaddr.host_addresses()
    _wait_for(lambda: hostaddr.host_addresses() == ["192.168.1.5"])
    assert len(calls) == 1  # fresh: not looked up again

    hostaddr._at -= hostaddr.REFRESH_SECONDS + 1  # a minute later
    started = time.monotonic()
    assert hostaddr.host_addresses() == ["192.168.1.5"]  # the old list, at once
    assert time.monotonic() - started < 1
    _wait_for(lambda: len(calls) == 2)  # the refresh, in the background
    gate.set()
    _wait_for(lambda: hostaddr.host_addresses() == ["10.1.1.9"])
    assert len(calls) == 2
    hostaddr._reset_for_tests()


def _wait_for(check, seconds: float = 2.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_a_lookup_that_fails_gives_nothing(monkeypatch):
    def fail(name):
        raise socket.gaierror("no such name")

    hostaddr._reset_for_tests()
    monkeypatch.setattr(socket, "gethostbyname_ex", fail)
    hostaddr.host_addresses()
    _wait_for(lambda: hostaddr._found is not None)
    assert hostaddr.host_addresses() == []
    hostaddr._reset_for_tests()
