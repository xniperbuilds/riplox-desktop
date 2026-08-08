"""
Riplox Desktop - application shell.

A Flask server bound to localhost renders the UI, and pywebview wraps it in a
native window. Run with --dev to skip the window and open it in a browser
instead (useful while working on the interface).
"""

import ctypes
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import webbrowser
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

    added = []
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
        added.append(job.id)

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
    return jsonify({"ok": True, "text": read_clipboard()})


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


def main() -> None:
    global _window
    dev = "--dev" in sys.argv or os.environ.get("RIPLOX_DEV") == "1"
    port = 5010 if dev else free_port()

    if dev:
        print(f"Riplox dev server: http://127.0.0.1:{port}")
        serve(port)
        return

    threading.Thread(target=serve, args=(port,), daemon=True).start()

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
    webview.start()


if __name__ == "__main__":
    main()
