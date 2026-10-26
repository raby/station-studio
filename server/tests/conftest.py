import json
import math
import plistlib
import shutil
import socket
import struct
import sys
import threading
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# station-player provides the playlist reading; use the copy in this repo.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "player"))

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")


class Unpaced:
    """A pacer that never waits: plays a whole queue as fast as ffmpeg can."""

    def wait(self, nbytes: int) -> None:
        pass

    def ahead(self) -> float:
        return 0.0


def write_wav(path: Path, seconds: float = 0.5, freq: float = 440.0, rate: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(frames)
        ))
    return path


def write_itunes_xml(path: Path, tracks: list[dict], playlists: list[dict]) -> Path:
    data = {
        "Major Version": 1,
        "Minor Version": 1,
        "Tracks": {str(t["Track ID"]): t for t in tracks},
        "Playlists": playlists,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(data, fh)
    return path


def station_export(tmp_path: Path, files: list[Path], name: str = "Rebirth Classics - Week 12",
                   titles: list[tuple[str, str]] | None = None) -> Path:
    """An XML shaped like a station-studio export: one playlist of `files`."""
    tracks = []
    for i, f in enumerate(files, start=1):
        artist, title = titles[i - 1] if titles else (f"Artist {i}", f"Song {i}")
        tracks.append({"Track ID": i, "Name": title, "Artist": artist,
                       "Location": f.resolve().as_uri(), "Persistent ID": f"PID{i:04d}"})
    playlist = {"Name": name, "Playlist Persistent ID": "WEEK12",
                "Playlist Items": [{"Track ID": i} for i in range(1, len(files) + 1)]}
    return write_itunes_xml(tmp_path / "week-12.xml", tracks, [playlist])


class FakeShoutcast:
    """Enough of a SHOUTcast 1.x DNAS to test a source against: the source
    port (port + 1) checks the password and collects the audio, and the
    listener port answers admin.cgi title updates."""

    def __init__(self, password: str = "hackme") -> None:
        self.password = password
        self.audio = bytearray()
        self.headers: list[str] = []
        self.titles: list[str] = []
        self.admin_agents: list[str] = []
        self.secrets: list[str] = []
        self.got_audio = threading.Event()
        self.got_title = threading.Event()
        # Two consecutive free ports.
        while True:
            self._http = socket.socket()
            self._http.bind(("127.0.0.1", 0))
            self.port = self._http.getsockname()[1]
            self._src = socket.socket()
            try:
                self._src.bind(("127.0.0.1", self.port + 1))
                break
            except OSError:
                self._http.close()
                self._src.close()
        for s in (self._http, self._src):
            s.listen(5)
        threading.Thread(target=self._accept_sources, daemon=True).start()
        threading.Thread(target=self._accept_http, daemon=True).start()

    def _accept_sources(self) -> None:
        while True:
            try:
                conn, _ = self._src.accept()
            except OSError:
                return
            threading.Thread(target=self._source, args=(conn,), daemon=True).start()

    def _source(self, conn: socket.socket) -> None:
        f = conn.makefile("rb")
        secret = f.readline().decode().rstrip("\r\n")
        self.secrets.append(secret)
        if secret.split(":#")[0] != self.password:
            conn.sendall(b"invalid password\r\n")
            conn.close()
            return
        conn.sendall(b"OK2\r\nicy-caps:11\r\n\r\n")
        while True:
            line = f.readline().decode().rstrip("\r\n")
            if not line:
                break
            self.headers.append(line)
        while True:
            data = f.read1(65536)
            if not data:
                return
            self.audio += data
            self.got_audio.set()

    def _accept_http(self) -> None:
        from urllib.parse import parse_qs, urlsplit
        while True:
            try:
                conn, _ = self._http.accept()
            except OSError:
                return
            with conn:
                req = b""
                while b"\r\n\r\n" not in req:
                    part = conn.recv(4096)
                    if not part:
                        break
                    req += part
                lines = req.decode().split("\r\n")
                target = lines[0].split(" ")[1]
                agent = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("user-agent:")), "")
                qs = parse_qs(urlsplit(target).query)
                if qs.get("pass") == [self.password] and qs.get("mode") == ["updinfo"]:
                    self.titles.append(qs["song"][0])
                    self.admin_agents.append(agent)
                    self.got_title.set()
                conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n")

    def close(self) -> None:
        for s in (self._http, self._src):
            s.close()


class FakeIcecast:
    """An Icecast 2.4 server's source side: PUT on a mount, metadata on /admin."""

    def __init__(self, user: str = "source", password: str = "hackme") -> None:
        import base64
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.audio = bytearray()
        self.requests: list[tuple[str, str, dict]] = []
        self.titles: list[str] = []
        self.got_audio = threading.Event()
        self.got_title = threading.Event()
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        from urllib.parse import parse_qs, urlsplit
        f = conn.makefile("rb")
        method, target, _ = f.readline().decode().split(" ", 2)
        headers = {}
        while True:
            line = f.readline().decode().rstrip("\r\n")
            if not line:
                break
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        self.requests.append((method, target, headers))
        if target == "/status-json.xsl":  # public, as on a real server
            body = json.dumps({"icestats": {"source": {"listenurl": f"http://localhost:{self.port}/classics",
                                                       "listeners": 3, "listener_peak": 5}}}).encode()
            conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\n\r\n" + body)
            conn.close()
            return
        if headers.get("authorization") != self.auth:
            conn.sendall(b"HTTP/1.1 401 Authentication Required\r\n\r\n")
            conn.close()
            return
        if method == "PUT":
            conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
            while True:
                data = f.read1(65536)
                if not data:
                    return
                self.audio += data
                self.got_audio.set()
        qs = parse_qs(urlsplit(target).query)
        if urlsplit(target).path == "/admin/metadata":
            self.titles.append(qs["song"][0])
            self.got_title.set()
        conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n")
        conn.close()

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def shoutcast():
    server = FakeShoutcast()
    yield server
    server.close()


@pytest.fixture
def icecast():
    server = FakeIcecast()
    yield server
    server.close()
