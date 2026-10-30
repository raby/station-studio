"""The addresses this computer's own name resolves to, looked up once,
in the background.

`gethostbyname_ex(gethostname())` finds addresses on every network the
computer is on (Wi-Fi and Ethernet, say). On some Macs the name is a
`.local` one that only resolves over mDNS, and the lookup can wait on the
network for over a minute (70 s on GitHub's macOS 15 runners). Settings,
the link to Station Studio and the live input's instructions all list
these addresses while answering a request, so the lookup mustn't happen
there: it starts the first time it's asked for, and until it's done the
answer is just the address on the route out, which the callers find
themselves without waiting.
"""
from __future__ import annotations

import socket
import threading

_lock = threading.Lock()
_found: list[str] | None = None
_started = False
_generation = 0  # a lookup started before a reset doesn't answer after it


def _look_up(generation: int) -> None:
    global _found
    try:
        found = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        found = []
    with _lock:
        if generation == _generation:
            _found = found


def host_addresses() -> list[str]:
    """What the computer's name resolves to, or [] while that's still
    being looked up (or if it can't be)."""
    global _started
    with _lock:
        if _found is not None:
            return list(_found)
        if not _started:
            _started = True
            threading.Thread(target=_look_up, args=(_generation,), name="host-addresses", daemon=True).start()
    return []


def _reset_for_tests() -> None:
    global _found, _started, _generation
    with _lock:
        _found, _started = None, False
        _generation += 1
