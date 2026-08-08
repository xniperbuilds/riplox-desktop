"""
Riplox Desktop - application shell.

A Flask server bound to localhost renders the UI, and pywebview wraps it in a
native window. Run with --dev to skip the window and open it in a browser
instead (useful while working on the interface).
"""

import ctypes
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import engine

APP_TITLE = "Riplox"
VERSION = "1.0.0"


def resource_dir() -> Path:
    """Where templates and static files live, frozen or not."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


BASE = resource_dir()
app = Flask(__name__, template_folder=str(BASE / "templates"),
            static_folder=str(BASE / "static"))
app.config["JSON_SORT_KEYS"] = False

manager = engine.DownloadManager()
_window = None
tray_app = None

# The UI talks to a real HTTP server on localhost. Anything else running on
# this machine - including a web page open in the user's browser - can reach
# that port too, so every /api call must prove it came from our own page.
TOKEN = secrets.token_urlsafe(24)


@app.before_request
def _guard():
    if not request.path.startswith("/api/"):
        return None

    # Blocks DNS-rebinding, where a hostile domain resolves to 127.0.0.1.
    host = (request.host or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return jsonify({"ok": False, "error": "Blocked."}), 403

    # A second copy of Riplox starting up asks the running one to show itself.
    # It cannot know this run's token, and all the call does is raise a window,
    # so it is the one endpoint that does not need one.
    if request.path == "/api/show":
        return None

    if request.headers.get("X-Riplox-Token") != TOKEN:
        return jsonify({"ok": False, "error": "Blocked."}), 403
    return None


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return render_template(
        "index.html",
        version=VERSION,
        token=TOKEN,
        settings=engine.load_settings(),
        has_ffmpeg=engine.has_ffmpeg(),
        quality_labels=engine.QUALITY_LABELS,
    )


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@app.post("/api/analyze")
def api_analyze():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Paste a link first."}), 400
    if not url.lower().startswith(("http://", "https://")):
        return jsonify({"ok": False, "error": "That does not look like a link."}), 400

    try:
        info = engine.analyze(url, engine.load_settings())
    except engine.EngineMissing:
        return jsonify({"ok": False, "error": "Download engine is missing. Reinstall Riplox."}), 500
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "info": info})


@app.post("/api/add")
def api_add():
    body = request.json or {}
    quality = body.get("quality") or engine.load_settings().get("default_quality", "best")
    items = body.get("items") or []

    if not items:
        return jsonify({"ok": False, "error": "Nothing to download."}), 400

    added = set()
    for item in items[:200]:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        job = manager.add(
            url=url,
            title=item.get("title", ""),
            thumbnail=item.get("thumbnail", ""),
            uploader=item.get("uploader", ""),
            quality=quality,
        )
        # add() returns the running job for a duplicate, so a set keeps the
        # count honest instead of claiming we queued the same thing twice.
        added.add(job.id)

    if not added:
        return jsonify({"ok": False, "error": "No usable links found."}), 400
    return jsonify({"ok": True, "added": len(added)})


@app.get("/api/jobs")
def api_jobs():
    return jsonify({
        "ok": True,
        "jobs": manager.snapshot(),
        "hasFfmpeg": engine.has_ffmpeg(),
    })


@app.post("/api/job/<action>")
def api_job_action(action):
    job_id = (request.json or {}).get("id", "")
    if action == "cancel":
        return jsonify({"ok": manager.cancel(job_id)})
    if action == "retry":
        return jsonify({"ok": manager.retry(job_id)})
    if action == "remove":
        return jsonify({"ok": manager.remove(job_id)})
    return jsonify({"ok": False, "error": "Unknown action."}), 400


@app.post("/api/clear-finished")
def api_clear_finished():
    manager.clear_finished()
    return jsonify({"ok": True})


@app.get("/api/settings")
def api_get_settings():
    return jsonify({"ok": True, "settings": engine.load_settings(),
                    "engineVersion": engine.engine_version(),
                    "hasFfmpeg": engine.has_ffmpeg()})


@app.post("/api/settings")
def api_set_settings():
    saved = engine.save_settings(request.json or {})
    return jsonify({"ok": True, "settings": saved})


@app.post("/api/choose-folder")
def api_choose_folder():
    """Native folder picker. Falls back to the current value outside the shell."""
    current = engine.load_settings()["download_dir"]
    if _window is None:
        return jsonify({"ok": False, "error": "Folder picker needs the app window."}), 400

    import webview
    result = _window.create_file_dialog(webview.FOLDER_DIALOG, directory=current)
    if not result:
        return jsonify({"ok": False, "cancelled": True})

    chosen = result[0] if isinstance(result, (list, tuple)) else result
    saved = engine.save_settings({"download_dir": str(chosen)})
    return jsonify({"ok": True, "settings": saved})



# Opening a file means handing it to the Windows shell, which will happily run
# an executable. Only media we produced, only from inside the download folder.
PLAYABLE = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv",
            ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".aac", ".flac",
            ".jpg", ".jpeg", ".png", ".webp"}


def _inside_downloads(path: Path) -> bool:
    try:
        root = Path(engine.load_settings()["download_dir"]).resolve()
        target = path.resolve()
    except OSError:
        return False
    return target == root or root in target.parents


@app.post("/api/choose-cookies")
def api_choose_cookies():
    """Pick an exported cookies.txt. Works for browsers we cannot read."""
    if _window is None:
        return jsonify({"ok": False, "error": "File picker needs the app window."}), 400

    import webview
    result = _window.create_file_dialog(
        webview.OPEN_DIALOG,
        allow_multiple=False,
        file_types=("Cookie files (*.txt)", "All files (*.*)"),
    )
    if not result:
        return jsonify({"ok": False, "cancelled": True})

    chosen = result[0] if isinstance(result, (list, tuple)) else result
    saved = engine.save_settings({"cookies_file": str(chosen)})
    return jsonify({"ok": True, "settings": saved})


@app.post("/api/open")
def api_open():
    body = request.json or {}
    target = (body.get("path") or "").strip()
    reveal = bool(body.get("reveal"))

    if not target:
        folder = Path(engine.load_settings()["download_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # noqa: S606 - Windows shell open
        return jsonify({"ok": True})

    path = Path(target)
    if not _inside_downloads(path):
        return jsonify({"ok": False, "error": "That file is outside the download folder."}), 403

    if not path.exists():
        # Moved or deleted since it was downloaded - show the folder instead.
        parent = path.parent
        if not parent.exists() or not _inside_downloads(parent):
            return jsonify({"ok": False, "error": "That file is no longer there."}), 404
        os.startfile(str(parent))  # noqa: S606
        return jsonify({"ok": True, "note": "File missing - opened its folder."})

    if path.is_file() and path.suffix.lower() not in PLAYABLE:
        reveal = True  # never hand an unexpected file type to the shell

    try:
        if reveal and path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # noqa: S606
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True})


@app.post("/api/update-engine")
def api_update_engine():
    return jsonify(engine.update_engine())


@app.get("/api/history")
def api_history():
    return jsonify({"ok": True, "history": engine.load_history()})


@app.post("/api/history/clear")
def api_history_clear():
    engine.clear_history()
    return jsonify({"ok": True})


@app.get("/api/clipboard")
def api_clipboard():
    """
    The pending link is found by a background thread, not by this call, so
    clipboard watching keeps working while the app sits on another tab or
    minimised.
    """
    return jsonify({
        "ok": True,
        "text": read_clipboard(),
        "pending": watcher.pending,
        "autoCount": watcher.auto_count,
        "hotkey": watcher.hotkey_state,
        "hotkeyLabel": watcher.hotkey_label,
        # The window only hides on close when there is a tray icon to get it
        # back from, so the UI needs to know whether one exists.
        "tray": tray_app is not None and tray_app.icon is not None,
    })


@app.post("/api/clipboard/dismiss")
def api_clipboard_dismiss():
    watcher.pending = ""
    return jsonify({"ok": True})


@app.post("/api/show")
def api_show():
    """Raise the window - used by the tray, and by a second copy on startup."""
    show_window()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Clipboard (Win32, no extra dependency)
# --------------------------------------------------------------------------

CF_UNICODETEXT = 13
_clip_lock = threading.Lock()

if os.name == "nt":
    # Declaring these is not optional: handles and pointers are 64-bit, and
    # ctypes defaults every return value to a 32-bit int. Letting it truncate
    # the clipboard handle crashes the whole process with an access violation.
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    _user32.OpenClipboard.restype = ctypes.c_bool
    _user32.CloseClipboard.restype = ctypes.c_bool
    _user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    _user32.IsClipboardFormatAvailable.restype = ctypes.c_bool
    _user32.GetClipboardData.argtypes = [ctypes.c_uint]
    _user32.GetClipboardData.restype = ctypes.c_void_p

    _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    _kernel32.GlobalUnlock.restype = ctypes.c_bool


def read_clipboard() -> str:
    if os.name != "nt":
        return ""

    # Only one caller may hold the clipboard open at a time.
    with _clip_lock:
        if not _user32.OpenClipboard(None):
            return ""
        try:
            if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.c_wchar_p(pointer).value or ""
            finally:
                _kernel32.GlobalUnlock(handle)
        except (OSError, ValueError):
            return ""
        finally:
            _user32.CloseClipboard()


# --------------------------------------------------------------------------
# Clipboard watching and the global shortcut
# --------------------------------------------------------------------------

URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 1

# Any of these combinations may already belong to another program, so try
# them in order and use the first one Windows actually grants us.
HOTKEY_CHOICES = [
    (MOD_CONTROL | MOD_SHIFT, 0x44, "Ctrl + Shift + D"),
    (MOD_CONTROL | MOD_ALT, 0x44, "Ctrl + Alt + D"),
    (MOD_CONTROL | MOD_SHIFT, 0x59, "Ctrl + Shift + Y"),
    (MOD_CONTROL | MOD_ALT, 0x59, "Ctrl + Alt + Y"),
]


class ClipboardWatcher:
    """
    Polls the clipboard on its own thread and, optionally, queues what it
    finds. Also owns the global shortcut, because both do the same job: turn
    a copied link into a download without the user opening the window.
    """

    def __init__(self, download_manager):
        self.manager = download_manager
        self.pending = ""           # link offered to the UI, not yet used
        self.auto_count = 0         # bumped whenever we queue something
        self.hotkey_state = "off"   # off | on | taken
        self.hotkey_label = ""      # the combination we actually got
        self._last_seen = ""
        self._handled = set()

    def start(self):
        threading.Thread(target=self._clip_loop, daemon=True).start()
        threading.Thread(target=self._hotkey_loop, daemon=True).start()

    # -- clipboard -------------------------------------------------------

    def _clip_loop(self):
        while True:
            try:
                self._check()
            except Exception:
                pass          # a watcher must never take the app down
            time.sleep(1.0)

    def _check(self):
        settings = engine.load_settings()
        if not settings.get("auto_paste"):
            self.pending = ""
            return

        text = (read_clipboard() or "").strip()
        if not text or text == self._last_seen:
            return
        self._last_seen = text

        if not URL_RE.match(text) or text in self._handled:
            return

        if settings.get("auto_download"):
            self._queue(text, settings)
        else:
            self.pending = text

    def _queue(self, url, settings=None):
        settings = settings or engine.load_settings()
        self._handled.add(url)
        if len(self._handled) > 200:
            self._handled.clear()

        self.manager.add(url, title=url,
                         quality=settings.get("default_quality", "best"))
        self.auto_count += 1
        self.pending = ""

    # -- global shortcut -------------------------------------------------

    def _hotkey_loop(self):
        if os.name != "nt":
            return
        if not engine.load_settings().get("hotkey", True):
            return

        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                          wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG),
                                       wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int

        for mods, key, label in HOTKEY_CHOICES:
            if user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, key):
                self.hotkey_state = "on"
                self.hotkey_label = label
                break
        else:
            # Every candidate is spoken for by some other program.
            self.hotkey_state = "taken"
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self._on_hotkey()
                except Exception:
                    pass

    def _on_hotkey(self):
        text = (read_clipboard() or "").strip()
        if not text:
            self._announce("Nothing to download", "Copy a video link first.")
            return
        if not URL_RE.match(text):
            self._announce("Not a link", "What you copied is not a web address.")
            return

        # The shortcut is an explicit instruction, so it downloads even when
        # the auto-download setting is off.
        self._handled.discard(text)
        self._queue(text)
        self._announce("Riplox", "Download started.")

    def _announce(self, title, message):
        if tray_app is not None:
            tray_app.notify(title, message)


watcher = ClipboardWatcher(manager)


# --------------------------------------------------------------------------
# One copy at a time
# --------------------------------------------------------------------------

# Closing the window only hides it, so it is easy to launch Riplox again
# believing the first copy is gone. Two copies means two queues, two clipboard
# watchers, two tray icons, and a global shortcut owned by whichever started
# first - which looks exactly like a shortcut that downloads into nowhere.

MUTEX_NAME = "Local\\RiploxDesktop.SingleInstance"
ERROR_ALREADY_EXISTS = 183
_mutex = None


def instance_file() -> Path:
    return engine.data_dir() / "instance.json"


def show_window() -> None:
    """Bring the window back from the tray. Safe to call from any thread."""
    if _window is None:
        return
    try:
        _window.show()
        _window.restore()
    except Exception:
        pass


def claim_single_instance() -> bool:
    """
    True when this is the only copy. Otherwise the copy already running is
    asked to show itself and this one should quit.
    """
    global _mutex
    if os.name != "nt":
        return True

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    # Held for the life of the process; Windows releases it when we exit, even
    # on a crash, so a dead copy never blocks the next start.
    _mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
        return True

    _wake_running_copy()
    return False


def _wake_running_copy() -> None:
    try:
        with open(instance_file(), "r", encoding="utf-8") as fh:
            port = int(json.load(fh)["port"])
    except (OSError, ValueError, TypeError, KeyError):
        return          # no port to talk to; quitting quietly is still right

    try:
        request_ = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/show", data=b"", method="POST")
        urllib.request.urlopen(request_, timeout=2).close()
    except OSError:
        pass


def publish_port(port: int) -> None:
    """Leave the port where the next copy can find it."""
    try:
        with open(instance_file(), "w", encoding="utf-8") as fh:
            json.dump({"port": port, "pid": os.getpid()}, fh)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Boot
# --------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(port: int, host: str = "127.0.0.1") -> None:
    # make_server instead of app.run: no development banner, and nothing is
    # written to stderr in the shipped app.
    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    make_server(host, port, app, threaded=True).serve_forever()


def start_tray(window) -> None:
    """Notification-area icon, notifications and taskbar progress."""
    global tray_app
    import tray

    def show():
        show_window()

    def quit_app():
        try:
            window.destroy()
        except Exception:
            pass
        os._exit(0)

    tray_app = tray.Tray(
        manager,
        icon_path=BASE / "static" / "img" / "riplox.png",
        on_show=show,
        on_quit=quit_app,
    )
    tray_app.start()


def main() -> None:
    global _window
    dev = "--dev" in sys.argv or os.environ.get("RIPLOX_DEV") == "1"

    if not dev and not claim_single_instance():
        return          # the copy already running has been raised instead

    port = 5010 if dev else free_port()

    if dev:
        print(f"Riplox dev server: http://127.0.0.1:{port}")
        watcher.start()
        serve(port)
        return

    threading.Thread(target=serve, args=(port,), daemon=True).start()
    publish_port(port)
    watcher.start()

    try:
        import webview
    except ImportError:
        webbrowser.open(f"http://127.0.0.1:{port}")
        threading.Event().wait()
        return

    _window = webview.create_window(
        APP_TITLE,
        f"http://127.0.0.1:{port}",
        width=1180,
        height=780,
        min_size=(940, 620),
        background_color="#0A101B",
        text_select=False,
    )

    # Closing the window hides it instead of ending the app, so downloads and
    # the global shortcut keep working. Quit lives in the tray menu.
    def on_closing():
        if tray_app is not None and tray_app.icon is not None:
            _window.hide()
            tray_app.notify("Riplox is still running",
                            "Downloads continue. Quit from the tray icon.")
            return False
        return True

    _window.events.closing += on_closing
    webview.start(start_tray, _window)


if __name__ == "__main__":
    main()
