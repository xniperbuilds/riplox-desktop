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

import cookies
import engine
import potoken
import sharing
import watch

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
        # The Library uses these to tell a folder name that is really a site
        # from one that is just where a file happened to land.
        sites=engine.known_sites(),
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


@app.post("/api/command")
def api_command():
    """
    The command Riplox is about to run, in full.

    Built by the queue's own builder rather than a second copy of it, so what
    is shown here is what actually runs. The cookie file is a per-job
    temporary that does not exist yet, so it is named rather than opened.
    """
    body = request.json or {}
    url = (body.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return jsonify({"ok": False, "error": "Paste a link first."}), 400

    settings = engine.load_settings()
    opts = engine.clean_opts(body.get("opts"))
    job = engine.Job(
        url=url,
        quality=body.get("quality") or settings.get("default_quality", "best"),
        start=str(body.get("start") or "")[:12],
        end=str(body.get("end") or "")[:12],
        exact=bool(body.get("exact")),
        opts=opts,
    )

    cookie_path = None
    if not opts.get("no_cookies"):
        chosen = settings.get("cookies_file", "")
        if chosen:
            cookie_path = chosen
        elif settings.get("cookies_signin", True):
            cookie_path = "<this site's saved session>"

    try:
        args = manager.build_args(job, settings, "", cookie_path)
    except engine.EngineMissing:
        return jsonify({"ok": False, "error": "Download engine is missing."}), 500

    quoted = " ".join(f'"{a}"' if (" " in a or not a) else a for a in args)
    return jsonify({"ok": True, "command": quoted})


@app.post("/api/add")
def api_add():
    body = request.json or {}
    quality = body.get("quality") or engine.load_settings().get("default_quality", "best")
    items = body.get("items") or []

    if not items:
        return jsonify({"ok": False, "error": "Nothing to download."}), 400

    # A batch is paced differently: a burst of requests off one playlist is
    # what gets an address asked to prove it is a person.
    batch = len(items) > 1

    # Trimming only makes sense for a single video, and only with ffmpeg.
    start = end = ""
    exact = False
    if not batch and engine.has_ffmpeg():
        start = str(body.get("start") or "")[:12]
        end = str(body.get("end") or "")[:12]
        exact = bool(body.get("exact"))
        if not (engine.valid_time(start) and engine.valid_time(end)):
            return jsonify({"ok": False,
                            "error": "Times should look like 1:30 or 1:02:15."}), 400

    # Whatever "More options" was showing. It belongs to this one download,
    # so it travels with the request and is never written to Settings. A
    # picked format is about one video, so a batch cannot carry one.
    opts = engine.clean_opts(body.get("opts"))
    if batch:
        opts.pop("format_id", None)
        opts.pop("outtmpl", None)

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
            batch=batch,
            start=start,
            end=end,
            exact=exact,
            opts=opts,
        )
        # add() returns the running job for a duplicate, so a set keeps the
        # count honest instead of claiming we queued the same thing twice.
        added.add(job.id)

    if not added:
        return jsonify({"ok": False, "error": "No usable links found."}), 400

    # Said once, as the queue grows, rather than nagging on every screen.
    warning = ""
    room = engine.free_space(engine.load_settings().get("download_dir", ""))
    if 0 <= room < engine.SPACE_WARN:
        warning = f"Only {engine.human_bytes(room)} free on that drive."


    return jsonify({"ok": True, "added": len(added), "warning": warning})


@app.get("/api/jobs")
def api_jobs():
    return jsonify({
        "ok": True,
        "jobs": manager.snapshot(),
        "hasFfmpeg": engine.has_ffmpeg(),
        # Decides whether a failed job is offered a "Fix this" button.
        "hasPotoken": potoken.installed(),
    })


@app.post("/api/job/<action>")
def api_job_action(action):
    job_id = (request.json or {}).get("id", "")
    if action == "cancel":
        return jsonify({"ok": manager.cancel(job_id)})
    if action == "pause":
        return jsonify({"ok": manager.pause(job_id)})
    if action == "retry":
        return jsonify({"ok": manager.retry(job_id)})
    if action == "remove":
        return jsonify({"ok": manager.remove(job_id)})
    return jsonify({"ok": False, "error": "Unknown action."}), 400


@app.post("/api/check-update")
def api_check_update():
    """Ask GitHub whether a newer Riplox exists. Never installs anything."""
    body = request.get_json(silent=True) or {}
    return jsonify(engine.check_for_update(VERSION, force=bool(body.get("force"))))


@app.post("/api/fix-botcheck")
def api_fix_botcheck():
    """
    One button for "Sign in to confirm you're not a bot": fetch the helper if
    it is missing, then start the job again.
    """
    body = request.get_json(silent=True) or {}
    job_id = str(body.get("id", ""))

    if not potoken.installed():
        result = potoken.install()
        if not result.get("ok"):
            return jsonify({"ok": False,
                            "error": result.get("message", "Could not set that up.")})
        engine.save_settings({"potoken": True})

    if job_id and not manager.retry(job_id):
        return jsonify({"ok": False, "error": "That download is no longer waiting."})
    return jsonify({"ok": True})


@app.get("/api/convert/formats")
def api_convert_formats():
    import convert
    return jsonify({
        "ok": True,
        "formats": [{"id": k, "label": v["label"]} for k, v in convert.FORMATS.items()],
        "quality": [{"id": k, "label": v["label"]} for k, v in convert.QUALITY.items()],
    })


@app.get("/api/convert/library")
def api_convert_library():
    """Everything Riplox has downloaded that still exists and has audio in it."""
    import convert
    seen, out = set(), []
    for item in engine.load_history():
        path = item.get("filepath") or ""
        if not path or path in seen:
            continue
        seen.add(path)
        target = Path(path)
        if not target.exists() or target.suffix.lower() not in convert.MEDIA_SUFFIXES:
            continue
        out.append({"path": path, "name": target.name,
                    "size": engine.human_bytes(target.stat().st_size),
                    "source": item.get("quality", "")})
    return jsonify({"ok": True, "files": out[:400]})


@app.post("/api/convert/pick")
def api_convert_pick():
    """Choose files from anywhere on the PC, not only what Riplox downloaded."""
    if _window is None:
        return jsonify({"ok": False, "error": "Picking files needs the app window."}), 400

    import webview
    result = _window.create_file_dialog(
        webview.OPEN_DIALOG, allow_multiple=True,
        file_types=("Video and audio (*.mp4;*.mkv;*.webm;*.mov;*.avi;*.m4v;"
                    "*.mp3;*.m4a;*.opus;*.ogg;*.wav;*.flac;*.aac)",))
    if not result:
        return jsonify({"ok": False, "cancelled": True})

    picked = [{"path": str(p), "name": Path(p).name,
               "size": engine.human_bytes(Path(p).stat().st_size)}
              for p in result if Path(p).exists()]
    return jsonify({"ok": True, "files": picked})


@app.post("/api/convert")
def api_convert():
    import convert
    body = request.get_json(silent=True) or {}
    paths = body.get("paths") or []
    fmt = str(body.get("format", "mp3")).lower()
    quality = str(body.get("quality", "high")).lower()
    beside = bool(body.get("beside", True))

    if not paths:
        return jsonify({"ok": False, "error": "Nothing chosen."}), 400
    if fmt not in convert.FORMATS:
        return jsonify({"ok": False, "error": "Unknown format."}), 400

    target_dir = "" if beside else engine.load_settings().get("download_dir", "")

    added = 0
    for raw in paths[:200]:
        source = Path(str(raw))
        if not source.exists():
            continue
        manager.add_convert(source, fmt, quality, target_dir)
        added += 1

    if not added:
        return jsonify({"ok": False, "error": "None of those files are there."})
    return jsonify({"ok": True, "added": added})


@app.post("/api/settings/export")
def api_settings_export():
    if _window is None:
        return jsonify({"ok": False, "error": "Saving a file needs the app window."}), 400

    import webview
    result = _window.create_file_dialog(
        webview.SAVE_DIALOG, save_filename="riplox-settings.json",
        file_types=("JSON file (*.json)",))
    if not result:
        return jsonify({"ok": False, "cancelled": True})

    chosen = Path(result[0] if isinstance(result, (list, tuple)) else result)
    try:
        chosen.write_text(engine.export_settings(), encoding="utf-8")
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Could not write it: {exc}"})
    return jsonify({"ok": True, "path": str(chosen)})


@app.post("/api/settings/import")
def api_settings_import():
    if _window is None:
        return jsonify({"ok": False, "error": "Opening a file needs the app window."}), 400

    import webview
    result = _window.create_file_dialog(
        webview.OPEN_DIALOG, file_types=("JSON file (*.json)",))
    if not result:
        return jsonify({"ok": False, "cancelled": True})

    chosen = Path(result[0] if isinstance(result, (list, tuple)) else result)
    try:
        outcome = engine.import_settings(chosen.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"Could not read it: {exc}"})
    return jsonify(outcome)


@app.post("/api/export-links")
def api_export_links():
    """Write the library out as a list of links, a spreadsheet, or everything."""
    body = request.get_json(silent=True) or {}
    kind = str(body.get("format", "txt")).lower()
    if kind not in ("txt", "csv", "json"):
        return jsonify({"ok": False, "error": "Unknown format."}), 400

    items = engine.load_history()
    if not items:
        return jsonify({"ok": False, "error": "Nothing downloaded yet."})

    if _window is None:
        return jsonify({"ok": False,
                        "error": "Saving a file needs the app window."}), 400

    import webview
    result = _window.create_file_dialog(
        webview.SAVE_DIALOG,
        save_filename=f"riplox-links.{kind}",
        file_types=(f"{kind.upper()} file (*.{kind})",),
    )
    if not result:
        return jsonify({"ok": False, "cancelled": True})

    chosen = result[0] if isinstance(result, (list, tuple)) else result
    target = Path(chosen)
    try:
        target.write_text(engine.export_links(items, kind), encoding="utf-8")
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Could not write it: {exc}"})

    return jsonify({"ok": True, "count": len(items), "path": str(target)})


@app.post("/api/resume-all")
def api_resume_all():
    """Start the downloads that were still waiting when Riplox last closed."""
    return jsonify({"ok": True, "resumed": manager.resume_all()})


@app.post("/api/job-log")
def api_job_log():
    """The raw engine output for one job - for reporting a problem."""
    job_id = (request.json or {}).get("id", "")
    return jsonify({"ok": True, "log": manager.job_log(job_id)})


@app.post("/api/clear-finished")
def api_clear_finished():
    manager.clear_finished()
    return jsonify({"ok": True})


@app.get("/api/settings")
def api_get_settings():
    return jsonify({"ok": True, "settings": engine.load_settings(),
                    "engineVersion": engine.engine_version(),
                    "environment": engine.environment(),
                    "hasFfmpeg": engine.has_ffmpeg()})


# --------------------------------------------------------------------------
# Browser sign-in
# --------------------------------------------------------------------------

@app.get("/api/cookies/status")
def api_cookies_status():
    return jsonify({"ok": True, "cookies": cookies.status()})


@app.post("/api/cookies/signin")
def api_cookies_signin():
    url = (request.json or {}).get("url") or "https://www.youtube.com/"
    if not str(url).lower().startswith("https://"):
        return jsonify({"ok": False, "error": "Blocked."}), 400
    return jsonify(cookies.start_login(url))


@app.post("/api/cookies/refresh")
def api_cookies_refresh():
    return jsonify(cookies.refresh())


@app.post("/api/cookies/forget")
def api_cookies_forget():
    cookies.forget()
    return jsonify({"ok": True, "cookies": cookies.status()})


# --------------------------------------------------------------------------
# Proof-of-origin helper
# --------------------------------------------------------------------------

@app.get("/api/potoken/status")
def api_potoken_status():
    return jsonify({"ok": True, "potoken": potoken.status()})


@app.post("/api/potoken/install")
def api_potoken_install():
    return jsonify(potoken.install())


@app.post("/api/potoken/remove")
def api_potoken_remove():
    engine.save_settings({"potoken": False})
    return jsonify(potoken.remove())


@app.post("/api/settings")
def api_set_settings():
    patch = request.json or {}
    saved = engine.save_settings(patch)
    # Turning Sharing on or off has to take effect now, not at the next start.
    if "sharing" in patch or "share_lan_only" in patch or "share_relay" in patch:
        sharing.apply_setting(bool(saved.get("sharing")))
    if "watch" in patch:
        watch.apply_setting(bool(saved.get("watch")))
    return jsonify({"ok": True, "settings": saved})


# --------------------------------------------------------------------------
# Watching a channel or playlist
# --------------------------------------------------------------------------

@app.post("/api/watch/state")
def api_watch_state():
    return jsonify({"ok": True, "state": watch.state()})


@app.post("/api/watch/add")
def api_watch_add():
    body = request.json or {}
    try:
        result = watch.add(str(body.get("url", ""))[:2000],
                           str(body.get("kind", ""))[:12])
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:200]}), 500
    return jsonify({"ok": True, "result": result, "state": watch.state()})


@app.post("/api/watch/remove")
def api_watch_remove():
    body = request.json or {}
    return jsonify({"ok": watch.remove(str(body.get("id", ""))[:24]),
                    "state": watch.state()})


@app.post("/api/watch/pause")
def api_watch_pause():
    body = request.json or {}
    done = watch.set_paused(str(body.get("id", ""))[:24], bool(body.get("paused")))
    return jsonify({"ok": done, "state": watch.state()})


@app.post("/api/watch/seen")
def api_watch_seen():
    body = request.json or {}
    done = watch.clear_new(str(body.get("id", ""))[:24],
                           str(body.get("video", ""))[:64])
    return jsonify({"ok": done, "state": watch.state()})


@app.post("/api/watch/check")
def api_watch_check():
    """One item, or everything. Either way it runs off the request thread."""
    body = request.json or {}
    item_id = str(body.get("id", ""))[:24]
    if item_id:
        result = watch.check(item_id)
    else:
        result = watch.check_all()
    return jsonify({"ok": bool(result.get("ok")),
                    "error": result.get("error", ""),
                    "new": result.get("new", 0),
                    "state": watch.state()})


# --------------------------------------------------------------------------
# Send to Riplox
# --------------------------------------------------------------------------

def _share_sink(url: str, quality: str, who: str, opts=None, device="") -> None:
    """A link that arrived from a paired device. Queued like any other."""
    job = manager.add(url=url, title=url, quality=quality, opts=opts or {},
                      origin=device)
    if tray_app is not None:
        tray_app.notify("Sent from " + (who or "your phone"), "Download started.")
    return job


sharing.set_sink(_share_sink)


@app.post("/api/share/state")
def api_share_state():
    return jsonify({"ok": True, "state": sharing.state()})


@app.post("/api/share/invite")
def api_share_invite():
    if not engine.load_settings().get("sharing"):
        return jsonify({"ok": False, "error": "Turn Sharing on first."}), 400
    if not sharing.crypto_available():
        return jsonify({"ok": False,
                        "error": "This build cannot encrypt. Reinstall Riplox."}), 500
    try:
        return jsonify({"ok": True, "invite": sharing.make_invite(
            (request.json or {}).get("name", ""))})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)[:140]}), 500


@app.post("/api/share/revoke")
def api_share_revoke():
    device = str((request.json or {}).get("id", ""))[:24]
    return jsonify({"ok": sharing.revoke(device), "state": sharing.state()})


@app.post("/api/share/revoke-all")
def api_share_revoke_all():
    return jsonify({"ok": True, "gone": sharing.revoke_all(),
                    "state": sharing.state()})


@app.post("/api/share/pause")
def api_share_pause():
    body = request.json or {}
    done = sharing.set_paused(str(body.get("id", ""))[:24], bool(body.get("paused")))
    return jsonify({"ok": done, "state": sharing.state()})


@app.post("/api/share/rename")
def api_share_rename():
    body = request.json or {}
    done = sharing.rename(str(body.get("id", ""))[:24], body.get("name", ""))
    return jsonify({"ok": done, "state": sharing.state()})


@app.post("/api/share/limits")
def api_share_limits():
    body = request.json or {}
    done = sharing.set_limits(str(body.get("id", ""))[:24],
                              body.get("limits") or {})
    return jsonify({"ok": done, "state": sharing.state()})


@app.post("/api/share/approve")
def api_share_approve():
    body = request.json or {}
    done = sharing.approve(str(body.get("id", ""))[:24], bool(body.get("ok")))
    return jsonify({"ok": done, "state": sharing.state()})


@app.post("/api/share/clear")
def api_share_clear():
    sharing.clear_log()
    return jsonify({"ok": True, "state": sharing.state()})


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


@app.post("/api/choose-folder-once")
def api_choose_folder_once():
    """
    The same picker, for a folder that applies to one download only.

    Deliberately does not save: "More options" must never change a setting,
    or a stray click there would quietly redirect every future download.
    """
    if _window is None:
        return jsonify({"ok": False, "error": "Folder picker needs the app window."}), 400

    import webview
    result = _window.create_file_dialog(
        webview.FOLDER_DIALOG, directory=engine.load_settings()["download_dir"])
    if not result:
        return jsonify({"ok": False, "cancelled": True})

    chosen = result[0] if isinstance(result, (list, tuple)) else result
    return jsonify({"ok": True, "dir": str(chosen)})



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


@app.post("/api/open-url")
def api_open_url():
    """
    Open a page in the real browser. Restricted to a short allowlist, so a
    page that talked its way past the token could not use Riplox to launch
    arbitrary links.
    """
    url = ((request.json or {}).get("url") or "").strip()
    if url not in engine.OPENABLE:
        return jsonify({"ok": False, "error": "Not a link Riplox opens."}), 400
    webbrowser.open(url)
    return jsonify({"ok": True})


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
    channel = (request.json or {}).get("channel", "")
    return jsonify(engine.update_engine(channel))


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


def _raise_existing_window() -> bool:
    """
    Bring the running copy's window up by finding it, not by asking it.

    This is the first thing tried because it depends on nothing: no file, no
    port, no HTTP. Found in real use - instance.json on this machine was a day
    older than the running app, so the second copy had a port that answered
    nothing and quit in silence, which looks exactly like Riplox refusing to
    open.
    """
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)             # SW_RESTORE, undoes minimised
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _wake_running_copy() -> None:
    if _raise_existing_window():
        return

    # Hidden to the tray, so there is no window to find. Ask it over its own
    # API instead - which is what the port file is for.
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
    """
    Leave the port where the next copy can find it.

    Written to a temporary file and moved into place, the same way settings
    and the queue are: a half-written line here would be read as no port at
    all by the next copy that starts.
    """
    path = instance_file()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"port": port, "pid": os.getpid()}, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
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
        shutdown_children()
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


def shutdown_children() -> None:
    """
    Nothing Riplox started may outlive it. A stray helper server or a headless
    browser left running is the kind of thing users only notice as a machine
    that never quite goes idle.
    """
    try:
        potoken.stop()
    except Exception:
        pass
    try:
        cookies.cancel()
    except Exception:
        pass
    try:
        sharing.stop()
    except Exception:
        pass
    try:
        watch.stop()
    except Exception:
        pass


def main() -> None:
    global _window
    dev = "--dev" in sys.argv or os.environ.get("RIPLOX_DEV") == "1"

    if not dev and not claim_single_instance():
        return          # the copy already running has been raised instead

    # A crash can leave a decrypted cookie file or a helper server behind.
    cookies.sweep_temp()
    potoken.kill_orphans()
    manager.restore()

    # A phone that was paired yesterday should not have to be told again.
    if engine.load_settings().get("sharing"):
        sharing.start()
    if engine.load_settings().get("watch"):
        watch.start()

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
