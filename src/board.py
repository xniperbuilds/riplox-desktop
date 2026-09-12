"""
The Board - one public room of links, read and written from the app.

Everything that decides anything lives in the Worker (board/worker.js). This
file asks it questions and holds a socket open while somebody is looking at the
Board, and that is deliberately all it does: Riplox is open source, so a check
made here is a convenience for the person typing and never a gate. There is no
copy of the link parser in this file for the same reason - one parser, in one
place, asked over /check.

Nothing here may raise into the app. Every call answers with a dict carrying
"ok", because a board that cannot be reached is a normal Tuesday and must never
be the reason a window does not open.
"""

import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import engine
from sharing import RELAY_HEADERS, _Wire

# The Board's own Worker, on its own address. Separate from the relay on
# purpose: they share a Cloudflare account and therefore a daily allowance, but
# the relay is what Send runs on and nothing about a link board should ever be
# a reason to redeploy it.
DEFAULT_BOARD = "https://board.xniperbuilds.com"

# How long a socket stays open after the Board stops being looked at.
#
# The socket exists only while somebody is watching, which is the whole reason
# this costs almost nothing: a user who never opens the Board never connects at
# all. Sixty seconds of grace so that clicking to the Queue and back does not
# reconnect, which would cost a request each way.
IDLE_SECONDS = 60

# How long a read waits before the loop gets a turn.
#
# This number does not touch the network - nothing is sent and nothing is
# asked for. It exists because a blocking read is the only thing the socket
# thread is doing, so until it comes back the thread cannot notice that the
# Board was left. With the read waiting for a ping interval, "closes a minute
# after they stop looking" was really "closes up to ten minutes after", which
# is the one number this feature's whole cost argument rests on.
READ_SECONDS = 5

# Read from the server on connect rather than decided here, so that the number
# can be changed for copies that are already installed. This is only the value
# used before the server has said anything.
PING_SECONDS = 600

_lock = threading.RLock()
_thread = None
_stop = threading.Event()
_watching = 0.0          # when the Board was last looked at
_live = []               # rows that arrived down the socket, waiting for a read
_note = ""               # why the socket is not connected, in words
_ping = PING_SECONDS


def base() -> str:
    """The Board as an https origin, whatever it was typed as."""
    url = (engine.load_settings().get("board_url") or DEFAULT_BOARD).strip().rstrip("/")
    if url.startswith("wss://"):
        return "https://" + url[6:]
    if url.startswith("ws://"):
        return "http://" + url[5:]
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def install_id() -> str:
    """
    A random value, made once, that identifies this copy of the app.

    Not a person and not derived from anything about one: no machine id, no
    account, no address. The server stores only a salted hash of it, and it is
    never sent to another client - so the Board cannot become a set of
    pseudonymous profiles with a posting history attached.

    Anyone can delete the file and get a new one, which is exactly why the
    server also counts by address.
    """
    path = engine.data_dir() / "board_id.txt"
    try:
        got = path.read_text(encoding="utf-8").strip()
        if len(got) >= 16:
            return got
    except OSError:
        pass
    made = secrets.token_hex(16)
    try:
        path.write_text(made, encoding="utf-8")
    except OSError:
        # A read-only data directory is a portable copy on a locked stick. It
        # still works; it just counts as a new install every time.
        pass
    return made


def _ask(path: str, body=None, timeout: float = 12.0) -> dict:
    """One request. Never raises - the answer always has "ok" in it."""
    url = base() + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = dict(RELAY_HEADERS)
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        request = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # The Worker answers refusals with JSON and a status - 429 for a limit,
        # 503 for a closed board. Read the body rather than turning all of them
        # into "something went wrong", because every one of those has been
        # written as a sentence somebody can act on.
        try:
            return json.loads(exc.read().decode("utf-8", "replace"))
        except (ValueError, OSError):
            return {"ok": False, "error": "The board did not answer properly."}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"ok": False, "error": "Could not reach the board."}


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def state() -> dict:
    """Whether the Board is on, and what it wants to say."""
    global _ping
    got = _ask("/state")
    if got.get("ok"):
        _ping = int(got.get("ping") or PING_SECONDS)
    return got


def feed(platform: str = "", before: int = 0, frm: int = 0, to: int = 0, q: str = "") -> dict:
    """A page of the board, newest first."""
    query = {}
    if platform:
        query["platform"] = platform
    if before:
        query["before"] = int(before)
    if frm:
        query["from"] = int(frm)
    if to:
        query["to"] = int(to)
    if q:
        query["q"] = q[:60]
    tail = ("?" + urllib.parse.urlencode(query)) if query else ""
    return _ask("/feed" + tail)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def share(url: str) -> dict:
    """
    Put a link on the board.

    Three steps, in this order for a reason:

    1. Ask the Worker whether the link is one the board takes. That is one
       small request, and it is what stops step 2 - which runs the download
       engine and takes seconds - from being spent on a link that was never
       going to be accepted.
    2. Read the title. The person posting already chose to open this link, so
       their own machine asking about it leaks nothing they had not already
       done; everybody reading the board gets a readable row without their own
       address touching a platform.
    3. Post it.

    A title that cannot be read is not a failure. The link goes up bare - that
    is a worse row, not a lost one, and refusing to post because a title was
    unavailable would be the app deciding something the user did not ask it to.
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "Paste a link first."}

    checked = _ask("/check", {"url": url})
    if not checked.get("ok"):
        return checked

    title = ""
    try:
        info = engine.analyze(checked["url"], engine.load_settings(), limit=1)
        title = str(info.get("title") or "")
        if title.lower() in ("untitled", "playlist", "channel"):
            title = ""
    except Exception:
        # Deliberately everything. This is the optional half of a post and it
        # runs a subprocess against a site nobody controls; there is no failure
        # here worth turning into a refusal the user has to read.
        title = ""

    return _ask("/share", {"url": checked["url"], "title": title, "install": install_id()})


def report(link_id: int) -> dict:
    """Flag a link. The row is hidden for this person the moment it is sent."""
    return _ask("/report", {"id": int(link_id), "install": install_id()})


# --------------------------------------------------------------------------
# The socket
#
# One connection, open only while somebody is looking at the Board, closed a
# minute after they stop. state.acceptWebSocket() on the other end means the
# Durable Object hibernates between messages, so an open socket costs nothing
# while it is quiet - but a socket nobody is watching still costs a hello every
# few minutes for no reason at all, which is what IDLE_SECONDS is for.
# --------------------------------------------------------------------------

def watching() -> None:
    """Called while the Board is on screen. Starts the socket if it is not."""
    global _thread, _watching
    with _lock:
        _watching = time.time()
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run, name="board-socket", daemon=True)
        _thread.start()


def drain() -> list:
    """Rows that arrived since the last time this was asked."""
    with _lock:
        got, _live[:] = list(_live), []
    return got


def note() -> str:
    """Why the socket is not connected, if it is not. Empty when it is fine."""
    with _lock:
        return _note


def stop() -> None:
    _stop.set()


def _socket_url() -> str:
    origin = base()
    scheme = "wss" if origin.startswith("https") else "ws"
    return f"{scheme}://{origin.split('://', 1)[-1]}/ws"


def _run() -> None:
    """
    Keep a socket while the Board is being watched.

    A network that will not pass a WebSocket upgrade gets told so and nothing
    else. There is deliberately no long-poll fallback: a held-open request
    stops the Durable Object hibernating, and that is the one shape already
    known to be able to spend this account's whole daily allowance - the relay
    measured one always-on PC on that path at 83% of a day. The Board is a
    nice-to-have and will not be the thing that takes Send down.
    """
    global _note, _ping, _thread
    while not _stop.is_set():
        if time.time() - _watching > IDLE_SECONDS:
            break

        wire = None
        try:
            wire = _Wire(_socket_url(), timeout=30)
            # Short reads from here on, so that the check at the top of the
            # loop below actually gets to run. See READ_SECONDS.
            wire.sock.settimeout(READ_SECONDS)
            with _lock:
                _note = ""
            wire.send(json.dumps({"hello": 1}))
            last = time.time()

            while not _stop.is_set() and time.time() - _watching <= IDLE_SECONDS:
                try:
                    message = wire.recv()
                except socket.timeout:
                    # Nothing arrived, which is what a quiet board looks like.
                    # Not a failure, and not a reason to reconnect - just the
                    # moment this loop gets to look at the clock.
                    if time.time() - last >= _ping:
                        wire.send(json.dumps({"hello": 1}))
                        last = time.time()
                    continue
                except OSError:
                    break
                try:
                    got = json.loads(message)
                except ValueError:
                    continue

                if isinstance(got.get("row"), dict):
                    with _lock:
                        _live.append(got["row"])
                        # A board nobody has looked at for an hour must not
                        # grow without limit in memory. The screen only ever
                        # shows a page anyway, and anything dropped here is
                        # still in the table.
                        del _live[:-200]
                if got.get("ping"):
                    _ping = int(got["ping"])
                if time.time() - last >= _ping:
                    wire.send(json.dumps({"hello": 1}))
                    last = time.time()
        except OSError as exc:
            with _lock:
                _note = _why(str(exc))
            # One reconnect a minute at most. A board that cannot be reached is
            # not worth a tight loop, and a Worker that is refusing everybody
            # should not be asked harder.
            _stop.wait(60)
        finally:
            if wire is not None:
                wire.close()

    with _lock:
        _thread = None


def _why(text: str) -> str:
    """The reason, in the words the person needs rather than the socket's."""
    low = text.lower()
    if "426" in low or "upgrade" in low or "400" in low:
        return "This network will not allow the Board's live connection. Refresh to see new links."
    if "503" in low or "closed" in low:
        return "The board is closed right now."
    return "Not connected to the board."
