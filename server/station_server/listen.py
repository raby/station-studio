"""The Listen button: the broadcast exactly as it goes to the server.

Each station's encoded MP3 is copied to whoever is listening through the
page (`/api/stations/<id>/listen.mp3`), on this computer or, with phone
access on, a phone. A listener who can't keep up is dropped rather than
holding up the broadcast.
"""
from __future__ import annotations

import queue as queues
import threading

MAX_LISTENERS = 8
# About a minute of 128 kbps audio waiting for a slow listener, then they go.
_BACKLOG = 240


class TooManyListeners(Exception):
    pass


class Listeners:
    def __init__(self, most: int = MAX_LISTENERS) -> None:
        self.most = most
        self._lock = threading.Lock()
        self._queues: list[queues.Queue] = []

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._queues)

    def open(self) -> queues.Queue:
        with self._lock:
            if len(self._queues) >= self.most:
                raise TooManyListeners(f"{self.most} people are listening already")
            q: queues.Queue = queues.Queue(_BACKLOG)
            self._queues.append(q)
            return q

    def close(self, q: queues.Queue) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def publish(self, data: bytes) -> None:
        with self._lock:
            for q in list(self._queues):
                try:
                    q.put_nowait(data)
                except queues.Full:  # too slow: let them go
                    self._queues.remove(q)
                    self._end(q)

    def end(self) -> None:
        """Off air: every listener's stream ends."""
        with self._lock:
            for q in self._queues:
                self._end(q)
            self._queues.clear()

    @staticmethod
    def _end(q: queues.Queue) -> None:
        try:
            q.put_nowait(None)
        except queues.Full:
            try:
                q.get_nowait()
                q.put_nowait(None)
            except (queues.Empty, queues.Full):
                pass


class Tap:
    """A sender that also hands everything it sends to the listeners."""

    def __init__(self, sender, listeners: Listeners) -> None:
        self._sender = sender
        self.listeners = listeners

    def send(self, data: bytes) -> None:
        self._sender.send(data)
        self.listeners.publish(data)

    def __getattr__(self, name):
        return getattr(self._sender, name)
