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


def data_dir() -> Path:
    """The same folder Riplox itself uses, worked out the same way."""
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
            send_message({"ok": True, "active": running()})
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
