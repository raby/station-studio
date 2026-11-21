"""Keeps other websites from driving Station Studio through the browser.

Studio has no login: it trusts whoever can reach it, which by default is
this computer only. Two kinds of web page could still get in through the
browser of someone using it, and this stops both, as Station Player and
Station Server stop them.

- **DNS rebinding.** A site that points its own name at 127.0.0.1 can
  read and change everything here, as if it were Studio's own page. Its
  requests still carry its name in the Host header, so Studio answers
  only to the names this computer goes by (localhost, 127.0.0.1, ::1),
  plus any listed in STATION_STUDIO_ALLOWED_HOSTS, for a Studio that's
  meant to be opened from other computers (in Docker on a NAS, say).

- **Cross-site requests.** Any page can make the browser send a POST
  here without reading the answer: enough to delete exports or send a
  week to Station Server. The browser says where each request comes
  from (`Sec-Fetch-Site`, and `Origin` in browsers without it), and
  the page can't change that, so a change (POST, PUT, PATCH, DELETE)
  from anywhere but Studio's own pages is refused. Requests that say
  neither come from outside a browser (curl, scripts) and are let
  through: a web page can't make one.

Player and Server ask for a header of their own on each change instead.
Same protection; this way none of the page's requests, the Makefile's
`prune-exports`, or anyone's curl has to change.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

ALLOWED_HOSTS_ENV = "STATION_STUDIO_ALLOWED_HOSTS"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

WRONG_HOST = (
    "Station Studio only answers at this computer's own address "
    "(localhost or 127.0.0.1). To open it at {host}, start it with "
    f"{ALLOWED_HOSTS_ENV}={{host}}."
)
CROSS_SITE = "Only Station Studio's own pages can make changes here."


def hostname(value: str | None) -> str | None:
    """The name in a Host or Origin value, without port, brackets or
    case: "[::1]:8001" → "::1". None when it can't be read."""
    if not value:
        return None
    try:
        name = urlsplit(value if "//" in value else "//" + value).hostname
    except ValueError:
        return None
    return name or None


def allowed_hosts(environ=os.environ) -> frozenset[str] | None:
    """The names Studio answers to; None means any (the setting is *)."""
    extra = {h.strip().lower() for h in environ.get(ALLOWED_HOSTS_ENV, "").split(",") if h.strip()}
    if "*" in extra:
        return None
    return LOCAL_HOSTS | {hostname(h) or h for h in extra}


def host_ok(host: str | None, allowed: frozenset[str] | None) -> bool:
    if allowed is None:
        return True
    return hostname(host) in allowed


def same_origin(headers: dict[str, str], host: str | None, trusted_origins=()) -> bool:
    """Whether a change can be trusted to come from Studio's own pages
    (or from outside a browser altogether)."""
    site = headers.get("sec-fetch-site")
    if site in ("same-origin", "none"):
        return True
    origin = headers.get("origin")
    if origin and origin in trusted_origins:
        return True
    if site:  # "cross-site" or "same-site": another page made it
        return False
    if origin:  # a browser without Sec-Fetch-Site: compare Origin with Host
        return origin != "null" and urlsplit(origin).netloc.lower() == (host or "").lower()
    return True  # neither: not a browser


class Guard:
    """ASGI middleware applying both checks to every HTTP request.
    `trusted_origins` are pages elsewhere that may make changes (the
    SvelteKit dev server's, as CORS allows)."""

    def __init__(self, app, trusted_origins=(), environ=os.environ) -> None:
        self.app = app
        self.trusted_origins = frozenset(trusted_origins)
        self.environ = environ

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host")
        if not host_ok(host, allowed_hosts(self.environ)):
            name = hostname(host) or "that address"
            return await _refuse(scope, send, WRONG_HOST.format(host=name))
        if scope["method"] not in SAFE_METHODS and not same_origin(headers, host, self.trusted_origins):
            return await _refuse(scope, send, CROSS_SITE)
        return await self.app(scope, receive, send)


async def _refuse(scope, send, message: str) -> None:
    # The API's errors as FastAPI shapes them ({"detail": ...}), so the
    # page shows the message; a page gets it as plain text.
    if scope["path"].startswith("/api"):
        body, ctype = json.dumps({"detail": message}).encode(), b"application/json"
    else:
        body, ctype = (message + "\n").encode(), b"text/plain; charset=utf-8"
    await send({"type": "http.response.start", "status": 403, "headers": [
        (b"content-type", ctype), (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})
