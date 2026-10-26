"""The now/next feed: what each station is playing, what's coming up and
what's just played, for the station's website and Station Player.

Station Server knows the week's running order in advance (schedule.py),
so a feed holds the next few hours with the time each song goes out, and
a page reading it works out what's on now from the clock. That means it
needs publishing only now and then: about once an hour, and straight away
when what's on air stops matching what was published (a skip, a restart,
going off air, a presenter going live).

Settings › Website publishes the feed to a GitHub Pages site of the
station's own (a public repository, written to with a token that can
change only that repository's contents):

    stations.json         the stations, by name and slug
    <slug>.json           each station's feed (station_feed below)
    index.html            a page with every station: now, coming up, just played
    widget.html           one station, to embed in a website (?station=<slug>)
    now.js, now.css       what those two pages share
    .nojekyll             served as they are

The page files are Station Server's own (static/site/); they're published
with the feeds, so a new version of Station Server brings new pages.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from station_server import __version__

logger = logging.getLogger(__name__)

FEED_HOURS = 6  # how far ahead a feed goes
RECENT = 10  # how many just-played songs it lists
REFRESH_SECONDS = 3600  # published at least this often while on air
LOW_SECONDS = 2 * 3600  # ... or sooner, when this little is left of what was published
CHECK_SECONDS = 20  # how often it compares what's on air with what was published
SOON_SECONDS = 120  # what's on changed: published after at most this, so a burst of skips is one publish
SITE_DIR = Path(__file__).parent / "static" / "site"
SITE_FILES = ("index.html", "widget.html", "now.js", "now.css")
_REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


class FeedError(Exception):
    """Publishing failed; the message says why, for Settings › Website."""


def slug(name: str) -> str:
    """'Rebirth Classics' -> 'rebirth-classics'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "station"


def clean_repo(value: str) -> str:
    """'raby/rebirth-now', or a github.com link to it -> 'raby/rebirth-now'.
    Raises ValueError for anything else."""
    raw = value.strip().strip("/").removesuffix(".git")
    raw = re.sub(r"^(https?://)?(www\.)?github\.com/", "", raw)
    if raw and not _REPO_RE.match(raw):
        raise ValueError("Give the GitHub repository as owner/name, like raby/rebirth-now")
    return raw


def site_url(repo: str) -> str:
    """Where GitHub Pages serves a repository: https://raby.github.io/rebirth-now/
    (a repository called raby.github.io is served at the top)."""
    owner, name = repo.split("/", 1)
    if name.lower() == f"{owner.lower()}.github.io":
        return f"https://{owner.lower()}.github.io/"
    return f"https://{owner.lower()}.github.io/{name}/"


def _row(row: dict) -> dict:
    item = {"at": row["at"], "seconds": row["seconds"]}
    if "show" in row:
        item["show"] = row["show"]
    else:
        item["title"], item["artist"] = row.get("title"), row.get("artist")
    return item


def station_feed(runner, hours: float = FEED_HOURS, now: datetime | None = None) -> dict:
    """A station's feed: on air or not, the presenter if one is live, the
    running order from what's on now for the next `hours` (each song or
    show with when it starts and how long it is), and the songs just
    played. Nothing private: no file paths, addresses or passwords."""
    now = now or datetime.now().astimezone()
    st = runner.station
    on = runner.state == "on" and not runner.rehearsal
    ch = runner.channel
    live = ch.live if on and ch else None
    items: list[dict] = []
    if on and live is None:
        horizon = now + timedelta(hours=hours)
        for row in runner.coming(int(hours * 60)):  # a song is rarely under a minute
            if datetime.fromisoformat(row["at"]) > horizon:
                break
            items.append(_row(row))
    recent = []
    for e in reversed(runner.playlog.entries(date.today() - timedelta(days=1), date.today())):
        if (e.get("seconds") or 0) < 30:  # a skip
            continue
        recent.append({"at": e["at"].isoformat(timespec="seconds"), "title": e.get("title"),
                       "artist": e.get("artist")})
        if len(recent) >= RECENT:
            break
    return {
        "station": st.name,
        "slug": slug(st.name),
        "updated": now.isoformat(timespec="seconds"),
        "on_air": on,
        "live": {"presenter": live.presenter or "Live show",
                 "since": datetime.fromtimestamp(live.since).astimezone().isoformat(timespec="seconds")}
        if live is not None else None,
        "items": items,
        "recent": recent,
    }


def on_now(feed: dict, at: datetime) -> tuple | None:
    """What a feed says is on at `at`: (title, artist) or ("show", name),
    ("live", presenter), or None (off air, or past the end of the feed)."""
    if not feed.get("on_air"):
        return None
    if feed.get("live"):
        return ("live", feed["live"]["presenter"])
    for item in feed.get("items") or []:
        start = datetime.fromisoformat(item["at"])
        if start <= at < start + timedelta(seconds=item["seconds"]):
            return ("show", item["show"]) if "show" in item else (item.get("title"), item.get("artist"))
    return None


class GitHubPages:
    """Writes files into a GitHub repository (the Contents API), which
    GitHub Pages then serves. Only changed files are written."""

    API = "https://api.github.com"

    def __init__(self, repo: str, token: str, branch: str = "main", opener=urllib.request.urlopen) -> None:
        self.repo, self.token, self.branch, self._open = repo, token, branch, opener
        self._known: dict[str, tuple[str, str]] = {}  # path -> (sha, our content's hash)

    def _call(self, method: str, path: str, body: dict | None = None) -> dict | None:
        req = urllib.request.Request(
            f"{self.API}/repos/{self.repo}/contents/{path}" + (f"?ref={self.branch}" if method == "GET" else ""),
            data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": f"StationServer/{__version__}",
                     "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=20) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            try:
                said = json.loads(exc.read()).get("message") or ""
            except (ValueError, AttributeError, OSError):
                said = ""
            if exc.code in (401, 403):
                raise FeedError(f"GitHub turned the token down ({exc.code} {said}). Check it can change the "
                                f"contents of {self.repo}, and hasn't expired.") from None
            if exc.code == 404:
                raise FeedError(f"GitHub can't find {self.repo}, or the token can't see it") from None
            raise FeedError(f"GitHub said {exc.code} {said}".strip()) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FeedError(f"Couldn't reach GitHub ({getattr(exc, 'reason', exc)})") from None

    def put(self, path: str, content: bytes, message: str) -> bool:
        """Write a file unless it already holds `content`; whether it wrote."""
        digest = hashlib.sha1(content).hexdigest()
        known = self._known.get(path)
        if known and known[1] == digest:
            return False
        sha = known[0] if known else None
        if sha is None:
            got = self._call("GET", path)
            if got:
                sha = got.get("sha")
                try:
                    if base64.b64decode(got.get("content") or "") == content:
                        self._known[path] = (sha, digest)
                        return False
                except ValueError:
                    pass
        body = {"message": message, "content": base64.b64encode(content).decode(), "branch": self.branch}
        if sha:
            body["sha"] = sha
        try:
            got = self._call("PUT", path, body)
        except FeedError:
            self._known.pop(path, None)  # perhaps changed elsewhere: ask again next time
            raise
        self._known[path] = ((got or {}).get("content", {}).get("sha"), digest)
        return True


class FeedPublisher:
    """Keeps the website's feeds up to date while Settings › Website is on."""

    def __init__(self, app, pages=GitHubPages, clock=time.time) -> None:
        self.app, self._pages_cls, self._clock = app, pages, clock
        self.pages = None
        self.error: str | None = None
        self.last: dict | None = None  # {at, files}
        self._published: dict[str, tuple[float, dict]] = {}  # slug -> (when, the feed)
        self._changed_since: dict[str, float] = {}  # slug -> when it first differed
        self._site_done = False
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- settings ------------------------------------------------------

    def apply(self) -> None:
        cfg = self.app.config
        want = cfg.feed and bool(cfg.feed_repo and cfg.feed_token)
        with self._lock:
            key = (cfg.feed_repo, cfg.feed_token)
            if self.pages is None or (self.pages.repo, self.pages.token) != key:
                self.pages = self._pages_cls(cfg.feed_repo, cfg.feed_token) if want else None
                self._published.clear()
                self._site_done = False
                self.error = None
            if want and not (self._thread and self._thread.is_alive()):
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="feed", daemon=True)
                self._thread.start()
            elif not want:
                self.pages = None
                self._stop.set()
                self._wake.set()
        if want:
            self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def publish_now(self) -> None:
        """Settings › Website › Publish now: every station, straight away."""
        with self._lock:
            self._published.clear()
        self._wake.set()

    def status(self) -> dict:
        cfg = self.app.config
        return {"enabled": cfg.feed, "repo": cfg.feed_repo, "has_token": bool(cfg.feed_token),
                "url": site_url(cfg.feed_repo) if cfg.feed_repo else None,
                "stations": [{"name": st.name, "slug": slug(st.name)} for st in cfg.stations],
                "error": self.error, "last": self.last}

    # ---- publishing ----------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self.tick()
            except Exception:  # never let the feed take Station Server down
                logger.exception("feed: publishing failed")
            self._wake.wait(CHECK_SECONDS)

    def due(self, name: str, feed: dict, now: datetime) -> bool:
        """Whether a station's feed should be published now."""
        t = self._clock()
        was = self._published.get(name)
        if was is None:
            return True
        at, old = was
        if feed["on_air"] != old["on_air"] or bool(feed["live"]) != bool(old["live"]):
            return True  # on or off air, a presenter on or off: straight away
        if feed["on_air"]:
            if t - at >= REFRESH_SECONDS:
                return True
            items = old.get("items") or []
            if items:
                last = items[-1]
                end = datetime.fromisoformat(last["at"]) + timedelta(seconds=last["seconds"])
                if (end - now).total_seconds() < LOW_SECONDS:
                    return True
        if on_now(old, now) != on_now(feed, now):
            first = self._changed_since.setdefault(name, t)
            return t - first >= SOON_SECONDS or t - at >= SOON_SECONDS
        self._changed_since.pop(name, None)
        return False

    def tick(self) -> int:
        """Publish whatever is due; how many files were written."""
        with self._lock:
            pages = self.pages
        if pages is None:
            return 0
        cfg = self.app.config
        now = datetime.now().astimezone()
        wrote = 0
        try:
            if not self._site_done:
                for name in SITE_FILES:
                    wrote += pages.put(name, (SITE_DIR / name).read_bytes(), f"Station Server {__version__}: {name}")
                wrote += pages.put(".nojekyll", b"", "Serve the pages as they are")
                self._site_done = True
            stations = [{"name": st.name, "slug": slug(st.name)} for st in cfg.stations]
            wrote += pages.put("stations.json", json.dumps({"stations": stations}, indent=1).encode(),
                               "The stations")
            for st in cfg.stations:
                runner = self.app.runners.get(st.id)
                if runner is None:
                    continue
                feed = station_feed(runner, now=now)
                name = feed["slug"]
                if not self.due(name, feed, now):
                    continue
                if pages.put(f"{name}.json", json.dumps(feed, ensure_ascii=False, indent=1).encode(),
                             f"{st.name}: {_summary(feed)}"):
                    wrote += 1
                self._published[name] = (self._clock(), feed)
                self._changed_since.pop(name, None)
        except FeedError as exc:
            if self.error != str(exc):
                logger.warning("feed: %s", exc)
            self.error = str(exc)
            return wrote
        self.error = None
        if wrote:
            self.last = {"at": now.isoformat(timespec="seconds"), "files": wrote}
        return wrote


def _summary(feed: dict) -> str:
    if not feed["on_air"]:
        return "off air"
    if feed["live"]:
        return f"live, {feed['live']['presenter']}"
    first = (feed["items"] or [{}])[0]
    return f"now {first.get('show') or first.get('title') or '?'}"
