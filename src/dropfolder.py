"""
A folder Riplox watches, so anything on this PC can hand it a download.

Drop a file of links into it and they are queued. That is the whole feature.
It exists because everything else that can start a download needs Riplox to
be the thing being used - the window, the clipboard, the extension, a paired
phone - and a script, a scheduled task, or another program has none of those.
Writing a file is the one thing every one of them can already do.

Two shapes are read, and neither needs anything installed:

    one link per line, in a .txt          - what a person writes
    {"url": "...", "quality": "1080"}     - what a program writes, as .json,
                                            or a list of those

A file is read once and then renamed rather than deleted: `name.txt.done`, or
`name.txt.bad` with the reason inside it. Deleting somebody's file to signal
success is not a signal, it is a loss - and a file that failed has to leave
something behind to look at, or the folder is a place where things vanish.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import engine

# Read on a timer rather than by watching the filesystem: a watcher has to be
# right about every rename, move and network share, and this has to be right
# about nothing at all. Five seconds is under the time it takes to alt-tab.
TICK = 5.0
MAX_LINKS = 200               # one file must not become an afternoon
MAX_BYTES = 512 * 1024
SUFFIXES = (".txt", ".json", ".riplox")

_stop = threading.Event()
_thread = None
_lock = threading.RLock()
_sink = None                  # set by app.py; the thing that queues a link
_status = {"state": "off", "last": "", "queued": 0, "error": ""}


def folder() -> Path:
    """Where to look. Settable, and made on demand rather than at startup."""
    chosen = (engine.load_settings().get("drop_dir") or "").strip()
    return Path(chosen) if chosen else engine.data_dir() / "drop"


def set_sink(fn) -> None:
    """`fn(url, quality, opts)` queues one link. Given by app.py."""
    global _sink
    _sink = fn


# --------------------------------------------------------------------------
# Reading one file
# --------------------------------------------------------------------------

def parse(text: str) -> list:
    """
    The links in a dropped file, with whatever was asked for each of them.

    JSON first, because a program that took the trouble to write JSON meant
    it. Anything else is read as one link per line, which is what a person
    types and what every other tool exports.
    """
    text = (text or "").strip()
    if not text:
        return []

    if text[0] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            return []                      # not the JSON it looked like
        rows = data if isinstance(data, list) else [data]
        out = []
        for row in rows[:MAX_LINKS]:
            if isinstance(row, str):
                row = {"url": row}
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                continue
            job = {"url": url}
            quality = str(row.get("quality") or "").strip()
            if quality in engine.QUALITY_LABELS:
                job["quality"] = quality
            where = str(row.get("folder") or row.get("dest_dir") or "").strip()
            if where:
                job["dest_dir"] = where[:400]
            out.append(job)
        return out

    out = []
    for line in text.splitlines():
        line = line.strip()
        # A comment, because a list of links people maintain by hand always
        # grows a note at the top of it.
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith(("http://", "https://")):
            out.append({"url": line})
        if len(out) >= MAX_LINKS:
            break
    return out


def _finish(path: Path, suffix: str, note: str = "") -> None:
    """
    Move a file aside once it has been read.

    Never deleted: a file that arrived here came from somewhere, and making it
    disappear is indistinguishable from losing it. `.done` says it was taken,
    `.bad` says it was not and has the reason inside.
    """
    target = path.with_name(path.name + suffix)
    try:
        if note:
            target.write_text(note + "\n\n" + path.read_text(
                encoding="utf-8", errors="replace"), encoding="utf-8")
            path.unlink()
        else:
            if target.exists():
                target = path.with_name(f"{path.name}.{int(time.time())}{suffix}")
            os.replace(path, target)
    except OSError:
        pass                  # a folder we cannot write to is not a crash


def take(path: Path) -> int:
    """Read one dropped file and queue what is in it. Returns how many."""
    try:
        if path.stat().st_size > MAX_BYTES:
            _finish(path, ".bad", "# Too big to be a list of links.")
            return 0
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _finish(path, ".bad", f"# Could not read it: {exc}")
        return 0

    jobs = parse(text)
    if not jobs:
        _finish(path, ".bad", "# No links in it. One link per line, or JSON.")
        return 0
    if _sink is None:
        return 0              # not wired yet; leave the file for the next tick

    queued = 0
    for job in jobs:
        try:
            _sink(job["url"], job.get("quality", ""),
                  {"dest_dir": job["dest_dir"]} if job.get("dest_dir") else {})
            queued += 1
        except Exception:                  # noqa: BLE001
            pass              # one bad link must not strand the other 199

    _finish(path, ".done")
    _status["last"] = path.name
    _status["queued"] += queued
    return queued


def sweep() -> int:
    """Everything waiting in the folder, once."""
    where = folder()
    try:
        where.mkdir(parents=True, exist_ok=True)
        names = sorted(p for p in where.iterdir()
                       if p.is_file() and p.suffix.lower() in SUFFIXES)
    except OSError as exc:
        _status["error"] = str(exc)[:160]
        return 0

    _status["error"] = ""
    total = 0
    for path in names[:20]:
        total += take(path)
    return total


# --------------------------------------------------------------------------
# The timer
# --------------------------------------------------------------------------

def _loop() -> None:
    while not _stop.is_set():
        _stop.wait(TICK)
        if _stop.is_set():
            break
        if not engine.load_settings().get("drop_on"):
            continue
        try:
            sweep()
        except Exception as exc:           # noqa: BLE001
            _status["error"] = str(exc)[:160]


def start() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _status["state"] = "on"
        _thread = threading.Thread(target=_loop, name="riplox-drop", daemon=True)
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


def state() -> dict:
    settings = engine.load_settings()
    return {
        "on": bool(settings.get("drop_on")),
        "folder": str(folder()),
        "state": _status["state"],
        "last": _status["last"],
        "queued": _status["queued"],
        "error": _status["error"],
    }
