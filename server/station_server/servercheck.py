"""Test a SHOUTcast or Icecast server step by step, without going on air.

Each step says what it found in plain words, and the first one that fails
says what to change, so "it won't connect" turns into "port 8001 is
closed" or "that's an Icecast server, not SHOUTcast". The password is
tried the way going on air would (connect, then leave at once), but not
while something else is sending there: a server takes one source at a
time, and the test shouldn't knock Winamp off the air.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from station_server.senders import ADMIN_USER_AGENT, SenderError, ShoutcastSender, StreamInfo, sender_from_url

TIMEOUT = 6.0
KINDS = {"icecast": "Icecast", "shoutcast": "SHOUTcast"}


def _step(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def _fetch(url: str, timeout: float = TIMEOUT) -> tuple[int, dict, str]:
    """GET `url` as a browser would (SHOUTcast 1.x answers only browsers):
    status, headers and up to 64 KB of the body, error pages included."""
    req = urllib.request.Request(url, headers={"User-Agent": ADMIN_USER_AGENT})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read(65536).decode("latin-1")
    except urllib.error.HTTPError as exc:
        body = exc.read(65536).decode("latin-1") if exc.fp else ""
        return exc.code, dict(exc.headers or {}), body


def identify(status: int, headers: dict, body: str, stats=None) -> tuple[str | None, str]:
    """Which server answered: ("icecast" | "shoutcast" | None, its name)."""
    server = next((v for k, v in headers.items() if k.lower() == "server"), "")
    if "icecast" in server.lower():
        return "icecast", server.strip()
    if "shoutcast" in server.lower():
        return "shoutcast", server.strip()
    text = body.lower()
    if "shoutcast" in text:
        return "shoutcast", "SHOUTcast DNAS 2" if "server v2" in text or "dnas 2" in text else "SHOUTcast DNAS"
    if "icecast" in text:
        return "icecast", "Icecast"
    if stats is not None:
        return "icecast", str(((stats or {}).get("icestats") or {}).get("server_id") or "Icecast")
    return None, server.strip()


def _icecast_stats(host: str, port: int, fetch) -> dict | None:
    try:
        status, _headers, body = fetch(f"http://{host}:{port}/status-json.xsl")
        return json.loads(body) if status == 200 else None
    except (OSError, ValueError):
        return None


def _connect_problem(host: str, port: int, exc: OSError, what: str = "the server") -> str:
    if isinstance(exc, ConnectionRefusedError):
        return (f"Nothing answers on port {port} at {host}: {what} isn't running there, or it uses "
                "another port. Check the port in the server's settings (or the hosting account).")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return (f"No answer from {host} port {port} within {TIMEOUT:.0f} seconds: a firewall is in the "
                "way, the router there isn't forwarding the port, or the address is wrong.")
    return f"Couldn't connect to {host} port {port}: {exc}"


def _password_problem(kind: str, exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if kind == "icecast":
        if " 401" in text:
            return ("The server refused the password. Use Icecast's source password (<source-password> "
                    "in icecast.xml, or the mount's own password), with the user name source.")
        if " 403" in text and ("in use" in low or "mountpoint" in low):
            return "Something else is already sending to that mount: the server takes one source per mount."
        if " 403" in text:
            return f"The server turned the source away ({text}). Its icecast.xml may limit sources or mounts."
        if " 404" in text or " 405" in text:
            return f"The server doesn't take sources this way ({text}): it may be a listener address, not the server itself."
    else:
        if "invalid password" in low or "no reply" in low:
            return ("The server refused the password. Use the DNAS source password (password= or "
                    "streampassword_1= in its config, or the hosting account's source password).")
    return text


def diagnose(url: str, password: str | None, on_air: bool = False, resolve=socket.getaddrinfo,
             connect=socket.create_connection, fetch=_fetch, make_sender=sender_from_url) -> dict:
    """Run through the checks for the server at `url` (shoutcast://... or
    icecast://...). `on_air`: this station is on air there now, so the
    password is known to work and the source isn't disturbed."""
    steps: list[dict] = []
    result = {"steps": steps, "kind": None, "server": None, "ok": False}
    parsed = urllib.parse.urlsplit(url or "")
    scheme = parsed.scheme.lower()
    if scheme not in KINDS or not parsed.hostname or not parsed.port:
        steps.append(_step("Address", "fail", "Enter the server's address (a name like stream.example.com, or "
                                              "numbers like 81.150.147.206) and its port."))
        return result
    host, port, kind = parsed.hostname, parsed.port, scheme
    label = KINDS[kind]
    if kind == "icecast" and parsed.path.strip() in ("", "/"):
        steps.append(_step("Address", "fail", "Enter the mount, the part after the port in Icecast's "
                                              "addresses, like /radio or /stream."))
        return result

    try:
        found = sorted({a[4][0] for a in resolve(host, port, 0, socket.SOCK_STREAM)})
        if found == [host]:
            steps.append(_step("Address", "ok", f"{host}"))
        else:
            steps.append(_step("Address", "ok", f"{host} is at {', '.join(found[:3])}"))
    except OSError:
        steps.append(_step("Address", "fail", f"There's no server called {host}: check the spelling."))
        return result

    try:
        connect((host, port), timeout=TIMEOUT).close()
        steps.append(_step("Port", "ok", f"Port {port} is open"))
    except OSError as exc:
        steps.append(_step("Port", "fail", _connect_problem(host, port, exc)))
        return result

    try:
        status, headers, body = fetch(f"http://{host}:{port}/")
    except OSError as exc:
        status, headers, body = 0, {}, ""
        steps.append(_step("Server", "warn", f"It didn't answer as a web page ({exc})"))
    stats = _icecast_stats(host, port, fetch) if kind == "icecast" else None
    found_kind, name = identify(status, headers, body, stats) if status else (None, "")
    result["server"] = name or None
    if found_kind and found_kind != kind:
        other = KINDS[found_kind]
        how = ("choose Icecast as the type and enter its mount (like /radio)" if found_kind == "icecast"
               else "choose SHOUTcast as the type; for a DNAS 2 stream other than the first, set the stream number")
        steps.append(_step("Server", "fail", f"This is {name or other}, not {label}: {how}."))
        result["kind"] = found_kind
        return result
    if found_kind:
        result["kind"] = found_kind
        steps.append(_step("Server", "ok", name or label))
    elif status:
        steps.append(_step("Server", "warn", f"Something answers on port {port}, but it doesn't say it's "
                                             f"{label}. It may be a web page or a different service."))

    if kind == "shoutcast":
        try:
            connect((host, port + 1), timeout=TIMEOUT).close()
            steps.append(_step("Source port", "ok", f"Port {port + 1} is open (SHOUTcast takes sources one port up)"))
        except OSError as exc:
            steps.append(_step("Source port", "fail",
                               f"SHOUTcast takes sources on the port above the listeners' one, and port {port + 1} "
                               + ("is closed: forward it too, or for DNAS 2 check portlegacy= in its config."
                                  if isinstance(exc, ConnectionRefusedError) else
                                  f"doesn't answer. {_connect_problem(host, port + 1, exc)}")))
            return result

    sender = None
    try:
        sender = make_sender(url, StreamInfo(name="Station Server test"), password or None)
    except ValueError as exc:
        steps.append(_step("Password", "fail", "Enter the source password." if "password" in str(exc).lower()
                           else str(exc)))
    counts = None
    if sender is not None:
        try:
            counts = sender.count_listeners()
        except Exception:  # noqa: BLE001 - a private stats page: no counts
            counts = None
    if sender is None:
        pass
    elif on_air:
        steps.append(_step("Password", "ok", "This station is on air there now, so the password works"))
    elif counts and counts.get("source"):
        steps.append(_step("Password", "warn",
                           "Something is sending there now (Winamp's plugin, say), so the password wasn't "
                           "tried: the server takes one source at a time. Stop it and test again."))
    else:
        try:
            sender.connect()
            sender.close()
            steps.append(_step("Password", "ok", "The server accepted the password"))
        except SenderError as exc:
            steps.append(_step("Password", "fail", _password_problem(kind, exc)))
        except OSError as exc:
            steps.append(_step("Password", "fail", _connect_problem(
                host, port + 1 if isinstance(sender, ShoutcastSender) else port, exc)))

    if sender is not None:
        if counts is None:
            steps.append(_step("Listeners", "warn", "The server's listener count can't be read (its status page "
                                                     "is private or turned off), so Station Server can't show it."))
        else:
            most = f" (most {counts['max']})" if counts.get("max") else ""
            n = counts.get("listeners", 0)
            steps.append(_step("Listeners", "ok", f"{n} listening now{most}" if n != 1 else f"1 listening now{most}"))
    result["ok"] = not any(s["status"] == "fail" for s in steps)
    return result
