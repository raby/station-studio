"""Keep the computer awake while a station is on air.

A computer that goes to sleep takes every station off air, and nobody may
be there to wake it. While at least one station is on air (or trying to
get back on), Station Server asks the system not to sleep when idle. The
screen can still turn off.

- Windows: SetThreadExecutionState, held by a thread of our own (the
  request lasts as long as the thread that made it).
- Mac: `caffeinate -i`, which ends by itself if Station Server does.
- Linux: `systemd-inhibit`, when there is one.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

# Windows' SetThreadExecutionState flags.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    def __init__(self, platform: str = sys.platform) -> None:
        self.platform = platform
        self._lock = threading.Lock()
        self._holders = 0
        self._proc: subprocess.Popen | None = None
        self._release: threading.Event | None = None

    @property
    def held(self) -> bool:
        return self._holders > 0

    def hold(self) -> None:
        with self._lock:
            self._holders += 1
            if self._holders == 1:
                self._start()

    def release(self) -> None:
        with self._lock:
            if self._holders == 0:
                return
            self._holders -= 1
            if self._holders == 0:
                self._stop()

    # ---- per platform ---------------------------------------------------

    def _start(self) -> None:
        try:
            if self.platform == "win32":
                self._release = threading.Event()
                threading.Thread(target=self._windows, args=(self._release,), name="keep-awake",
                                 daemon=True).start()
            elif self.platform == "darwin":
                self._proc = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
            elif shutil.which("systemd-inhibit"):
                self._proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep", "--who=Station Server", "--why=On air",
                     "--mode=block", "sleep", "infinity"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info("keeping the computer awake while on air")
        except OSError as exc:
            logger.warning("couldn't keep the computer awake: %s", exc)

    def _stop(self) -> None:
        if self._release is not None:
            self._release.set()
            self._release = None
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
        logger.info("the computer may sleep again")

    @staticmethod
    def _windows(release: threading.Event) -> None:
        import ctypes
        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel.SetThreadExecutionState.restype = ctypes.c_uint32
        if not kernel.SetThreadExecutionState(ctypes.c_uint32(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)):
            logger.warning("Windows wouldn't keep the computer awake")
            return
        release.wait()
        kernel.SetThreadExecutionState(ctypes.c_uint32(ES_CONTINUOUS))
