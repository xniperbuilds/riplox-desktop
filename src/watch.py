"""
Watching a channel or a playlist for new videos.

What this does is deliberately small: it looks at the newest few items every so
often and tells you which ones it had not seen before. It never downloads
anything by itself. That single limit is what keeps the feature honest -

  * nothing appears on your disk that you did not ask for,
  * a mistake costs a list you can ignore, not a full drive,
  * and the check itself stays small enough to look like a person opening a
    page rather than a program scraping one.

The risk this feature carries is real and is not hidden from the user: any
repeated automated request to YouTube can end in "Sign in to confirm you're not
a bot". So the checks are slow by default, one at a time, never in a burst, and
the screen that turns this on says all of that before it is switched on - along
with what to do if it happens.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time

import engine

MAX_ITEMS = 20               # more than anyone watches, few enough to stay slow
PEEK = 30                    # newest N per check
KNOWN_CAP = 400              # ids remembered per item
FRESH_CAP = 60               # unseen videos held per item

# One check per tick at most, so ten watched channels are ten separate requests
# minutes apart rather than ten at once. A burst is what a rate limiter sees.
TICK = 60
SPACING = 90

HOURS = (6, 12, 24, 48)
DEFAULT_HOURS = 12

_lock = threading.RLock()
_stop = threading.Event()
_sweeping = threading.Event()    # a Check all is already walking the list
# Only ever one request in the air. The timer already spaces its checks out,
# but Check now and Check all can both be pressed while one is running, and
# three requests arriving together is exactly the shape that gets an address
# asked to prove it is a person.
_asking = threading.Lock()
_thread = None
_last_check = 0.0
_status = {"state": "off", "busy": "", "error": ""}


# --------------------------------------------------------------------------
# Stored state
# --------------------------------------------------------------------------

def _file():
    return engine.data_dir() / "watch.json"


def load() -> dict:
    with _lock:
        try:
            with open(_file(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {"items": []}
        data.setdefault("items", [])
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


def interval() -> float:
    hours = engine.load_settings().get("watch_hours", DEFAULT_HOURS)
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = DEFAULT_HOURS
    return (hours if hours in HOURS else DEFAULT_HOURS) * 3600.0


# --------------------------------------------------------------------------
# Adding
# --------------------------------------------------------------------------

def add(url: str, kind: str = "") -> dict:
    """
    Start watching a link.

    The first check is a baseline, not a result: every video it can see is
    written down as already known. Without that, adding a channel would
    announce its entire back catalogue as new.
    """
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("That does not look like a link.")

    data = load()
    if len(data["items"]) >= MAX_ITEMS:
        raise ValueError(f"Watching {MAX_ITEMS} things already. Remove one first.")
    for item in data["items"]:
        if item["url"] == url:
            raise ValueError("That one is already being watched.")

    settings = engine.load_settings()
    info = engine.peek(url, settings, PEEK)

    # A bare channel address answers with its tabs - Videos, Shorts, Live - and
    # a tab list never changes when a video is posted. Watching it would look
    # like it was working and never find anything, so the tabs are handed back
    # for the user to choose from instead.
    if info.get("is_tabs"):
        return {"choose": True, "title": info.get("title") or "Channel",
                "tabs": info.get("tabs") or []}

    item = {
        "id": secrets.token_hex(6),
        "url": url,
        "kind": kind if kind in ("channel", "playlist") else _guess_kind(url),
        "title": info.get("title") or url,
        "uploader": info.get("uploader") or "",
        "thumbnail": info.get("thumbnail") or "",
        "added": time.time(),
        "checked": time.time(),
        "paused": False,
        "error": "",
        "botcheck": False,
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

def check(item_id: str) -> dict:
    """One check, now. Used by the timer and by the Check now button alike."""
    data = load()
    item = _find(data, item_id)
    if not item:
        return {"ok": False, "error": "Not being watched."}

    _status["busy"] = item.get("title") or item["url"]
    try:
        with _asking:
            info = engine.peek(item["url"], engine.load_settings(), PEEK)
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
        return {"ok": False, "error": "Not being watched."}

    known = set(item.get("known") or [])
    found = []
    for entry in info["entries"]:
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

    if info.get("title"):
        item["title"] = info["title"]
    item["checked"] = time.time()
    item["error"] = ""
    item["botcheck"] = False
    save(data)
    return {"ok": True, "new": len(found), "item": _public(item)}


def check_all() -> dict:
    """
    Every watched item, one after another with a gap between them.

    Started on its own thread and answered straight away: twenty items spaced
    ninety seconds apart is half an hour, which no button press should wait
    for. The screen follows along through state().
    """
    if _sweeping.is_set():
        return {"ok": True, "running": True}

    def run():
        _sweeping.set()
        _status["state"] = "checking"
        try:
            ids = [i["id"] for i in load()["items"] if not i.get("paused")]
            for index, item_id in enumerate(ids):
                if index:
                    _stop.wait(SPACING)
                if _stop.is_set():
                    break
                try:
                    check(item_id)
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

def _loop() -> None:
    global _last_check
    while not _stop.is_set():
        _stop.wait(TICK)
        if _stop.is_set():
            break
        if not engine.load_settings().get("watch"):
            continue

        # One item per tick, and only if it is actually due. Ten watched
        # channels become ten requests a minute apart, never ten at once.
        if time.time() - _last_check < SPACING:
            continue

        due = None
        gap = interval()
        for item in load()["items"]:
            if item.get("paused"):
                continue
            if time.time() - item.get("checked", 0) >= gap:
                due = item["id"]
                break

        if due:
            _last_check = time.time()
            _status["state"] = "checking"
            try:
                check(due)
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
        "watching": len(item.get("known") or []),
        "new": item.get("fresh", []),
    }


def state() -> dict:
    settings = engine.load_settings()
    items = [_public(i) for i in load()["items"]]
    return {
        "on": bool(settings.get("watch")),
        "acknowledged": bool(settings.get("watch_ack")),
        "hours": settings.get("watch_hours", DEFAULT_HOURS),
        "choices": list(HOURS),
        "state": _status["state"],
        "busy": _status["busy"],
        "sweeping": _sweeping.is_set(),
        "max": MAX_ITEMS,
        "items": items,
        "new": sum(len(i["new"]) for i in items),
    }
