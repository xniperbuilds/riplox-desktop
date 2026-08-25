"""
The browser extension's way in, without a question every time.

Chrome will hand a riplox:// link to Windows, but it asks first, and newer
versions of Chrome no longer offer "always allow" - so that route costs a click
on every single download. Native messaging is the interface Chrome provides for
exactly this: a program the browser is told about at install time, which it may
talk to without asking anyone.

This program does one thing. It reads a link from the browser and writes it into
the same inbox a riplox:// click writes to, so both routes end in one place and
the running copy of Riplox needs to know nothing about either.

It deliberately does not start downloads, open windows, or talk to the network.
It is the smallest thing that can be trusted with a browser's ear.

The protocol is Chrome's: each message is a little-endian uint32 length followed
by that many bytes of UTF-8 JSON, on stdin and stdout.
"""

import json
import os
import struct
import sys
import time
from pathlib import Path

APP_NAME = "RiploxDesktop"
MAX_MESSAGE = 1024 * 1024          # a link, not a payload
INBOX_CAP = 200


# Where the app said to put things. Written beside this exe when a portable
# copy connects a browser - see app._write_host_manifest.
POINTER = "host-data-dir.txt"


def _beside() -> Path:
    """The folder this program runs from."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """
    The same folder Riplox itself uses - which is NOT always LOCALAPPDATA.

    It used to assume it was, and that was a silent bug: engine.data_dir()
    learned about portable copies and this did not. A portable Riplox read its
    own Data folder while the browser dropped links into LOCALAPPDATA, so the
    extension said "sent" and nothing ever arrived.

    This cannot simply call engine.data_dir(). RiploxHost.exe is built from
    this one file, and importing the engine would drag the whole application
    into a program whose entire job is to pass a link along. So the app hands
    the answer over instead, with the two derivable cases behind it.
    """
    here = _beside()

    # 1. What the app told us, if it is still true. A portable drive can come
    #    back as a different letter, and a pointer at a folder that no longer
    #    exists is exactly the silence this function exists to prevent.
    try:
        told = (here / POINTER).read_text(encoding="utf-8").strip()
        if told and Path(told).is_dir():
            return Path(told)
    except OSError:
        pass

    # 2. A Data folder beside this exe - the portable ZIP, where RiploxHost.exe
    #    sits next to Riplox.exe and shares its folder.
    beside = here / "Data"
    if beside.is_dir():
        return beside

    # 3. An ordinary install.
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_message():
    """One message from the browser, or None when it has hung up."""
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    length = struct.unpack("<I", header)[0]
    if length == 0 or length > MAX_MESSAGE:
        return None
    body = sys.stdin.buffer.read(length)
    if len(body) < length:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def send_message(payload: dict) -> None:
    raw = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(raw)))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def usable(url: str) -> bool:
    if not url or len(url) > 2000:
        return False
    return url.lower().startswith(("http://", "https://"))


def put(url: str, quality: str) -> None:
    """
    Append to the inbox, the same way Riplox does.

    Written to a temporary file and moved into place, so a copy of Riplox
    reading at the same moment sees either the old list or the new one and
    never half of either.
    """
    path = data_dir() / "inbox.json"
    try:
        waiting = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(waiting, list):
            waiting = []
    except (OSError, ValueError):
        waiting = []

    waiting.append({"url": url, "quality": quality, "at": time.time()})
    waiting = waiting[-INBOX_CAP:]

    tmp = path.with_name(f"inbox.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(waiting), encoding="utf-8")
    os.replace(tmp, path)


def running() -> int:
    """
    How many downloads are on the go, read off the same file Riplox saves.

    Read-only, local, and nothing is started or changed by asking. That keeps
    this program what it says it is - it now reads one more file than it did,
    and still opens nothing, launches nothing and talks to nobody.

    A missing file is not an error: it means Riplox has never run here, which
    is a truthful answer of zero.
    """
    try:
        saved = json.loads((data_dir() / "queue.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    jobs = saved.get("jobs") if isinstance(saved, dict) else saved
    if not isinstance(jobs, list):
        return 0
    live = ("queued", "starting", "downloading", "converting")
    return sum(1 for j in jobs
               if isinstance(j, dict) and j.get("status") in live)


def waiting() -> dict:
    """
    What has been handed over and not collected yet.

    Riplox drains this file every 1.5 seconds while it runs, so a link sitting
    here with an old timestamp means nothing is draining it. That is the only
    thing the browser actually needs to know: the difference between "sent, it
    is downloading" and "sent, and it will download when you open Riplox".
    Until now the extension could say neither, and the loudest complaint about
    every tool of this shape is not knowing which one happened.

    Worked out from the inbox alone, on purpose. The obvious alternatives were
    a file Riplox touches every few seconds, or the port in instance.json - and
    an instance.json five days stale once pointed at a dead port while Riplox
    ran happily on another. A file that claims to be fresh and is not is worse
    than no answer at all; an old timestamp cannot lie in that direction.
    """
    try:
        items = json.loads((data_dir() / "inbox.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"waiting": 0, "oldest": 0.0}
    if not isinstance(items, list) or not items:
        return {"waiting": 0, "oldest": 0.0}

    now = time.time()
    ages = [now - float(item.get("at") or now)
            for item in items if isinstance(item, dict)]
    # A clock that stepped backwards would otherwise report a negative age,
    # which reads as "collected in the future".
    return {"waiting": len(items), "oldest": max(0.0, max(ages, default=0.0))}


def main() -> None:
    while True:
        message = read_message()
        if message is None:
            return                      # the browser closed the pipe

        if not isinstance(message, dict):
            send_message({"ok": False, "error": "bad message"})
            continue

        # A question rather than a link. Answered without touching the inbox,
        # so asking can never queue anything by accident.
        if message.get("ask") == "status":
            send_message({"ok": True, "active": running(), **waiting()})
            continue

        url = str(message.get("url") or "").strip()
        if not usable(url):
            send_message({"ok": False, "error": "not a link"})
            continue

        try:
            put(url, str(message.get("quality") or "").strip())
        except OSError as exc:
            # The extension is told, so it can fall back to riplox:// rather
            # than reporting a send that never happened.
            send_message({"ok": False, "error": str(exc)[:120]})
            continue

        send_message({"ok": True})


if __name__ == "__main__":
    main()
