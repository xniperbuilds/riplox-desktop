"""
Following a channel or a playlist, and being told what is new.

There are two ways to ask, and the difference is the whole design.

* **The feed.** YouTube publishes an Atom feed for every channel and every
  playlist. Reading it is an ordinary HTTP GET of a public document - no
  sign-in, no engine, about ten kilobytes, and nothing a site answers with
  "prove you are a person". Where a feed can be worked out, that is what a
  routine check uses, and the old ceilings stop applying: fifteen minutes
  instead of six hours, a hundred followed things instead of twenty.
* **The engine.** Everything else, and three jobs the feed cannot do: the
  first look when something is followed (a feed holds only the newest
  fifteen), an occasional full check to catch what the feed missed, and every
  site that is not YouTube.

The feed is not perfect and is not treated as if it were. It carries no
duration and no kind, so nothing that needs those can be decided from it, and
it can miss an item outright - which is why the full check still runs.

Downloading is off unless it is turned on, per followed item, and capped when
it is. The promise is not "it never downloads"; it is that **nothing downloads
unless you asked for that channel by name**.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from xml.etree import ElementTree

import engine

MAX_ITEMS = 100              # a feed check is cheap, so the ceiling can be real
PEEK = 30                    # newest N per engine check
KNOWN_CAP = 400              # ids remembered per item
FRESH_CAP = 60               # unseen videos held per item

# One engine check per tick at most, spaced out: that is the shape a rate
# limiter objects to. A feed read is a public document and needs neither.
TICK = 60
SPACING = 90
FEED_SPACING = 5

# How often one item may be checked, in minutes. The fastest two only apply to
# something with a feed; an item the engine has to fetch is never checked more
# often than SLOW_FLOOR however this is set, because that is the request a site
# can object to.
MINUTES = engine.WATCH_MINUTES
DEFAULT_MINUTES = 60
SLOW_FLOOR = 360

# The feed misses things. Pinchflat, which does this for a living, says so of
# its own RSS indexing and runs a periodic full index anyway; so does this.
FULL_EVERY = 7 * 86400
FEED_STRIKES = 3             # consecutive failures before the feed is dropped

# Downloading on its own, when it has been turned on for that item.
AUTO_MAX_PER_CHECK = 3

_FEED_URL = "https://www.youtube.com/feeds/videos.xml?%s=%s"
_PLAYLIST_ID = re.compile(r"^(PL|UU|OL|FL|LL|RD)[A-Za-z0-9_-]{8,}$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{20,}$")
# A channel's other tabs are not what the channel feed lists - it carries the
# uploads and nothing else - so following Shorts through it would report the
# wrong videos and look like it was working.
_OTHER_TAB = re.compile(r"/(shorts|streams|live|playlists|community|posts)(/|$|\?)")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_NS = {"a": "http://www.w3.org/2005/Atom",
       "yt": "http://www.youtube.com/xml/schemas/2015",
       "media": "http://search.yahoo.com/mrss/"}

_lock = threading.RLock()
_stop = threading.Event()
_sweeping = threading.Event()    # a Check all is already walking the list
# Only ever one engine request in the air. The timer already spaces its checks
# out, but Check now and Check all can both be pressed while one is running,
# and three requests arriving together is exactly the shape that gets an
# address asked to prove it is a person. Feed reads do not take this.
_asking = threading.Lock()
_thread = None
_last_check = 0.0
_status = {"state": "off", "busy": "", "error": ""}


# --------------------------------------------------------------------------
# Stored state
# --------------------------------------------------------------------------

def _file():
    return engine.data_dir() / "watch.json"


def _migrate(item: dict) -> dict:
    """Fill in what a file written by an older version does not have."""
    item.setdefault("feed", "")
    item.setdefault("feed_fail", 0)
    item.setdefault("full_checked", item.get("checked", 0))
    item.setdefault("auto", False)
    return item


def load() -> dict:
    with _lock:
        try:
            with open(_file(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {"items": []}
        data.setdefault("items", [])
        data["items"] = [_migrate(i) for i in data["items"] if isinstance(i, dict)]
        return data


def _save(data: dict) -> None:
    path = _file()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def save(data: dict) -> None:
    with _lock:
        _save(data)


def _find(data: dict, item_id: str):
    for item in data["items"]:
        if item["id"] == item_id:
            return item
    return None


def minutes() -> int:
    """How often one item may be checked, as the user set it."""
    # An older file that says hours instead has already been read as minutes
    # by load_settings, which is the only place that can tell the difference.
    try:
        raw = int(engine.load_settings().get("watch_minutes", DEFAULT_MINUTES))
    except (TypeError, ValueError):
        raw = DEFAULT_MINUTES
    return raw if raw in MINUTES else DEFAULT_MINUTES


def interval(item: dict = None) -> float:
    """
    Seconds between checks for this item.

    An item without a feed has to be fetched with the engine, and that is the
    request a site can object to - so it keeps the old slow floor however this
    is set. An item with a feed does not.
    """
    chosen = minutes()
    if item is not None and not item.get("feed"):
        chosen = max(chosen, SLOW_FLOOR)
    return chosen * 60.0


# --------------------------------------------------------------------------
# The feed
# --------------------------------------------------------------------------

def feed_for(url: str, info: dict) -> str:
    """
    The Atom feed for this link, or "" when one cannot be worked out.

    Nothing is guessed. A playlist id or a channel id has to be in the link or
    in what the engine already read; anything else keeps the engine path, and
    a mixed list is perfectly fine.
    """
    low = (url or "").lower()
    if "youtube.com" not in low and "youtu.be" not in low:
        return ""
    if _OTHER_TAB.search(low):
        return ""

    listed = re.search(r"[?&]list=([A-Za-z0-9_-]+)", url or "")
    if listed and _PLAYLIST_ID.match(listed.group(1)):
        return _FEED_URL % ("playlist_id", listed.group(1))

    ident = str((info or {}).get("id") or "")
    if _PLAYLIST_ID.match(ident):
        return _FEED_URL % ("playlist_id", ident)

    channel = str((info or {}).get("channel_id") or "")
    if _CHANNEL_ID.match(channel):
        return _FEED_URL % ("channel_id", channel)

    found = re.search(r"/channel/(UC[A-Za-z0-9_-]{20,})", url or "")
    if found:
        return _FEED_URL % ("channel_id", found.group(1))
    return ""


def read_feed(feed_url: str) -> list:
    """
    The entries in an Atom feed, newest first, in the shape a check expects.

    Duration is None on purpose: the feed does not carry one, and inventing a
    number here would be worse than not having it.
    """
    request = urllib.request.Request(
        feed_url, headers={"User-Agent": _UA, "Accept": "application/atom+xml"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(2_000_000)

    root = ElementTree.fromstring(raw)
    out = []
    for entry in root.findall("a:entry", _NS):
        video_id = (entry.findtext("yt:videoId", "", _NS) or "").strip()
        if not video_id:
            continue
        title = (entry.findtext("a:title", "", _NS) or "").strip()
        thumb = ""
        node = entry.find("media:group/media:thumbnail", _NS)
        if node is not None:
            thumb = node.get("url") or ""
        out.append({"id": video_id,
                    "url": "https://www.youtube.com/watch?v=" + video_id,
                    "title": title or "Untitled",
                    "duration": None,
                    "thumbnail": thumb})
    return out


# --------------------------------------------------------------------------
# Adding
# --------------------------------------------------------------------------

def add(url: str, kind: str = "") -> dict:
    """
    Start following a link.

    The first check is a baseline, not a result: every video it can see is
    written down as already known. Without that, following a channel would
    announce its entire back catalogue as new. It is done with the engine
    rather than the feed because a feed holds only the newest fifteen, and the
    other four hundred would arrive as "new" on the first ordinary check.
    """
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("That does not look like a link.")

    data = load()
    if len(data["items"]) >= MAX_ITEMS:
        raise ValueError(f"Following {MAX_ITEMS} things already. Remove one first.")
    for item in data["items"]:
        if item["url"] == url:
            raise ValueError("That one is already being followed.")

    settings = engine.load_settings()
    info = engine.peek(url, settings, PEEK)

    # A bare channel address answers with its tabs - Videos, Shorts, Live - and
    # a tab list never changes when a video is posted. Following it would look
    # like it was working and never find anything, so the tabs are handed back
    # for the user to choose from instead.
    if info.get("is_tabs"):
        return {"choose": True, "title": info.get("title") or "Channel",
                "tabs": info.get("tabs") or []}

    now = time.time()
    item = {
        "id": secrets.token_hex(6),
        "url": url,
        "kind": kind if kind in ("channel", "playlist") else _guess_kind(url),
        "title": info.get("title") or url,
        "uploader": info.get("uploader") or "",
        "thumbnail": info.get("thumbnail") or "",
        "added": now,
        "checked": now,
        "full_checked": now,
        "paused": False,
        "error": "",
        "botcheck": False,
        "feed": feed_for(url, info),
        "feed_fail": 0,
        "auto": False,
        "known": [e["id"] for e in info["entries"] if e.get("id")][:KNOWN_CAP],
        "fresh": [],
    }
    data["items"].append(item)
    save(data)
    return {"choose": False, "item": _public(item)}


def _guess_kind(url: str) -> str:
    low = (url or "").lower()
    if "list=" in low or "/playlist" in low:
        return "playlist"
    return "channel"


def remove(item_id: str) -> bool:
    data = load()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    save(data)
    return len(data["items"]) != before


def set_paused(item_id: str, paused: bool) -> bool:
    data = load()
    item = _find(data, item_id)
    if not item:
        return False
    item["paused"] = bool(paused)
    save(data)
    return True


def set_auto(item_id: str, auto: bool) -> bool:
    """Turn downloading on for one followed item. Off everywhere by default."""
    data = load()
    item = _find(data, item_id)
    if not item:
        return False
    item["auto"] = bool(auto)
    save(data)
    return True


def clear_new(item_id: str, video_id: str = "") -> bool:
    """
    Take one video, or all of them, off the "new" list.

    They stay in `known`, so nothing that has been dismissed can come back and
    be announced a second time.
    """
    data = load()
    item = _find(data, item_id)
    if not item:
        return False
    if video_id:
        item["fresh"] = [v for v in item["fresh"] if v.get("id") != video_id]
    else:
        item["fresh"] = []
    save(data)
    return True


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def _look(item: dict, settings: dict) -> tuple:
    """
    One look at a followed thing: (entries, used, title).

    The feed first where there is one, the engine when there is not or when
    the feed has just failed. `used` says which, because the caller has to
    know whether a full check still needs doing.
    """
    if item.get("feed") and item.get("feed_fail", 0) < FEED_STRIKES:
        try:
            entries = read_feed(item["feed"])
            # An empty feed is not "nothing new" - it is a feed not answering
            # for this id. Falling through to the engine is the only honest
            # reading of it.
            if entries:
                return entries, "feed", ""
        except (urllib.error.URLError, OSError, ElementTree.ParseError,
                ValueError):
            pass
        item["feed_fail"] = int(item.get("feed_fail", 0)) + 1

    with _asking:
        info = engine.peek(item["url"], settings, PEEK)
    return info["entries"], "engine", info.get("title") or ""


def check(item_id: str, full: bool = False) -> dict:
    """One check, now. Used by the timer and by the Check now button alike."""
    data = load()
    item = _find(data, item_id)
    if not item:
        return {"ok": False, "error": "Not being followed."}

    _status["busy"] = item.get("title") or item["url"]
    settings = engine.load_settings()
    try:
        if full:
            with _asking:
                info = engine.peek(item["url"], settings, PEEK)
            entries, used, title = info["entries"], "engine", info.get("title") or ""
        else:
            entries, used, title = _look(item, settings)
    except Exception as exc:
        message = str(exc)[:200]
        data = load()                      # re-read: a check takes seconds
        item = _find(data, item_id)
        if item:
            item["error"] = message
            item["botcheck"] = engine.is_botcheck(message)
            item["checked"] = time.time()
            save(data)
        return {"ok": False, "error": message}
    finally:
        _status["busy"] = ""

    data = load()
    item = _find(data, item_id)
    if not item:
        return {"ok": False, "error": "Not being followed."}

    known = set(item.get("known") or [])
    found = []
    for entry in entries:
        if not entry.get("id") or entry["id"] in known:
            continue
        found.append({
            "id": entry["id"],
            "url": entry["url"],
            "title": entry["title"],
            "thumbnail": entry.get("thumbnail") or "",
            "duration": entry.get("duration"),
            "found": time.time(),
        })

    if found:
        # Newest first, and every id recorded whether or not the user acts on
        # it - the list is "what is new", not "what is waiting".
        item["fresh"] = (found + item.get("fresh", []))[:FRESH_CAP]
        item["known"] = ([f["id"] for f in found] + list(item.get("known") or []))[:KNOWN_CAP]

    if title:
        item["title"] = title
    item["checked"] = time.time()
    if used == "engine":
        item["full_checked"] = time.time()
        item["feed_fail"] = 0              # the engine answered; start over
    item["error"] = ""
    item["botcheck"] = False

    taken = _auto_download(item, found, settings) if found else []
    if taken:
        ids = {t["id"] for t in taken}
        item["fresh"] = [v for v in item["fresh"] if v.get("id") not in ids]

    save(data)

    # Following runs on a timer, usually with the window closed or minimised,
    # so a badge nobody is looking at is the same as saying nothing. Told once
    # per check, naming the channel - and saying plainly whether anything was
    # downloaded, because that is the one thing a person needs to know.
    if found:
        _announce(item.get("title") or "A channel you follow", len(found), len(taken))

    return {"ok": True, "new": len(found), "downloaded": len(taken),
            "via": used, "item": _public(item)}


def _auto_download(item: dict, found: list, settings: dict) -> list:
    """
    Queue new videos for a followed item that was told to download them.

    Off unless both switches are on - the one for this item and the one for
    the app - and capped, because a channel that posts forty things overnight
    must not be able to fill a drive while nobody is watching.
    """
    if not item.get("auto") or not settings.get("watch_auto"):
        return []
    try:
        import app
        manager = getattr(app, "manager", None)
        if manager is None:
            return []
        quality = settings.get("default_quality", "best")
        taken = []
        for video in found[:AUTO_MAX_PER_CHECK]:
            manager.add(url=video["url"], title=video.get("title", ""),
                        thumbnail=video.get("thumbnail", ""),
                        uploader=item.get("uploader", ""), quality=quality)
            taken.append(video)
        return taken
    except Exception:                       # noqa: BLE001
        return []                           # a failed queue is not a failed check


def _announce(where: str, count: int, taken: int = 0) -> None:
    """Tray notification for new items. Never raises into the check."""
    try:
        import app
        tray = getattr(app, "tray_app", None)
        if tray is None:
            return
        what = "1 new video" if count == 1 else f"{count} new videos"
        if taken:
            rest = f"{taken} downloading." if taken > 1 else "1 downloading."
        else:
            rest = "nothing has been downloaded."
        tray.notify(where[:60], what + " - " + rest, "watch")
    except Exception:                       # noqa: BLE001
        pass                                # a missed notice is not a failure


def check_all() -> dict:
    """
    Every followed item, one after another with a gap between them.

    Started on its own thread and answered straight away. The gap is the one
    that item's check needs: a feed read is a public document and takes
    seconds, an engine fetch takes ninety.
    """
    if _sweeping.is_set():
        return {"ok": True, "running": True}

    def run():
        _sweeping.set()
        _status["state"] = "checking"
        try:
            items = [i for i in load()["items"] if not i.get("paused")]
            for index, item in enumerate(items):
                if index:
                    _stop.wait(FEED_SPACING if item.get("feed") else SPACING)
                if _stop.is_set():
                    break
                try:
                    check(item["id"])
                except Exception as exc:
                    _status["error"] = str(exc)[:160]
        finally:
            _sweeping.clear()
            _status["state"] = "on" if engine.load_settings().get("watch") else "off"

    threading.Thread(target=run, name="riplox-watch-all", daemon=True).start()
    return {"ok": True, "running": True}


# --------------------------------------------------------------------------
# The timer
# --------------------------------------------------------------------------

def _due(items: list, now: float) -> tuple:
    """
    The next item to check, and whether it is time for a full one.

    A full check exists to repair what the feed missed, so it is looked for
    first - it is rare, and an item that never got one would drift.
    """
    for item in items:
        if item.get("paused"):
            continue
        if item.get("feed") and now - item.get("full_checked", 0) >= FULL_EVERY:
            return item, True
    for item in items:
        if item.get("paused"):
            continue
        if now - item.get("checked", 0) >= interval(item):
            return item, False
    return None, False


def _loop() -> None:
    global _last_check
    while not _stop.is_set():
        _stop.wait(TICK)
        if _stop.is_set():
            break
        if not engine.load_settings().get("watch"):
            continue

        now = time.time()
        due, full = _due(load()["items"], now)
        if due is None:
            continue

        # An engine fetch keeps its spacing; a feed read does not need it.
        gap = SPACING if (full or not due.get("feed")) else FEED_SPACING
        if now - _last_check < gap:
            continue

        _last_check = now
        _status["state"] = "checking"
        try:
            check(due["id"], full=full)
        except Exception as exc:
            _status["error"] = str(exc)[:160]
        _status["state"] = "on"


def start() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _status["state"] = "on"
        _thread = threading.Thread(target=_loop, name="riplox-watch", daemon=True)
        _thread.start()


def stop() -> None:
    global _thread
    _stop.set()
    _status["state"] = "off"
    with _lock:
        _thread = None


def apply_setting(on: bool) -> None:
    if on:
        start()
    else:
        stop()


# --------------------------------------------------------------------------
# What the screen shows
# --------------------------------------------------------------------------

def _public(item: dict) -> dict:
    return {
        "id": item["id"],
        "url": item["url"],
        "kind": item.get("kind", "channel"),
        "title": item.get("title") or item["url"],
        "uploader": item.get("uploader", ""),
        "thumbnail": item.get("thumbnail", ""),
        "added": item.get("added", 0),
        "checked": item.get("checked", 0),
        "paused": bool(item.get("paused")),
        "error": item.get("error", ""),
        "botcheck": bool(item.get("botcheck")),
        # Whether this one is read from a published feed or fetched with the
        # engine. It decides how often it may be checked, so the screen says
        # which it is rather than leaving the difference invisible.
        "feed": bool(item.get("feed")),
        "auto": bool(item.get("auto")),
        "every": int(interval(item) // 60),
        "watching": len(item.get("known") or []),
        "new": item.get("fresh", []),
    }


def state() -> dict:
    settings = engine.load_settings()
    items = [_public(i) for i in load()["items"]]
    return {
        "on": bool(settings.get("watch")),
        "acknowledged": bool(settings.get("watch_ack")),
        "minutes": minutes(),
        "choices": list(MINUTES),
        "auto": bool(settings.get("watch_auto")),
        "autoMax": AUTO_MAX_PER_CHECK,
        "slowFloor": SLOW_FLOOR,
        "state": _status["state"],
        "busy": _status["busy"],
        "sweeping": _sweeping.is_set(),
        "max": MAX_ITEMS,
        "items": items,
        "new": sum(len(i["new"]) for i in items),
    }
