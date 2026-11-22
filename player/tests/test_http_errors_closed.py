"""Every fetch that shrugs off an HTTP error closes it first: an error is a
response too, and one left for the garbage collector is a leak Python
3.13+ warns about (pytest turns that into a failure here)."""
from __future__ import annotations

import gc
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from station_player import __main__ as cli
from station_player import directory, picks, server, whatson
from station_player.httperrors import closing_errors


class _NotFound(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_error(404)

    do_POST = do_GET

    def log_message(self, *args):
        pass


@pytest.fixture
def missing():
    """The address of a server that answers everything with a 404."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _NotFound)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _caught(call):
    """Run `call`, which should fail, and hand back what it raised."""
    with pytest.raises(Exception) as info:
        call()
    return info.value


def _404(exc):
    while exc is not None and not isinstance(exc, urllib.error.HTTPError):
        exc = exc.__cause__ or exc.__context__
    assert exc is not None and exc.code == 404
    return exc


@pytest.mark.parametrize("fetch", [
    pytest.param(lambda url: directory._fetch(url, "test", 1024), id="station directory"),
    pytest.param(lambda url: picks._fetch(url, 1024), id="picks"),
    pytest.param(lambda url: whatson._urlopen(url, 2), id="what's on"),
])
def test_a_fetch_that_404s_lets_the_error_go_closed(missing, fetch):
    exc = _404(_caught(lambda: fetch(missing + "nothing")))
    assert exc.fp.closed
    del exc
    gc.collect()  # would warn here if it weren't closed


def test_a_playlist_link_that_404s_lets_the_error_go_closed(missing):
    exc = _404(_caught(lambda: server._fetch_text(missing + "x.pls", deadline=2)))
    assert exc.fp.closed


def test_asking_what_answers_on_the_port_lets_a_404_go(missing):
    assert cli._running_instance(missing) is False
    assert cli._outdated_instance(missing) is False
    gc.collect()


def test_an_error_without_a_body_goes_through_untouched():
    bare = urllib.error.HTTPError("http://x/", 500, "Server Error", {}, None)

    def refuse(*args, **kwargs):
        raise bare

    assert _caught(lambda: closing_errors(refuse)("http://x/")) is bare
