"""Telling someone when a station is off air, since nobody may be watching.

A station that should be on air (you put it on air) but isn't, can't reach
its server, or is playing silence or its fallback, is a problem. Once a
problem has lasted Settings › Alerts' few minutes (long enough for a
retry or a reconnect to fix it by itself), an alert goes out:

- as a phone notification, through ntfy (https://ntfy.sh: install its app
  and subscribe to the same topic), and/or
- as an email, through the mail server given.

Another goes out when the station is back. Station Server can't send an
alert if the computer itself is off or has no internet, so an outside
check of the stream is still worth having.
"""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage

from station_server import __version__
from station_server.config import Alerts

logger = logging.getLogger(__name__)

CHECK_SECONDS = 15.0
_TIMEOUT = 20


def problem(runner) -> tuple[str, str] | None:
    """(headline, details) when a station that should be on air isn't
    going out properly; None when it's fine, or meant to be off."""
    st = runner.station
    if not st.on_air:
        return None
    state = runner.state
    if state in ("waiting", "starting", "error"):
        return "is off air", runner.error or "it hasn't gone back on air"
    if state != "on":
        return None
    sender = runner.sender
    if sender is not None and not sender.connected:
        return "is off air", f"it can't reach {getattr(sender, 'describe', 'the server')}"
    ch = runner.channel
    if ch is not None and ch.stuck:
        return "is sending silence", "none of the playlist's tracks can be read"
    if runner.playing_fallback():
        return "is playing its fallback", "none of the playlist's tracks can be read"
    return None


def send(cfg: Alerts, title: str, message: str) -> list[str]:
    """Send an alert every way that's set up. Returns what went wrong
    (an empty list when it all went), and never raises."""
    errors = []
    if cfg.ntfy:
        try:
            send_ntfy(cfg.ntfy, title, message)
        except Exception as exc:
            errors.append(f"Phone notification: {getattr(exc, 'reason', None) or exc}")
    if cfg.email_to:
        try:
            send_email(cfg, title, message)
        except Exception as exc:
            errors.append(f"Email: {exc}")
    for e in errors:
        logger.warning("couldn't send the alert: %s", e)
    return errors


def send_ntfy(topic_url: str, title: str, message: str, opener=urllib.request.urlopen) -> None:
    parts = urllib.parse.urlsplit(topic_url)
    body = json.dumps({"topic": parts.path.strip("/"), "title": title, "message": message,
                       "priority": 4, "tags": ["radio"]}).encode("utf-8")
    req = urllib.request.Request(f"{parts.scheme}://{parts.netloc}/", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": f"station-server/{__version__}"})
    with opener(req, timeout=_TIMEOUT) as resp:
        resp.read()


def send_email(cfg: Alerts, title: str, message: str, smtp=None) -> None:
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = cfg.email_from or cfg.smtp_user or cfg.email_to
    msg["To"] = cfg.email_to
    msg.set_content(f"{message}\n\n(Sent by Station Server {__version__}.)\n")
    if smtp is None:
        if cfg.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=_TIMEOUT,
                                    context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=_TIMEOUT)
    with smtp:
        if cfg.smtp_security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if cfg.smtp_user:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


class Watcher:
    """Looks at every station every few seconds and sends the alerts."""

    def __init__(self, app, send=send, clock=time.time) -> None:
        self.app = app
        self._send = send
        self._clock = clock
        self._since: dict[str, float] = {}  # station id -> when its problem began
        self._told: dict[str, float] = {}  # station id -> when it began, for those alerted
        self.last: dict | None = None  # the last alert, for Settings

    def check(self) -> None:
        cfg = self.app.config.alerts
        now = self._clock()
        for sid, runner in list(self.app.runners.items()):
            name = runner.station.name
            found = problem(runner)
            if found:
                since = self._since.setdefault(sid, now)
                if sid not in self._told and now - since >= cfg.after_minutes * 60:
                    self._told[sid] = since
                    headline, details = found
                    self._alert(cfg, f"{name} {headline}",
                                f"{name} {headline} since {_hhmm(since)}: {details}. "
                                "Station Server keeps trying to put it right.")
                continue
            self._since.pop(sid, None)
            began = self._told.pop(sid, None)
            if began is not None and cfg.back_on and runner.station.on_air:
                minutes = max(1, round((now - began) / 60))
                self._alert(cfg, f"{name} is back on air",
                            f"{name} is back on air, after {minutes} minute{'s' if minutes != 1 else ''}.")
        for sid in list(self._since):
            if sid not in self.app.runners:
                self._since.pop(sid, None)
                self._told.pop(sid, None)

    def _alert(self, cfg: Alerts, title: str, message: str) -> None:
        logger.warning("alert: %s", message)
        if not (cfg.ntfy or cfg.email_to):
            return
        errors = self._send(cfg, title, message)
        self.last = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "title": title,
                     "errors": errors}

    def run(self, stopped: threading.Event) -> None:
        while not stopped.wait(CHECK_SECONDS):
            try:
                self.check()
            except Exception:  # never let watching stop Station Server
                logger.exception("checking the stations for alerts failed")
