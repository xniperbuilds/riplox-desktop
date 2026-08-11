"""
Riplox Send for Windows - the window, the tray icon and the shortcut.

Windows has no share sheet, so the thing that makes this worth installing
rather than opening a web page is the keyboard: copy a link anywhere, press
the shortcut, and it is on its way to the PC that downloads. Nothing opens,
nothing steals focus, and the answer arrives as a notification.

The window is small on purpose - what your PC is doing, and a box to paste a
link into. Pairing lives on its own screen because it happens once.

No web server and no port: pywebview is handed the page directly and the page
calls into Python through js_api. A sender that opened a socket to talk to
itself would be a sender with an attack surface.
"""

import ctypes
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import webview

import send
import store

try:
    import pystray
    from PIL import Image
except ImportError:                 # the tray is a nicety, never a requirement
    pystray = None
    Image = None

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
VERSION = "1.0.0"

_window = None
_tray = None
_quitting = False                   # on the way out on purpose, not to the tray


# --------------------------------------------------------------------------
# The clipboard, read the short way
# --------------------------------------------------------------------------

CF_UNICODETEXT = 13


def clipboard_text() -> str:
    """Whatever is on the clipboard, or "" - never an exception."""
    if os.name != "nt":
        return ""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    # Another program can hold the clipboard for a moment; that is normal.
    for _ in range(5):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return ""

    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.c_wchar_p(pointer).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def first_link(text: str) -> str:
    for word in (text or "").split():
        if word.lower().startswith(("http://", "https://")):
            return word.strip().strip("<>\"'")
    return ""


# --------------------------------------------------------------------------
# Sending, always off the UI thread
# --------------------------------------------------------------------------

def _pairing():
    data = store.load()
    return data.get("room", ""), data.get("key", ""), data.get("lan", "")


def send_link(url: str) -> dict:
    room, key, lan = _pairing()
    if not (room and key):
        return {"ok": False, "why": "", "error": "This PC is not paired yet."}
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "why": "", "error": "That does not look like a link."}

    result = send.send_link(room, key, lan, url)
    # The PC's own address can change on a home network; a send that had to
    # fall back to the relay is the moment to notice that.
    if result.get("via") == "relay" and lan and not send.lan_alive(lan, room):
        data = store.load()
        data["lan"] = ""
        store.save(data)
    return result


def say(title: str, body: str) -> None:
    if _tray is not None and getattr(_tray, "icon", None) is not None:
        try:
            _tray.icon.notify(body, title)
            return
        except Exception:
            pass
    print(f"{title}: {body}")


def send_and_report(url: str) -> None:
    result = send_link(url)
    if result.get("error"):
        say("Riplox Send", result["error"])
        return
    words = send.words(result.get("why", ""))
    if result.get("ok"):
        say("Sent to your PC", words or "Left for your PC")
    else:
        say("Not sent", words or "Your PC refused that one")


# --------------------------------------------------------------------------
# What the page can call
# --------------------------------------------------------------------------

class Api:

    def state(self) -> dict:
        room, key, lan = _pairing()
        if not (room and key):
            return {"paired": False, "status": "none",
                    "label": "Not paired", "why": "Add this PC to Riplox first.",
                    "version": VERSION}

        answer = send.ping(room, key, lan)
        why = answer.get("why", "")
        if why == "pong":
            state, label, note = "ready", "Ready", (
                "Your PC is on and this machine is allowed."
                + (" On your network." if answer.get("via") == "lan" else ""))
        elif why == "paused":
            state, label, note = "paused", "Paused", "Your PC has paused this machine."
        elif why == "revoked":
            state, label, note = "gone", "Removed", "Your PC removed this machine. Pair again."
        elif why == "unknown":
            state, label, note = "gone", "Not recognised", "Your PC did not recognise this machine."
        else:
            state, label, note = "quiet", "PC not answering", (
                "It may be switched off. Links you send are kept for seven days.")
        return {"paired": True, "status": state, "label": label, "why": note,
                "version": VERSION, "lan": bool(lan)}

    def send(self, url: str) -> dict:
        url = (url or "").strip()
        result = send_link(url)
        result["words"] = send.words(result.get("why", ""))
        return result

    def paste(self) -> str:
        return first_link(clipboard_text())

    def pair(self, text: str, name: str = "") -> dict:
        invite = send.read_invite(text)
        if invite is None:
            return {"ok": False, "error": "That code does not look right."}

        result = send.pair(invite, name or send.guess_name())
        if not result.get("ok"):
            return {"ok": False,
                    "error": send.words(result.get("why", "")) or "Your PC did not accept it."}

        store.save({"room": result["room"], "key": result["key"],
                    "lan": result.get("lan", ""), "paired": time.time()})
        return {"ok": True}

    def forget(self) -> dict:
        store.forget()
        return {"ok": True}

    def hide(self) -> dict:
        if _window is not None:
            _window.hide()
        return {"ok": True}


# --------------------------------------------------------------------------
# riploxsend://pair - clicking the pairing link on Windows
# --------------------------------------------------------------------------
# Opening the pairing link in a browser pairs the browser, which is exactly the
# bug the Android app had: the code is single-use, so the browser spends it and
# the app it was meant for is left with nothing. Windows hands a registered
# protocol straight to the program instead, and the installer registers this
# one under HKCU - per-user, no administrator.

SCHEME = "riploxsend"


def pair_argument(argv) -> str:
    """The riploxsend://pair?c=… Windows passed us, or ""."""
    for arg in argv[1:]:
        if str(arg).lower().startswith(SCHEME + "://"):
            return str(arg)
    return ""


def _tell(title: str, body: str, bad: bool = False) -> None:
    """A message box, because at this point there is no window to speak from."""
    try:
        ctypes.windll.user32.MessageBoxW(
            None, body, title, 0x00000010 if bad else 0x00000040)
    except Exception:
        print(f"{title}: {body}")


def pair_from_link(raw: str) -> bool:
    """
    Pair from the link and say what happened. Returns True when it worked.

    Deliberately does not open the window: clicking a pairing link means "add
    this PC", not "show me an app". The code is spent either way, so the answer
    has to be said out loud rather than left for the next time it is opened.
    """
    query = parse_qs(urlsplit(raw).query)
    code = (query.get("c") or [""])[0].strip()
    lan = (query.get("l") or [""])[0].strip()

    invite = send.read_invite(code)
    if invite is None:
        _tell("Riplox Send", "That pairing link is not one this app can read.",
              bad=True)
        return False
    if lan and not invite.get("lan"):
        # The typed code has no room for the PC's address on the local network;
        # carrying it here is what keeps a send from having to leave the house.
        invite["lan"] = lan

    result = send.pair(invite, send.guess_name())
    if not result.get("ok"):
        _tell("Riplox Send",
              send.words(result.get("why", "")) or "Your PC did not accept it.",
              bad=True)
        return False

    store.save({"room": result["room"], "key": result["key"],
                "lan": result.get("lan", ""), "paired": time.time()})
    _tell("Riplox Send", "Paired. Copy a link anywhere and press the shortcut "
                         "to send it to your PC.")
    return True


# --------------------------------------------------------------------------
# The shortcut
# --------------------------------------------------------------------------

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
WM_HOTKEY = 0x0312

# Tried in order. A combination another program already owns simply fails to
# register, and the one that took is the one the window shows - guessing on
# the user's behalf is worse than telling them which it is.
HOTKEYS = [
    (MOD_CONTROL | MOD_SHIFT, 0x53, "Ctrl+Shift+S"),
    (MOD_CONTROL | MOD_ALT, 0x53, "Ctrl+Alt+S"),
    (MOD_CONTROL | MOD_SHIFT, 0x52, "Ctrl+Shift+R"),
    (MOD_CONTROL | MOD_ALT, 0x52, "Ctrl+Alt+R"),
]

hotkey_label = ""


def _hotkey_loop(jobs: queue.Queue) -> None:
    global hotkey_label
    user32 = ctypes.windll.user32

    for index, (mods, key, label) in enumerate(HOTKEYS):
        if user32.RegisterHotKey(None, index + 1, mods | MOD_NOREPEAT, key):
            hotkey_label = label
            break
    else:
        return

    message = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        if message.message == WM_HOTKEY:
            jobs.put(first_link(clipboard_text()))


def _hotkey_worker(jobs: queue.Queue) -> None:
    while True:
        url = jobs.get()
        if not url:
            say("Riplox Send", "No link on the clipboard.")
            continue
        send_and_report(url)


def start_hotkey() -> None:
    jobs = queue.Queue()
    threading.Thread(target=_hotkey_loop, args=(jobs,), daemon=True).start()
    threading.Thread(target=_hotkey_worker, args=(jobs,), daemon=True).start()


# --------------------------------------------------------------------------
# Tray
# --------------------------------------------------------------------------

class Tray:
    def __init__(self, icon_path: Path):
        self.icon_path = icon_path
        self.icon = None

    def start(self):
        if pystray is None or not self.icon_path.exists():
            return
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Open Riplox Send", self._show, default=True),
                pystray.MenuItem("Send the copied link", self._send_clipboard),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit),
            )
            self.icon = pystray.Icon("riploxsend", Image.open(self.icon_path),
                                     "Riplox Send", menu)
            self.icon.run()
        except Exception:
            self.icon = None

    def _show(self, *_):
        if _window is not None:
            _window.show()

    def _send_clipboard(self, *_):
        url = first_link(clipboard_text())
        if not url:
            say("Riplox Send", "No link on the clipboard.")
            return
        threading.Thread(target=send_and_report, args=(url,), daemon=True).start()

    def _quit(self, *_):
        global _quitting
        _quitting = True
        if self.icon is not None:
            self.icon.stop()
        try:
            if _window is not None:
                _window.destroy()
        except Exception:
            pass
        os._exit(0)


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------

def main() -> None:
    global _window, _tray

    # Started by a click on the pairing link rather than by the user: pair,
    # say so, and get out of the way. No window, no second copy running.
    link = pair_argument(sys.argv)
    if link:
        pair_from_link(link)
        return

    _window = webview.create_window(
        "Riplox Send",
        str(BASE / "ui" / "index.html"),
        js_api=Api(),
        width=460,
        height=690,
        min_size=(400, 560),
        background_color="#070C15",
        text_select=False,
    )

    def on_closing():
        # Closing hides it, so the shortcut keeps working. Quit is in the tray.
        # Quit itself destroys the window, which arrives here as an ordinary
        # close - without the flag the app would announce itself on its way out.
        if _quitting:
            return True
        if _tray is not None and _tray.icon is not None:
            _window.hide()
            return False
        return True

    _window.events.closing += on_closing

    def ready(_):
        global _tray
        _tray = Tray(BASE / "ui" / "riploxsend.png")
        _tray.start()
        start_hotkey()
        # The page asks for this once it is up; setting it here means it is
        # already true by then.
        _window.evaluate_js(f"window.HOTKEY = {hotkey_label!r};")

    webview.start(ready, _window)


if __name__ == "__main__":
    main()
