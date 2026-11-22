"""An HTTP error is a response too: where Station Server reads the
message from one, it closes it after, rather than leaving it to the
garbage collector (Python 3.13 and later warn about each one left).
Each fake keeps a reference to the error it raises, so only an explicit
close() could close its body."""
from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from station_server import servercheck, upnp
from station_server.feed import FeedError, GitHubPages


def refusing(code: int, body: bytes):
    """An opener that answers every request with an HTTP error, and the
    bodies of those it raised."""
    bodies, kept = [], []

    def opener(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        bodies.append(io.BytesIO(body))
        kept.append(urllib.error.HTTPError(url, code, "No", {}, bodies[-1]))
        raise kept[-1]

    return opener, bodies


def test_github_pages_closes_githubs_error():
    opener, bodies = refusing(403, b'{"message": "Bad credentials"}')
    with pytest.raises(FeedError, match="Bad credentials"):
        GitHubPages("raby/rebirth-now", "tok", opener=opener).put("a.json", b"1", "m")
    assert bodies and all(b.closed for b in bodies)


def test_github_pages_closes_a_missing_files_404():
    opener, bodies = refusing(404, b"{}")
    # The GET for the current file finds nothing (None), then the write is refused.
    with pytest.raises(FeedError):
        GitHubPages("raby/rebirth-now", "tok", opener=opener).put("a.json", b"1", "m")
    assert bodies and all(b.closed for b in bodies)


def test_the_server_check_closes_an_error_page(monkeypatch):
    opener, bodies = refusing(401, b"<html>Unauthorized</html>")

    class Opener:
        def open(self, req, timeout=None):
            return opener(req, timeout)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    status, _headers, body = servercheck._fetch("http://127.0.0.1:1/")
    assert status == 401 and "Unauthorized" in body
    assert bodies and all(b.closed for b in bodies)


def test_the_router_closes_a_soap_fault():
    service = next(iter(upnp.SERVICES))
    description = (
        '<root xmlns="urn:schemas-upnp-org:device-1-0"><device><serviceList><service>'
        f"<serviceType>{service}</serviceType><controlURL>/ctl</controlURL>"
        "</service></serviceList></device></root>").encode()
    fault, bodies = refusing(500, b"<errorDescription>ConflictInMappingEntry</errorDescription>")

    def opener(req, timeout=None):
        if isinstance(req, str):  # the description
            return io.BytesIO(description)
        return fault(req, timeout)

    gateway = upnp.Gateway("http://192.168.1.1/desc.xml", opener=opener)
    with pytest.raises(upnp.UpnpError, match="ConflictInMappingEntry"):
        gateway._call("GetExternalIPAddress", {})
    assert bodies and all(b.closed for b in bodies)
