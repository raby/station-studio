"""Starting a server doesn't look up the computer's name: HTTPServer's
own server_bind calls socket.getfqdn(host), which for 0.0.0.0 can wait on
mDNS for over a minute on some Macs, and the phone access and Station
Studio link servers start while Settings is answering."""
import socket
import time

from station_server.web import _Server
from http.server import BaseHTTPRequestHandler


def test_binding_doesnt_wait_on_a_name_lookup(monkeypatch):
    def slow(name=""):
        time.sleep(5)
        return "slow.local"

    monkeypatch.setattr(socket, "getfqdn", slow)
    started = time.monotonic()
    server = _Server(("0.0.0.0", 0), BaseHTTPRequestHandler)
    try:
        assert time.monotonic() - started < 1
        assert server.server_port == server.server_address[1] > 0
    finally:
        server.server_close()
