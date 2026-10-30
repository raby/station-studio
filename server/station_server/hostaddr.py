"""The addresses this computer's own name resolves to, looked up in the
background and kept fresh.

`gethostbyname_ex(gethostname())` finds addresses on every network the
computer is on (Wi-Fi and Ethernet, say). On some Macs the name is a
`.local` one that only resolves over mDNS, and the lookup can wait on the
network for over a minute (70 s on GitHub's macOS 15 runners). Settings,
the link to Station Studio and the live input's instructions all list
these addresses while answering a request, so the lookup mustn't happen
there. It runs in the background: the first time it's asked for, and
again once the answer is a minute old (a laptop moves between networks).
Until the first answer the list is empty, and the callers still have the
address on the route out, which they find without waiting.
"""
from __future__ import annotations

import socket
import threading
import time

REFRESH_SECONDS = 60.0

_lock = threading.Lock()
_found: list[str] | None = None
_at = 0.0  # time.monotonic() of the lookup that gave _found
_running = False
_generation = 0  # a lookup started before a reset doesn't answer after it


def _look_up(generation: int) -> None:
    global _found, _at, _running
    try:
        found = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        found = []
    with _lock:
        if generation == _generation:
            _found, _at, _running = found, time.monotonic(), False


def host_addresses() -> list[str]:
    """What the computer's name resolves to, as last looked up ([] until
    the first lookup answers, or if it can't be). Starts a lookup in the
    background when there's none yet or the last is over a minute old."""
    global _running
    with _lock:
        stale = _found is None or time.monotonic() - _at > REFRESH_SECONDS
        if stale and not _running:
            _running = True
            threading.Thread(target=_look_up, args=(_generation,), name="host-addresses", daemon=True).start()
        return list(_found or [])


def _reset_for_tests() -> None:
    global _found, _at, _running, _generation
    with _lock:
        _found, _at, _running = None, 0.0, False
        _generation += 1
