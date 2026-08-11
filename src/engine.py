"""
Riplox Desktop - download engine.

Wraps the yt-dlp executable in a subprocess instead of importing the library.
That is deliberate: the bundled binary lives in a user-writable folder, so the
app can update its own extractors ("Update engine") when a site changes its
layout. A frozen library copy would go stale within weeks.
"""

import csv
import ctypes
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

APP_NAME = "RiploxDesktop"

# Hide the console window every time we shell out on Windows.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def bundle_roots() -> list:
    """
    Every folder a shipped binary might sit in.

    PyInstaller puts bundled data under _internal, but an installer may also
    drop files beside the exe, and in development they live in the repo. Check
    all three rather than guessing which build produced this copy.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
        roots.append(Path(sys.executable).parent)
    else:
        roots.append(Path(__file__).resolve().parent.parent)
    return roots


# --------------------------------------------------------------------------
# Child processes that must not outlive us
# --------------------------------------------------------------------------
# Stopping cleanly is not enough: a crash, a kill from Task Manager, or a
# development run ended from outside all skip every shutdown handler, and the
# helper server is left running with nothing to stop it. A Windows job object
# is the only thing that holds in all of those cases - the kernel terminates
# the children when the last handle to the job closes, which happens when this
# process dies however it dies.

_JOB_HANDLE = None
_JOB_KILL_ON_CLOSE = 0x2000
_JOB_EXTENDED_LIMIT_INFO = 9


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOperationCount", "WriteOperationCount",
                 "OtherOperationCount", "ReadTransferCount",
                 "WriteTransferCount", "OtherTransferCount")]


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _job_handle():
    global _JOB_HANDLE
    if _JOB_HANDLE is not None or os.name != "nt":
        return _JOB_HANDLE

    kernel32 = ctypes.windll.kernel32
    # Handles are pointer-sized; letting ctypes default them to 32-bit ints
    # truncates and has crashed this app before.
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None

    limits = _JobExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE
    if not kernel32.SetInformationJobObject(
            handle, _JOB_EXTENDED_LIMIT_INFO,
            ctypes.byref(limits), ctypes.sizeof(limits)):
        return None

    _JOB_HANDLE = handle
    return _JOB_HANDLE


def tie_to_app(proc) -> None:
    """Make Windows kill this child whenever Riplox stops existing."""
    if os.name != "nt" or proc is None:
        return
    job = _job_handle()
    if not job:
        return

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    target = kernel32.OpenProcess(0x0100 | 0x0001, False, proc.pid)
    if not target:
        return
    try:
        kernel32.AssignProcessToJobObject(job, target)
    finally:
        kernel32.CloseHandle(target)


def data_dir() -> Path:
    """User-writable folder for settings, history and the live binaries."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def bin_dir() -> Path:
    d = data_dir() / "bin"
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_space(path) -> int:
    """Bytes still available on whatever drive this path lives on."""
    try:
        target = Path(path)
        while not target.exists() and target.parent != target:
            target = target.parent
        return shutil.disk_usage(target).free
    except (OSError, ValueError):
        return -1          # unknown, which must never be treated as "full"


# Refuse to start a job below this, rather than half-writing a file and
# handing the user whatever confusing thing yt-dlp says when the disk fills.
SPACE_FLOOR = 500 * 1024 * 1024
# Warn at queue time below this. Not a refusal - plenty of downloads are small.
SPACE_WARN = 2 * 1024 * 1024 * 1024


def default_download_dir() -> Path:
    return Path(os.path.expanduser("~")) / "Downloads" / "Riplox"


def _shipped(name: str) -> Path | None:
    """Find a binary that came with the app, wherever this build put it."""
    for root in bundle_roots():
        candidate = root / "bin" / name
        if candidate.exists():
            return candidate
    return None


# Where the folder build comes from. Two repositories, because the nightly
# builds live in one of their own.
_YTDLP_ZIP = {
    "stable": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_win.zip",
    "nightly": ("https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/"
                "latest/download/yt-dlp_win.zip"),
}

# The folder build's executable is around 8 MB because its Python lives beside
# it in _internal. The single-file build carries all of that inside itself and
# is over 17. Anything that size sitting in the folder is the single file that
# yt-dlp's own updater put there - see update_engine().
_ONEFILE_SIZE = 12 * 1024 * 1024


def _newer(source: Path, target: Path) -> bool:
    try:
        return not target.exists() or source.stat().st_mtime > target.stat().st_mtime
    except OSError:
        return False


def _is_onefile(exe: Path) -> bool:
    try:
        return exe.stat().st_size > _ONEFILE_SIZE
    except OSError:
        return False


def _swap_in(source: Path, live_dir: Path) -> bool:
    """
    Put a freshly unpacked engine in place, whole or not at all.

    A running yt-dlp holds its own files open, and a half-replaced folder - a
    new _internal beside an old exe - is a broken engine whose failure turns
    up long after the thing that caused it. If any step refuses, whatever was
    already there is untouched and still works.
    """
    staged = live_dir.with_name(live_dir.name + ".new")
    try:
        shutil.rmtree(staged, ignore_errors=True)
        shutil.copytree(source, staged)
        if live_dir.exists():
            shutil.rmtree(live_dir)
        staged.rename(live_dir)
        return True
    except OSError:
        shutil.rmtree(staged, ignore_errors=True)
        return False


def ytdlp_path() -> Path | None:
    """
    yt-dlp runs from a writable copy in LOCALAPPDATA so that "Update engine"
    works without admin rights. Program Files is read-only for normal users.

    It ships as a folder - yt-dlp.exe beside its _internal - rather than the
    single-file build, because the single-file build unpacks itself into a
    temp directory on every single run. Measured on this machine: 2.2s before
    one request had gone out, against 0.77s for the folder. Riplox starts
    yt-dlp for every paste, every job and every watch check, so that is 1.4s
    off each of them. yt-dlp's own --update-to handles this layout, which is
    the only reason it is an option at all.
    """
    name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"

    live_dir = bin_dir() / "ytdlp"
    live = live_dir / name
    shipped_dir = None
    for root in bundle_roots():
        candidate = root / "bin" / "ytdlp" / name
        if candidate.exists():
            shipped_dir = candidate.parent
            break

    # Repaired as well as updated. yt-dlp's own updater does not understand
    # this layout: asked to update, it drops the single-file build into the
    # folder and leaves _internal sitting there unused - which works, but
    # costs back every bit of the start-up time the folder was chosen for.
    # update_engine() no longer calls it, and this puts right the copies that
    # already went through it.
    if shipped_dir is not None and (_newer(shipped_dir / name, live)
                                    or _is_onefile(live)):
        _swap_in(shipped_dir, live_dir)

    if live.exists():
        # An install from before the folder build left a 17 MB copy behind.
        legacy = bin_dir() / name
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass
        return live
    if shipped_dir is not None:
        return shipped_dir / name

    # Older layout: one file, straight in bin. Kept so that an install which
    # cannot copy the folder for any reason still has a working engine.
    flat_live = bin_dir() / name
    flat_shipped = _shipped(name)
    if flat_shipped is not None and _newer(flat_shipped, flat_live):
        try:
            shutil.copy2(flat_shipped, flat_live)
        except OSError:
            pass
    if flat_live.exists():
        return flat_live
    if flat_shipped is not None:
        return flat_shipped

    found = shutil.which("yt-dlp")
    return Path(found) if found else None


def qjs_path() -> Path | None:
    """
    The JavaScript runtime yt-dlp needs for YouTube's n-challenge.

    Since yt-dlp 2025.11.12 an external JS runtime is required for full YouTube
    support, and when YouTube forces SABR streaming on a client without one the
    result is not a warning but a dead end - formats arrive with no URL. Almost
    no user has Deno or Node installed, so a 2 MB QuickJS build ships with the
    app. A user who does have Deno keeps using it: yt-dlp ranks it higher.
    """
    name = "qjs.exe" if os.name == "nt" else "qjs"
    shipped = _shipped(name)
    if shipped is not None:
        return shipped
    live = bin_dir() / name
    if live.exists():
        return live
    found = shutil.which(name)
    return Path(found) if found else None


def ffmpeg_path() -> Path | None:
    """
    ffmpeg stays where it shipped. This is a shared build - the exe needs its
    DLLs beside it, so copying the exe alone anywhere else would break it.
    """
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    shipped = _shipped(name)
    if shipped is not None:
        return shipped
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def has_ffmpeg() -> bool:
    return ffmpeg_path() is not None


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

# Where a paired phone and this PC meet when they are not on the same network.
# A setting rather than a constant so a relay outage is survivable: point it
# somewhere else and the feature keeps working.
#
# On the brand's own domain rather than the generated workers.dev subdomain,
# because that subdomain carries the account owner's email address - and this
# address is printed into a QR code and handed to every user.
DEFAULT_RELAY = "https://relay.xniperbuilds.com"

# Addresses that were never real. Replaced on load so nobody is left with a
# setting that can only ever fail.
DEAD_RELAYS = ("wss://relay.riplox.workers.dev", "")

# Every default below is what someone who never opens Settings gets, so each
# one is an answer to "what goes wrong for that person". The reasons are
# written down beside them - a default with no reason attached drifts.
DEFAULT_SETTINGS = {
    "download_dir": str(default_download_dir()),
    "default_quality": "best",
    # Two at once, not four: a home line shared between four downloads makes
    # all four slow and none of them finish, and YouTube notices the fifth.
    "max_parallel": 2,
    "cookies_browser": "none",       # none | firefox | chrome | edge | brave
    "cookies_file": "",              # the old single path, folded in on read
    "cookies_files": [],             # exported cookies.txt files, one per site
    # Off. A download that silently waits until 1am looks like a broken app,
    # and nobody who has not been to this screen is expecting it.
    "schedule_on": False,
    "schedule_from": "01:00",
    "schedule_to": "08:00",
    # On: the session was captured because someone signed in, so use it. It is
    # already per-site, so a YouTube login never travels to another site.
    "cookies_signin": True,
    "engine_channel": "stable",      # stable | nightly
    "potoken": False,                # opt-in: fetch the proof-of-origin helper
    # On: pacing costs a second or two and is the difference between YouTube
    # answering and YouTube asking to confirm you are not a bot.
    "polite_mode": True,
    # On: H.264 plays in Windows' own player and on a phone. AV1 is smaller and
    # sharper and opens to a black screen, which is the bug that was reported.
    "prefer_h264": True,
    "allow_ai_upscale": False,       # take YouTube's AI-enlarged versions too
    "write_subs": False,             # save subtitles alongside the video
    "sub_langs": "en",               # which languages, yt-dlp syntax
    "embed_subs": False,             # put them inside the file instead
    "embed_chapters": False,         # chapter marks players can jump between
    "sponsorblock": False,           # cut sponsor segments out of YouTube
    "skip_existing": False,          # remember what has been downloaded
    # Four pieces of the same file at once. This is where the real speed comes
    # from on fragmented video, and it is also why aria2c was not needed.
    # Higher looks faster on a fast line and starts being refused on a slow one.
    "fragments": 4,
    "speed_limit": 0,                # KB/s ceiling; 0 means no limit
    "check_updates": True,           # ask GitHub once a day if there is a newer build
    # Remembered so the daily check is actually daily. save_settings drops any
    # key it does not know, so these have to be declared or the throttle never
    # persists and GitHub gets asked on every single start.
    "_update_checked": 0,
    "_update_latest": "",
    # The same throttle for the engine itself, kept apart from the app's own:
    # they are different projects on different release schedules.
    "engine_checked": 0,
    "engine_latest": "",
    # On: a YouTube folder and a TikTok folder beats two hundred files in one
    # Downloads folder by the second week. Nothing is moved retrospectively.
    "subfolder_per_site": True,
    "auto_paste": True,              # watch clipboard for links
    "auto_download": False,          # queue a copied link without asking
    "hotkey": True,                  # Ctrl+Shift+D from anywhere
    "write_thumbnail": False,
    "theme": "auto",                 # auto | light | dark

    # --- Watching for new videos ----------------------------------------
    "watch": False,                  # master switch for the checks
    # Not a preference - a record that the warning about repeated automated
    # requests was read. The screen shows it until this is true.
    "watch_ack": False,
    "watch_hours": 12,               # how often one item may be checked

    # --- Send to Riplox -------------------------------------------------
    "sharing": False,                # master switch for the phone channel
    "share_lan_only": False,         # refuse the relay; home network only
    "share_approve": False,          # hold every incoming link for Approve
    "share_relay": DEFAULT_RELAY,    # which relay to dial
    # Keys and paired devices live in their own file, never in settings.json,
    # so a settings backup can never carry someone's pairing to another PC.
}

_settings_lock = threading.Lock()


def settings_file() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(settings_file(), "r", encoding="utf-8") as fh:
            s.update(json.load(fh))
    except (OSError, ValueError):
        pass
    # Never trust a stale path from a previous machine.
    try:
        Path(s["download_dir"]).mkdir(parents=True, exist_ok=True)
    except OSError:
        s["download_dir"] = str(default_download_dir())
        Path(s["download_dir"]).mkdir(parents=True, exist_ok=True)

    # A relay address that was only ever a placeholder is worse than none: it
    # would sit there failing to connect forever. Anything the user typed
    # themselves is left exactly as it is.
    if s.get("share_relay") in DEAD_RELAYS:
        s["share_relay"] = DEFAULT_RELAY
    return s


def save_settings(patch: dict) -> dict:
    with _settings_lock:
        s = load_settings()
        for key, value in patch.items():
            if key in DEFAULT_SETTINGS:
                s[key] = value

        # A temp file named after this process, because a shared "settings.tmp"
        # is a collision waiting to happen - a second copy of the app, or a
        # virus scanner holding the file open, turns the rename into
        # "Access is denied" and the save is lost.
        tmp = settings_file().with_suffix(f".{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(s, fh, indent=2)

            for attempt in range(5):
                try:
                    tmp.replace(settings_file())
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.1)      # whoever holds it is about to let go
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return s


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------

_history_lock = threading.Lock()
HISTORY_LIMIT = 300


def history_file() -> Path:
    return data_dir() / "history.json"


def load_history() -> list:
    try:
        with open(history_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def add_history(entry: dict) -> None:
    with _history_lock:
        items = load_history()
        items.insert(0, entry)
        del items[HISTORY_LIMIT:]
        tmp = history_file().with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(items, fh, indent=2)
        tmp.replace(history_file())


def clear_history() -> None:
    with _history_lock:
        try:
            history_file().unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# yt-dlp invocation
# --------------------------------------------------------------------------

class EngineMissing(RuntimeError):
    pass


def cookie_files(settings: dict) -> list:
    """
    Every cookies.txt the user has added, in order, skipping ones that moved.

    A single path used to live in `cookies_file`; it is folded in here so an
    upgrade keeps working without asking anyone to pick their file again.
    """
    settings = settings or {}
    raw = list(settings.get("cookies_files") or [])
    one = settings.get("cookies_file", "")
    if one and one not in raw:
        raw.insert(0, one)
    return [p for p in raw if p and Path(p).exists()]


def open_cookies(settings: dict, url: str):
    """
    Choose a cookie source for this URL. Returns (path or None, temporary).

    A file the user picked themselves always wins. Otherwise the session
    captured by the built-in sign-in is written out - but only the cookies
    belonging to this site, and only into a temp file the caller deletes.
    """
    files = cookie_files(settings)
    if files:
        try:
            import cookies as cookie_store
            path = cookie_store.from_files(files, url)
        except Exception:
            path = None            # never let cookie trouble block a download
        if path:
            return path, True
        # Files are set but none of them knows this site: fall through to the
        # sign-in rather than sending nothing, which is what a user who has
        # both would expect.

    if (settings or {}).get("cookies_signin", True):
        try:
            import cookies as cookie_store
            path = cookie_store.materialize(url)
        except Exception:
            path = None            # never let cookie trouble block a download
        if path:
            return path, True

    return None, False


def close_cookies(path, temporary: bool) -> None:
    if not (path and temporary):
        return
    try:
        import cookies as cookie_store
        cookie_store.release(path)
    except Exception:
        pass


def _base_args(settings: dict, cookie_path=None, batch: bool = False) -> list:
    exe = ytdlp_path()
    if exe is None:
        raise EngineMissing("yt-dlp binary not found")

    args = [str(exe), "--no-warnings", "--ignore-config", "--no-colors"]

    ff = ffmpeg_path()
    if ff is not None:
        args += ["--ffmpeg-location", str(ff.parent)]

    # Without a JavaScript runtime YouTube's newer streaming path hands back
    # formats with no URL at all, so this is not optional any more.
    qjs = qjs_path()
    if qjs is not None:
        args += ["--js-runtimes", f"quickjs:{qjs}"]

    if cookie_path is not None:
        args += ["--cookies", str(cookie_path)]
    else:
        # Chromium browsers have locked their cookie store since Chrome 127,
        # so this only ever succeeds for Firefox.
        browser = (settings or {}).get("cookies_browser", "none")
        if browser and browser != "none":
            args += ["--cookies-from-browser", browser]

    if (settings or {}).get("potoken"):
        try:
            import potoken
            base = potoken.ensure_running()
            if base:
                args += [
                    "--plugin-dirs", str(potoken.plugin_dir()),
                    "--extractor-args", f"youtubepot-bgutilhttp:base_url={base}",
                ]
        except Exception:
            pass               # a missing helper must never stop a download

    if (settings or {}).get("polite_mode", True):
        # Bulk grabs off a playlist are exactly the burst pattern that gets an
        # IP asked to prove it is a person. A short pause costs nothing.
        args += ["--sleep-requests", "0.75"]
        if batch:
            args += ["--sleep-interval", "1", "--max-sleep-interval", "4"]

    return args


# Chromium-based browsers encrypt cookies so only the browser itself can read
# them (Chrome 127+, July 2024). Firefox does not.
LOCKED_BROWSERS = {"chrome", "edge", "brave", "opera", "vivaldi", "chromium"}


def _kill_tree(proc) -> None:
    """
    Stop a download and everything it started.

    yt-dlp is a frozen executable and re-launches itself as a child process.
    Killing only the one we spawned leaves that child running, still holding
    the output pipe we are reading - so the download carried on, the row stayed
    on "downloading", and Stop appeared to do nothing for ten seconds. Windows
    does not kill descendants for you; taskkill /T does.
    """
    if os.name != "nt":
        try:
            proc.terminate()
        except OSError:
            pass
        return

    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.terminate()      # better than nothing if taskkill is missing
        except OSError:
            pass


def _run(args: list, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


# Asking yt-dlp its version means starting yt-dlp, which is a bundled
# executable and takes a second or two. It was being asked on every finished
# job, for a string that only changes when the engine is updated.
_version_cache = {"exe": None, "stamp": 0.0, "value": ""}


def engine_version() -> str:
    exe = ytdlp_path()
    if exe is None:
        return "missing"

    try:
        stamp = exe.stat().st_mtime
    except OSError:
        stamp = 0.0
    if _version_cache["exe"] == str(exe) and _version_cache["stamp"] == stamp:
        return _version_cache["value"]

    try:
        out = _run([str(exe), "--version"], timeout=30)
        value = (out.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"

    _version_cache.update({"exe": str(exe), "stamp": stamp, "value": value})
    return value


# --------------------------------------------------------------------------
# Fetching the engine
# --------------------------------------------------------------------------
# Measured rather than guessed: the zip came down at 0.10 MB/s and the
# connection was dropped at 15.9 MB of 18.4 after 161 seconds. So it was never
# hanging - it is an 18 MB file on a slow line, on a button that said nothing
# for minutes and then failed. Three answers, in the order they matter:
# chunks with a percentage anyone can watch, a .part file the next attempt
# continues instead of starting the 18 MB again, and timeouts that say what
# happened rather than a button that goes quiet.

_ENGINE_CHUNK = 256 * 1024
_ENGINE_READ_TIMEOUT = 30            # per read, not for the whole download
_ENGINE_TOTAL_CAP = 10 * 60          # the whole job, across every attempt
_ENGINE_ATTEMPTS = 3

_engine_dl = {"busy": False, "percent": 0.0, "bytes": 0, "total": 0,
              "message": "", "done": False, "ok": None}
_engine_dl_lock = threading.Lock()


def engine_progress() -> dict:
    """What the update button is doing right now. Read by /api/engine-progress."""
    with _engine_dl_lock:
        return dict(_engine_dl)


def _engine_say(**fields) -> None:
    with _engine_dl_lock:
        _engine_dl.update(fields)


def _download_engine_zip(url: str, part: Path, deadline: float) -> int:
    """
    One attempt at the zip, continuing a .part file if one is already there.

    Raises OSError with a message meant to be read by a person. The .part is
    left behind on purpose - the next attempt sends a Range header and carries
    on from that byte rather than starting again.
    """
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "Riplox"}
    if have:
        headers["Range"] = f"bytes={have}-"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=_ENGINE_READ_TIMEOUT) as response:
        # A server that ignores Range answers 200 with the whole file. Appending
        # to what is already there would quietly corrupt the zip, so start over.
        if have and getattr(response, "status", 200) != 206:
            have = 0
            part.unlink(missing_ok=True)

        length = int(response.headers.get("Content-Length") or 0)
        total = have + length if length else 0
        _engine_say(total=total)

        with open(part, "ab" if have else "wb") as out:
            while True:
                if time.monotonic() > deadline:
                    raise OSError(
                        "Gave up after ten minutes - the connection is too slow "
                        "or keeps dropping. What arrived is kept, so pressing "
                        "update again carries on from there.")
                chunk = response.read(_ENGINE_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                have += len(chunk)
                percent = (have / total * 100.0) if total else 0.0
                _engine_say(bytes=have, percent=round(percent, 1),
                            message=(f"Downloading {percent:.0f}%" if total
                                     else f"Downloading {human_bytes(have)}"))

    if total and have < total:
        raise OSError("The connection dropped part-way through.")
    return have


def update_engine(channel: str = "") -> dict:
    """
    Fetch a new engine and put it in place.

    Not `yt-dlp --update-to`, which is what this used to do. yt-dlp does not
    recognise the folder layout as one of its own: asked to update, it writes
    the single-file build over the executable and leaves _internal sitting
    there unused. That still runs - and starts nearly three times slower,
    which is the entire reason the folder build was chosen. The cost came back
    silently, the first time anyone pressed this button.

    Fetching the same zip the installer ships is the fix and is no harder. It
    lands in LOCALAPPDATA, so it still needs no administrator rights.

    The nightly channel matters here: when YouTube changes something, the fix
    lands in nightly first and in a stable release weeks later.
    """
    exe = ytdlp_path()
    if exe is None:
        return {"ok": False, "message": "Engine not installed."}

    channel = (channel or load_settings().get("engine_channel", "stable")).lower()
    if channel not in _YTDLP_ZIP:
        channel = "stable"

    # An install from before the folder build has a single file, and yt-dlp
    # updates one of those perfectly well.
    if exe.parent.name != "ytdlp":
        try:
            out = _run([str(exe), "--update-to", channel], timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "message": str(exc)}
        text = ((out.stdout or "") + (out.stderr or "")).strip()
        latest = "is up to date" in text.lower()
        updated = "updated" in text.lower() or "installing" in text.lower()
        return {
            "ok": latest or updated or out.returncode == 0,
            "message": text.splitlines()[-1] if text else "Done.",
            "version": engine_version(),
        }

    before = engine_version()
    unpacked = data_dir() / "engine.tmp"
    part = data_dir() / "engine.zip.part"
    deadline = time.monotonic() + _ENGINE_TOTAL_CAP
    _engine_say(busy=True, percent=0.0, bytes=0, total=0, done=False, ok=None,
                message="Starting")

    def finish(ok: bool, message: str) -> dict:
        _engine_say(busy=False, done=True, ok=ok, message=message)
        return {"ok": ok, "message": message, "version": engine_version()}

    failure = ""
    for attempt in range(1, _ENGINE_ATTEMPTS + 1):
        try:
            _download_engine_zip(_YTDLP_ZIP[channel], part, deadline)
            failure = ""
            break
        except Exception as exc:                 # offline, refused, dropped, slow
            failure = str(exc)[:200] or "That download did not finish."
            if attempt >= _ENGINE_ATTEMPTS or time.monotonic() > deadline:
                break
            pause = 3 * attempt
            _engine_say(message=f"Lost the connection - trying again in {pause}s")
            time.sleep(pause)

    if failure:
        return finish(False, failure)

    _engine_say(percent=100.0, message="Unpacking")
    try:
        shutil.rmtree(unpacked, ignore_errors=True)
        zipfile.ZipFile(part).extractall(unpacked)

        if not (unpacked / exe.name).exists():
            part.unlink(missing_ok=True)
            return finish(False, "That download had no engine in it.")
        if not _swap_in(unpacked, exe.parent):
            return finish(False, "Could not replace the engine. Stop any "
                                 "running download and try again.")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        # Half a zip resumes; a broken one only wastes the next attempt too.
        part.unlink(missing_ok=True)
        return finish(False, str(exc)[:200])
    finally:
        shutil.rmtree(unpacked, ignore_errors=True)

    part.unlink(missing_ok=True)
    after = engine_version()
    save_settings({"engine_checked": time.time(), "engine_latest": after})
    return finish(True, "Already on the newest engine." if after == before
                  else f"Updated to {after}.")


# --------------------------------------------------------------------------
# Start with Windows
# --------------------------------------------------------------------------
# HKCU, never HKLM: per-user, no administrator, and gone the moment the toggle
# goes off. It starts into the tray rather than onto the screen - an app that
# takes over the display at every login is an app that gets uninstalled.

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "Riplox"


def _autostart_command() -> str:
    """The exact line Windows would run. Quoted - the path has spaces in it."""
    return f'"{Path(sys.executable).resolve()}" --tray'


def autostart_on() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_NAME)
        return bool(value)
    except (OSError, ImportError):
        return False


def set_autostart(on: bool) -> dict:
    """
    Add or remove the Run entry. Returns what to tell the user.

    Refused outside the installed app on purpose: a development run would write
    python.exe and a script path into the registry, and that entry would go on
    firing at every login long after the checkout had moved.
    """
    if os.name != "nt":
        return {"ok": False, "message": "Windows only."}
    if not getattr(sys, "frozen", False):
        return {"ok": False, "on": False,
                "message": "Only the installed Riplox can start with Windows."}

    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            if on:
                winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ,
                                  _autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, _RUN_NAME)
                except FileNotFoundError:
                    pass                  # already gone is the wanted state
    except (OSError, ImportError) as exc:
        return {"ok": False, "on": autostart_on(), "message": str(exc)[:160]}

    return {"ok": True, "on": on,
            "message": ("Riplox will start with Windows, in the tray."
                        if on else "Riplox will not start with Windows.")}


# Which release is published, asked of the API rather than by starting the
# 18 MB download. Two repositories, because nightly builds have their own.
_YTDLP_RELEASE_API = {
    "stable": "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
    "nightly": ("https://api.github.com/repos/yt-dlp/yt-dlp-nightly-builds/"
                "releases/latest"),
}


def check_engine_update(force: bool = False) -> dict:
    """
    Is there a newer engine? Only ever asks - never downloads by itself.

    Called when the window opens and at most once a day after that, so it is a
    quiet line in Settings and never a running download nobody asked for. The
    request is the releases API, a few hundred bytes; the zip is only fetched
    when the update button is pressed.
    """
    state = load_settings()
    channel = str(state.get("engine_channel", "stable")).lower()
    if channel not in _YTDLP_RELEASE_API:
        channel = "stable"

    current = engine_version()
    last = float(state.get("engine_checked", 0) or 0)
    known = str(state.get("engine_latest", "") or "")

    def verdict(latest: str, checked: bool, error: str = "") -> dict:
        newer = (bool(latest) and current not in ("missing", "unknown")
                 and _version_tuple(latest) > _version_tuple(current))
        out = {"ok": not error, "checked": checked, "current": current,
               "latest": latest, "newer": newer, "channel": channel}
        if error:
            out["error"] = error
        return out

    if not force and time.time() - last < _UPDATE_GAP:
        return verdict(known, False)

    try:
        request = urllib.request.Request(
            _YTDLP_RELEASE_API[channel],
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "Riplox"})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        latest = str(data.get("tag_name") or "").lstrip("vV")
    except Exception as exc:                     # offline is not an error here
        return verdict(known, True, str(exc)[:120])

    save_settings({"engine_checked": time.time(), "engine_latest": latest})
    return verdict(latest, True)


QUALITY_LABELS = {
    "best": "Best available",
    "2160": "4K · 2160p",
    "1440": "2K · 1440p",
    "1080": "Full HD · 1080p",
    "720": "HD · 720p",
    "480": "SD · 480p",
    "360": "Low · 360p",
    "mp3": "MP3 audio",
}


# H.264 video with AAC audio in an MP4 plays in Windows Media Player,
# PowerPoint, WhatsApp and every phone. VP9/AV1 does not without an extra
# codec pack, so it is only used when the user turns compatibility off.
H264 = "[vcodec~='^(avc1|h264)']"
AAC = "[acodec~='^(mp4a|aac)']"

# YouTube now invents higher-resolution versions of old videos with AI and
# offers them alongside the real thing. yt-dlp marks them - the format id ends
# in "-sr" and the note reads "AI-upscaled" - but sorts them normally, so the
# biggest number wins and a 480p video is handed over as a machine-made 1080p.
# Measured on a 2007 upload: 399-sr at 1440x1080 was chosen over the real 397
# at 640x480.
NO_UPSCALE = "[format_id!*=-sr]"

# Which player client "More options" is allowed to ask for. Anything else the
# browser sends is dropped rather than passed through to the command line.
PLAYER_CLIENTS = ("", "tv_simply", "web_safari", "mweb", "android_vr", "ios", "web")

_OPT_KEYS = ("format_id", "audio_lang", "sub_langs", "outtmpl", "dest_dir",
             "player_client", "no_cookies", "max_mb")


def clean_opts(opts) -> dict:
    """
    Sanitise the per-download overrides.

    Everything here ends up on a command line, so nothing arrives unchecked -
    a format id with a space in it, or a template with a shell character, is
    dropped instead of being passed along.
    """
    if not isinstance(opts, dict):
        return {}

    out = {}
    for key in _OPT_KEYS:
        value = opts.get(key)
        if value in (None, "", False):
            continue

        if key == "format_id":
            text = str(value).strip()
            if re.fullmatch(r"[A-Za-z0-9_.+\-/]{1,120}", text):
                out[key] = text
        elif key in ("audio_lang", "sub_langs"):
            text = str(value).strip()
            if re.fullmatch(r"[A-Za-z0-9,\-]{1,60}", text):
                out[key] = text
        elif key == "player_client":
            text = str(value).strip()
            if text in PLAYER_CLIENTS:
                out[key] = text
        elif key == "outtmpl":
            text = str(value).strip()
            # A template names a file, so a path separator in it would let one
            # download escape the folder the rest of them go to.
            if text and len(text) <= 160 and not re.search(r'[\\/:*?"<>|]', text):
                out[key] = text
        elif key == "dest_dir":
            path = Path(str(value)).expanduser()
            if path.is_dir():
                out[key] = str(path)
        elif key == "no_cookies":
            out[key] = True
        elif key == "max_mb":
            # A size ceiling for one download. yt-dlp checks this before it
            # starts writing, which is the only place a size limit can be kept
            # honestly - guessing from a probe would sometimes be wrong, and
            # stopping halfway would leave a part file behind.
            try:
                size = int(value)
            except (TypeError, ValueError):
                continue
            if 1 <= size <= 100000:
                out[key] = size

    return out


def format_args(quality: str, settings: dict, audio_lang: str = "") -> list:
    """Translate a UI quality choice into yt-dlp format flags."""
    ff = has_ffmpeg()
    safe = bool((settings or {}).get("prefer_h264", True))
    # Empty string when the user wants the upscales, so it drops out of every
    # selector below without a second code path.
    sr = "" if (settings or {}).get("allow_ai_upscale") else NO_UPSCALE

    # Dubbed uploads are ordinary audio streams carrying a language tag, so
    # choosing one is a format filter rather than a flag of its own. The
    # fallbacks after it mean a wrong guess still downloads something.
    la = f"[language^={audio_lang}]" if audio_lang else ""

    if quality == "mp3":
        if not ff:
            # No encoder available - grab the best standalone audio track as-is.
            return ["-f", (f"bestaudio{la}/bestaudio/best" if la else "bestaudio/best")]
        # Cover art and tags: without these every song arrives as an untitled
        # grey box. YouTube serves WebP thumbnails, which MP3 cannot carry, so
        # they have to be converted before they can be embedded.
        return ["-f", (f"bestaudio{la}/bestaudio/best" if la else "bestaudio/best"), "-x",
                "--audio-format", "mp3",
                "--audio-quality", "0", "--embed-thumbnail", "--embed-metadata",
                "--convert-thumbnails", "jpg"]

    if not ff:
        # Without ffmpeg we can only take streams that are already muxed, and
        # a muxed stream carries whatever audio it was made with - so there is
        # nothing to choose between here.
        if quality == "best":
            return ["-f", f"best{sr}[ext=mp4]/best{sr}/best[ext=mp4]/best"]
        cap = f"[height<=?{quality}]"
        return ["-f", f"best{cap}{sr}[ext=mp4]/best{cap}{sr}/"
                      f"best{cap}[ext=mp4]/best{cap}/best"]

    cap = "" if quality == "best" else f"[height<=?{quality}]"

    # Built as a list and de-duplicated rather than concatenated, because with
    # no audio language chosen several of these strings come out identical -
    # and this selector is now shown to the user in More options, where a
    # repeated branch just looks like a mistake.
    branches = []

    def add(branch):
        if branch not in branches:
            branches.append(branch)

    if safe:
        add(f"bv*{cap}{sr}{H264}+ba{la}{AAC}")
        add(f"bv*{cap}{sr}{H264}+ba{la}")
        add(f"bv*{cap}{sr}{H264}+ba")
    add(f"bv*{cap}{sr}+ba{la}")
    add(f"bv*{cap}{sr}+ba")
    add(f"b{cap}{sr}")
    add(f"bv*{sr}+ba")
    add(f"b{sr}")

    # Last resort with no filter at all. A video whose every format is upscaled
    # must still download - refusing to fetch anything would be a worse answer
    # than handing over the upscale.
    if sr:
        add("bv*+ba")
        add("b")

    return ["-f", "/".join(branches), "-S", "res",
            "--merge-output-format", "mp4"]


def archive_file() -> Path:
    return data_dir() / "downloaded.txt"


# --------------------------------------------------------------------------
# Getting your list back out
# --------------------------------------------------------------------------

# Two things must never travel in a settings file: the cookies, which are a
# live signed-in session, and the pairing keys, which belong to one machine.
# Neither exists in DEFAULT_SETTINGS by accident - they are named here so the
# rule survives someone adding them later.
# "sharing" is here for the same reason as the cookies: importing a backup
# must never quietly start a listener on a machine nobody paired anything to.
# The pairing keys are not in settings at all - they live in share.json.
NEVER_EXPORT = ("cookies_file", "cookies_signin", "_update_checked",
                "_update_latest", "engine_checked", "engine_latest", "sharing")


def export_settings() -> str:
    data = {k: v for k, v in load_settings().items() if k not in NEVER_EXPORT}
    return json.dumps({
        "riplox_settings": 1,
        "note": ("Your sign-in and any paired devices are deliberately not in "
                 "here - they belong to the machine they were made on."),
        "settings": data,
    }, indent=2)


def import_settings(text: str) -> dict:
    """
    Read a settings file back. Paths are the trap: download_dir carries a
    Windows username, so a file from another PC points at a folder that does
    not exist - and the app would look like it was working while writing
    nowhere. Anything unreachable falls back to the default.
    """
    try:
        blob = json.loads(text)
    except ValueError:
        return {"ok": False, "error": "That is not a Riplox settings file."}

    incoming = blob.get("settings") if isinstance(blob, dict) else None
    if not isinstance(incoming, dict):
        return {"ok": False, "error": "That is not a Riplox settings file."}

    patch, remapped = {}, []
    for key, value in incoming.items():
        if key not in DEFAULT_SETTINGS or key in NEVER_EXPORT:
            continue
        if key == "download_dir":
            target = Path(str(value))
            reachable = target.exists() or (target.parent.exists()
                                            and free_space(target.parent) > 0)
            if not reachable:
                remapped.append(f"{value} -> {default_download_dir()}")
                value = str(default_download_dir())
        patch[key] = value

    if not patch:
        return {"ok": False, "error": "Nothing in that file could be used."}

    save_settings(patch)
    return {"ok": True, "count": len(patch), "remapped": remapped}


def export_links(items: list, kind: str) -> str:
    """
    Everything downloaded, as text. Three shapes because three different
    people want this: a bare list to paste somewhere, a spreadsheet, or the
    whole record for another program.
    """
    kind = (kind or "txt").lower()

    if kind == "json":
        return json.dumps(items, indent=2, ensure_ascii=False)

    if kind == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["title", "url", "quality", "size", "when", "file"])
        for it in items:
            writer.writerow([it.get("title", ""), it.get("url", ""),
                             it.get("quality", ""), it.get("size", ""),
                             it.get("when", ""), it.get("filepath", "")])
        return buffer.getvalue()

    # Plain list. A comment header would break pasting it straight back in.
    return "\n".join(it.get("url", "") for it in items if it.get("url"))


def queue_file() -> Path:
    return data_dir() / "queue.json"


# --------------------------------------------------------------------------
# "There is a newer Riplox"
# --------------------------------------------------------------------------
# Asks GitHub for the latest release tag and compares it. It never downloads
# and never installs - it only tells you, and points at the page. Checked at
# most once a day so it cannot become chatter.

RELEASES_API = "https://api.github.com/repos/xniperbuilds/riplox-desktop/releases/latest"
RELEASES_PAGE = "https://github.com/xniperbuilds/riplox-desktop/releases/latest"
HOME_PAGE = "https://xniperbuilds.com"
# Where a bug or an idea goes. A GitHub issue rather than an email address:
# it is public, so it cannot be quietly lost, and the person reporting can see
# what happened to it.
ISSUES_PAGE = "https://github.com/xniperbuilds/riplox-desktop/issues/new/choose"
_UPDATE_GAP = 24 * 3600

# The only addresses Riplox will ever hand to the real browser. An allowlist
# rather than a check on the string, so a page that talked its way past the
# token still cannot use the app as a launcher for anything it likes.
OPENABLE = (RELEASES_PAGE, HOME_PAGE, ISSUES_PAGE)


def _version_tuple(text: str) -> tuple:
    parts = re.findall(r"\d+", text or "")
    return tuple(int(p) for p in parts[:4]) or (0,)


def check_for_update(current: str, force: bool = False) -> dict:
    state = load_settings()
    last = float(state.get("_update_checked", 0) or 0)
    known = str(state.get("_update_latest", "") or "")

    if not force and time.time() - last < _UPDATE_GAP:
        # Answer from what we already know rather than asking again.
        return {"ok": True, "checked": False, "latest": known,
                "newer": bool(known) and _version_tuple(known) > _version_tuple(current),
                "page": RELEASES_PAGE}

    try:
        import urllib.request
        request = urllib.request.Request(
            RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                   "User-Agent": "Riplox"})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        latest = str(data.get("tag_name") or "").lstrip("vV")
    except Exception as exc:                     # offline is not an error here
        return {"ok": False, "checked": True, "error": str(exc)[:120],
                "latest": known, "newer": False, "page": RELEASES_PAGE}

    save_settings({"_update_checked": time.time(), "_update_latest": latest})
    return {"ok": True, "checked": True, "latest": latest,
            "newer": bool(latest) and _version_tuple(latest) > _version_tuple(current),
            "page": RELEASES_PAGE}


# Anything longer than this is not a timestamp the user meant to type.
_TIME_RE = re.compile(r"^\d{1,2}(:[0-5]?\d){0,2}(\.\d{1,3})?$")


def valid_time(value: str) -> bool:
    """Empty means "not given", which is fine. Anything else must parse."""
    value = (value or "").strip()
    return not value or bool(_TIME_RE.match(value))


def _clean_time(value: str) -> str:
    value = (value or "").strip()
    return value if value and _TIME_RE.match(value) else ""


def as_seconds(value: str) -> float:
    """m:ss or h:mm:ss into seconds. 0 when it is not a time."""
    if not _clean_time(value):
        return 0.0
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return total


def section_arg(start: str, end: str, exact: bool = False) -> list:
    """--download-sections for a trimmed download, or nothing."""
    # A typo must not quietly become a different clip. If either end of the
    # range was given but unreadable, take the whole video instead of guessing.
    if not (valid_time(start) and valid_time(end)):
        return []

    start, end = _clean_time(start), _clean_time(end)
    if not start and not end:
        return []

    # A trimmed download is cut by ffmpeg, and ffmpeg is the only thing that
    # knows how far it has got. We pass --print elsewhere, which turns --quiet
    # on implicitly, and quiet silences ffmpeg completely - measured: 0 status
    # lines with it, 96 without. That silence is what left the queue reading
    # 0.0% for three and a half minutes.
    args = ["--no-quiet", "--download-sections", f"*{start or '0'}-{end or 'inf'}"]

    # Exact cuts have to re-encode the video, which measured 218s against 94s
    # for the same two-minute clip. Keyframe-aligned copying is the default
    # because most people want the clip, not the extra three minutes; anyone
    # who needs the cut on the exact frame can ask for it.
    if exact:
        args.append("--force-keyframes-at-cuts")
    return args


def extra_args(settings: dict, quality: str, trimmed: bool = False) -> list:
    """Everything optional the user has switched on."""
    args = []
    audio_only = quality == "mp3"

    fragments = settings.get("fragments", 4)
    try:
        fragments = max(1, min(16, int(fragments)))
    except (TypeError, ValueError):
        fragments = 4
    args += ["--concurrent-fragments", str(fragments)]

    # Leaves the rest of the connection usable while a download runs, which
    # people notice far more than any codec setting.
    try:
        limit = int(settings.get("speed_limit", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit > 0:
        args += ["--limit-rate", f"{limit}K"]

    if settings.get("write_subs") and not audio_only:
        langs = (settings.get("sub_langs") or "en").strip() or "en"
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", langs,
                 "--sub-format", "srt/vtt/best", "--convert-subs", "srt"]
        if settings.get("embed_subs"):
            args.append("--embed-subs")

    if settings.get("embed_chapters") and not audio_only:
        args.append("--embed-chapters")

    if settings.get("sponsorblock"):
        args += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]

    # The archive remembers video ids, not files. A clip of a video you have
    # already saved in full is a different file the user is asking for on
    # purpose, so trimming ignores the archive rather than silently refusing.
    if settings.get("skip_existing") and not trimmed:
        args += ["--download-archive", str(archive_file())]

    return args


def analyze(url: str, settings: dict) -> dict:
    """
    Inspect a URL without downloading.
    Returns a single video dict, or a playlist dict with entries.
    """
    # The same ladder a download climbs. Reading a link failed on a passing
    # bot check and stopped there, while queueing the very same link retried
    # and went through - and the message shown even said the retries were
    # spent, which they were not. Pasting is the first thing anyone does, so
    # it is the worst place to be the one path that gives up immediately.
    plans = _RETRY_CLIENTS if _is_youtube(url) else _PLAIN_RETRIES
    out = None

    cookie_path, temp_cookie = open_cookies(settings, url)
    try:
        for index, client in enumerate(plans):
            args = _base_args(settings, cookie_path)
            if client:
                args += ["--extractor-args", f"youtube:player_client={client}"]
            args += ["-J", "--flat-playlist", "--no-progress", url]

            try:
                out = _run(args, timeout=120)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Timed out while reading that link.")

            if out.returncode == 0 and (out.stdout or "").strip():
                break
            if index + 1 >= len(plans) or not _is_transient(out.stderr):
                break
            time.sleep(1 + index)      # short, because someone is watching
    finally:
        close_cookies(cookie_path, temp_cookie)

    if out is None or out.returncode != 0 or not (out.stdout or "").strip():
        raise RuntimeError(_clean_error(out.stderr if out is not None else ""))

    try:
        info = json.loads(out.stdout)
    except ValueError:
        raise RuntimeError("Could not read that link.")

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]

        # A channel link does not return videos - it returns its tabs, each
        # one a playlist of its own ("Videos", "Shorts", "Live"). Showing
        # those as if they were videos would be nonsense, so hand back the
        # tabs and let the user pick one; opening a tab is then an ordinary
        # playlist, and everything already built for playlists applies.
        tabs = [e for e in entries if e.get("_type") == "playlist"]
        if tabs and len(tabs) == len(entries):
            return {
                "kind": "channel",
                "title": info.get("title") or "Channel",
                "uploader": info.get("uploader") or info.get("channel") or "",
                "thumbnail": _pick_thumb(info),
                "tabs": [
                    {
                        "url": t.get("url") or t.get("webpage_url") or "",
                        # "MrBeast - Shorts" reads better as just "Shorts".
                        "title": (t.get("title") or "").split(" - ")[-1] or "Videos",
                        "count": t.get("playlist_count"),
                    }
                    for t in tabs if (t.get("url") or t.get("webpage_url"))
                ],
            }

        return {
            "kind": "playlist",
            "title": info.get("title") or "Playlist",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "count": len(entries),
            "thumbnail": _pick_thumb(info) or (_pick_thumb(entries[0]) if entries else ""),
            "entries": [
                {
                    "url": e.get("url") or e.get("webpage_url") or "",
                    "title": e.get("title") or "Untitled",
                    "duration": e.get("duration"),
                    "thumbnail": _pick_thumb(e),
                    # Only some extractors put a date in a flat listing, so
                    # the screen offers a date sort only when one is actually
                    # there. An empty sort option that silently does nothing
                    # is worse than no option.
                    "timestamp": e.get("timestamp") or e.get("release_timestamp"),
                }
                for e in entries
            ],
        }

    rungs = _available_qualities(info, settings)
    return {
        "kind": "video",
        "url": info.get("webpage_url") or url,
        "title": info.get("title") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "thumbnail": _pick_thumb(info),
        "extractor": (info.get("extractor_key") or info.get("extractor") or "").lower(),
        "qualities": rungs["rungs"],
        "upscaled": rungs["upscaled"],
        # Everything below feeds "More options". The closed screen never shows
        # any of it, so it costs nothing to carry.
        "formats": _format_rows(info),
        "audio_langs": _audio_langs(info),
        "sub_langs": _sub_langs(info),
    }


# --------------------------------------------------------------------------
# Reading a page for the links on it
# --------------------------------------------------------------------------

# Media a browser would play, or a file worth keeping. Anything else on a page
# - stylesheets, scripts, tracking pixels - is not what anyone is asking for.
_MEDIA_EXT = (
    ".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi", ".flv", ".ts", ".m3u8",
    ".mpd", ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".wma",
)
_SKIP_SCHEME = ("mailto:", "javascript:", "tel:", "data:", "#")
_GRAB_CAP = 300               # a page with more than this is a listing, not a
                              # page, and the list stops being useful anyway
_GRAB_BYTES = 3 * 1024 * 1024


class _Links(HTMLParser):
    """Every address a page points at, with the words that pointed at it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []           # (url, title)
        self._anchor = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self._anchor = attrs["href"]
            self._text = []
            return
        # Embedded players and media elements. A page that plays something is
        # pointing at it just as plainly as a link does.
        for key in ("src", "data-src", "content"):
            value = attrs.get(key)
            if value and tag in ("iframe", "video", "audio", "source", "embed", "meta"):
                if tag == "meta" and attrs.get("property") not in (
                        "og:video", "og:video:url", "og:video:secure_url",
                        "og:audio", "twitter:player"):
                    continue
                self.found.append((value, ""))

    def handle_data(self, data):
        if self._anchor is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            self.found.append((self._anchor, " ".join("".join(self._text).split())))
            self._anchor = None
            self._text = []


def grab(url: str, settings: dict) -> dict:
    """
    Read one page and list what on it can be downloaded.

    Deliberately not an extractor and never automatic: the page is fetched
    once, read for the addresses it points at, and those are handed back for
    the user to pick from. Nothing is analysed and nothing is queued here - a
    page with two hundred links must not become two hundred requests to a site
    that will start asking whether we are a person.

    The result is shaped exactly like a playlist, so the screen that already
    handles picking, sorting and taking the first N needs no new code.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise RuntimeError("That does not look like a link.")

    request = urllib.request.Request(url, headers={
        # Some sites hand a stripped page to anything that looks automated,
        # and a stripped page has no links on it to find.
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            kind = (response.headers.get_content_type() or "").lower()
            if "html" not in kind and "xml" not in kind:
                raise RuntimeError("That address is a file, not a page. "
                                   "Paste it on its own to download it.")
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read(_GRAB_BYTES).decode(charset, "replace")
            page = response.geturl()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Could not open that page. {str(exc)[:120]}")

    parser = _Links()
    try:
        parser.feed(body)
    except Exception:
        pass                      # a half-read page still yields what it had

    entries, seen = [], {page.rstrip("/")}
    for raw, title in parser.found:
        raw = (raw or "").strip()
        if not raw or raw.lower().startswith(_SKIP_SCHEME):
            continue
        full = urljoin(page, raw)
        if not full.lower().startswith(("http://", "https://")):
            continue

        bare = full.split("#")[0].rstrip("/")
        if bare in seen:
            continue

        path = urlsplit(full).path.lower()
        site = site_of(full)
        # Worth listing if a site we know serves media from it, or if the
        # address is plainly a media file. Everything else on a page is
        # navigation.
        if not (path.endswith(_MEDIA_EXT) or site in known_sites()):
            continue

        seen.add(bare)
        entries.append({
            "url": full,
            "title": title[:120] or path.rsplit("/", 1)[-1] or site,
            "duration": None,
            "thumbnail": "",
            "timestamp": None,
        })
        if len(entries) >= _GRAB_CAP:
            break

    if not entries:
        raise RuntimeError("Nothing downloadable was found on that page.")

    title = re.search(r"<title[^>]*>(.*?)</title>", body,
                      re.I | re.S)
    return {
        "kind": "playlist",
        "grabbed": True,
        "title": (title.group(1).strip()[:120] if title else "Links on this page"),
        "uploader": site_of(page),
        "count": len(entries),
        "thumbnail": "",
        "entries": entries,
    }


def peek(url: str, settings: dict, limit: int = 30) -> dict:
    """
    The first few items of a playlist or channel tab, and nothing else.

    analyze() reads a whole playlist - eight hundred entries for a large
    channel. Watching only ever needs the newest handful, and it repeats on a
    timer, so it asks for exactly that: --playlist-end keeps the request small
    on both ends, which is the difference between a light check and one that
    looks like scraping.
    """
    limit = max(1, min(int(limit or 30), 100))
    cookie_path, temp_cookie = open_cookies(settings, url)
    try:
        args = _base_args(settings, cookie_path) + [
            "-J", "--flat-playlist", "--no-progress",
            "--playlist-end", str(limit), url,
        ]
        out = _run(args, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Timed out while checking that link.")
    finally:
        close_cookies(cookie_path, temp_cookie)

    if out.returncode != 0 or not (out.stdout or "").strip():
        raise RuntimeError(_clean_error(out.stderr))

    try:
        info = json.loads(out.stdout)
    except ValueError:
        raise RuntimeError("Could not read that link.")

    entries = [e for e in (info.get("entries") or []) if e]
    tabs = [e for e in entries if e.get("_type") == "playlist"]

    return {
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "thumbnail": _pick_thumb(info),
        # A bare channel URL answers with its tabs, not its videos. The caller
        # has to be told, because watching a tab list would never see a new
        # video appear.
        "is_tabs": bool(tabs) and len(tabs) == len(entries),
        "entries": [
            {
                "id": str(e.get("id") or e.get("url") or ""),
                "url": e.get("url") or e.get("webpage_url") or "",
                "title": e.get("title") or "Untitled",
                "duration": e.get("duration"),
                "thumbnail": _pick_thumb(e),
            }
            for e in entries if not e.get("_type") == "playlist"
        ],
        "tabs": [
            {
                "url": t.get("url") or t.get("webpage_url") or "",
                "title": (t.get("title") or "").split(" - ")[-1] or "Videos",
                "count": t.get("playlist_count"),
            }
            for t in tabs if (t.get("url") or t.get("webpage_url"))
        ],
    }


def _short_codec(value: str) -> str:
    """avc1.640028 -> avc1, mp4a.40.2 -> mp4a, none -> ''."""
    value = (value or "").split(".")[0].strip()
    return "" if value in ("none", "") else value


def _format_rows(info: dict) -> list:
    """
    Every stream the site offers, as the table "More options" shows.

    Nothing is filtered out here. The point of the panel is that a technical
    user can see the real list and pick from it exactly - a curated list would
    just be the quality chips again.
    """
    rows = []
    for f in info.get("formats") or []:
        fid = f.get("format_id")
        if not fid or f.get("format_id") == "source":
            continue

        v, a = _short_codec(f.get("vcodec")), _short_codec(f.get("acodec"))
        if not v and not a:
            continue                       # storyboards and other non-media

        height, width = f.get("height"), f.get("width")
        size = f.get("filesize") or f.get("filesize_approx") or 0
        rows.append({
            "id": str(fid),
            "ext": f.get("ext") or "",
            "res": (f"{width}x{height}" if width and height
                    else (f"{height}p" if height else "audio")),
            "height": height or 0,
            "fps": f.get("fps") or 0,
            "vcodec": v,
            "acodec": a,
            "kind": "av" if (v and a) else ("video" if v else "audio"),
            "tbr": round(f.get("tbr") or f.get("abr") or 0),
            "size": human_bytes(size) if size else "",
            "bytes": int(size or 0),
            "lang": f.get("language") or "",
            "note": (f.get("format_note") or "").strip(),
            # YouTube offers machine-enlarged copies of old uploads beside the
            # real ones, and by bare numbers they win. Marked, never hidden.
            "sr": "-sr" in str(fid) or "ai-upscaled" in (f.get("format_note") or "").lower(),
        })

    order = {"av": 0, "video": 1, "audio": 2}
    rows.sort(key=lambda r: (order.get(r["kind"], 3), -r["height"], -r["tbr"]))
    return rows


def _audio_langs(info: dict) -> list:
    """Distinct audio languages, only worth showing when there is a choice."""
    seen = []
    for f in info.get("formats") or []:
        if _short_codec(f.get("acodec")) and f.get("language"):
            if f["language"] not in seen:
                seen.append(f["language"])
    return seen if len(seen) > 1 else []


def _sub_langs(info: dict) -> list:
    """Subtitle tracks, real ones first, automatic ones marked as such."""
    out = []
    for code in sorted((info.get("subtitles") or {}).keys()):
        if code != "live_chat":
            out.append({"code": code, "auto": False})
    have = {s["code"] for s in out}
    for code in sorted((info.get("automatic_captions") or {}).keys()):
        if code not in have and "-" not in code:      # skip the translated pile
            out.append({"code": code, "auto": True})
    return out


def _pick_thumb(info: dict) -> str:
    if not isinstance(info, dict):
        return ""
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = info.get("thumbnails") or []
    if thumbs:
        return thumbs[-1].get("url", "")
    return ""


def _is_upscale(f: dict) -> bool:
    """A format YouTube invented with AI rather than one the video was made in."""
    return ("-sr" in str(f.get("format_id") or "")
            or "ai-upscaled" in str(f.get("format_note") or "").lower())


def _available_qualities(info: dict, settings: dict = None) -> dict:
    """
    Which of our fixed quality rungs this video can actually deliver, and which
    of them are only reachable through an AI-enlarged copy.

    A rung that exists solely as an upscale is not the same offer as a real
    one. With upscales skipped - the default - asking for it would quietly hand
    back a smaller file, so it is not offered at all. With them allowed it is
    offered and labelled with what it really came from, because a chip reading
    "1440p" over a 480p video is the app lying about the file.
    """
    settings = settings or {}
    allow = bool(settings.get("allow_ai_upscale"))

    real, fake = set(), set()
    for f in info.get("formats") or []:
        h = f.get("height")
        if not isinstance(h, int):
            continue
        (fake if _is_upscale(f) else real).add(h)

    best_real = max(real) if real else 0

    rungs, notes = [], {}
    for key in ("2160", "1440", "1080", "720", "480", "360"):
        target = int(key)
        if any(h >= target for h in real):
            rungs.append(key)
        elif any(h >= target for h in fake):
            # Only an upscale can reach this height.
            if allow:
                rungs.append(key)
                notes[key] = best_real
            # Skipped otherwise: offering it would promise a file we would not
            # deliver, and "download this video" means the video.

    return {"rungs": ["best"] + rungs + ["mp3"], "upscaled": notes}


def _signed_in(site: str) -> bool:
    """
    Is there a saved session for this site?

    Imported here rather than at the top - cookies.py imports engine, and two
    modules importing each other at load time is a crash.
    """
    try:
        import cookies
        return bool(cookies.site_file(site).exists())
    except Exception:
        return False


def _clean_error(stderr: str) -> str:
    """Turn a yt-dlp stack of ERROR lines into one human sentence."""
    text = (stderr or "").strip()
    if not text:
        return "That link could not be opened."

    low_all = text.lower()

    # Chrome-family cookie stores cannot be decrypted by anything but the
    # browser itself, and this is the error that says so. Riplox no longer
    # needs to: Settings has a sign-in that asks the browser for them.
    if ("dpapi" in low_all
            or "app-bound" in low_all
            or "object has no attribute 'decode'" in low_all
            or ("cookie" in low_all and "decrypt" in low_all)):
        return ("Chrome-based browsers will not let another program read their "
                "cookies. Use 'Sign in with your browser' in Settings instead.")

    # TikTok's own words for "prove you are a person". Measured on a link that
    # failed here: the page it serves carries a captcha and the line "Video
    # currently unavailable", and both the stable and the nightly engine fail
    # on it identically - so telling the user to update or to open an issue,
    # which is what the raw message says, would send them nowhere.
    #
    # Which half of the advice is true depends on whether there is a TikTok
    # session at all, and that turned out to be the whole story: the session
    # store held YouTube and Google and no TikTok cookie whatsoever, while the
    # screen said "signed in". Telling someone to try again later when they
    # have never signed in wastes their afternoon.
    if "unexpected response from webpage" in low_all:
        if _signed_in("tiktok"):
            return ("TikTok served a verification page instead of the video, "
                    "and the saved TikTok sign-in did not get past it. Sign in "
                    "again from Settings, or try again in a while.")
        return ("TikTok is asking this computer to verify itself, so it served "
                "a check page instead of the video. There is no TikTok sign-in "
                "saved yet - Settings, then Sign in, then TikTok.")

    if "cookie" in low_all and ("permission" in low_all or "could not copy"
                                in low_all or "database" in low_all):
        return ("Those cookies cannot be read while the browser is open. Use "
                "'Sign in with your browser' in Settings - it does not have "
                "this problem.")

    if "javascript runtime" in low_all or "no supported javascript" in low_all:
        return ("The JavaScript helper is missing, so YouTube will not hand "
                "over its streams. Reinstall Riplox to restore it.")

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ERROR:"):
            msg = line[6:].strip()
            msg = msg.split(";")[0].strip()
            low = msg.lower()
            if "unsupported url" in low:
                return "This site is not supported."
            if "not a bot" in low or "login_required" in low:
                # Usually a passing IP-level check: Riplox already retries on
                # its own, so by the time this is shown the retries are spent.
                return ("YouTube asked for proof you are a real viewer and kept "
                        "asking. Wait a few minutes, or sign in with your "
                        "browser in Settings.")
            if "private" in low or "login" in low or "sign in" in low:
                return "This video is private - sign in with your browser in Settings."
            if "unavailable" in low or "removed" in low:
                return "This video is unavailable or was removed."
            if "geo" in low and "restrict" in low:
                return "This video is blocked in your region."
            if "age" in low and ("restrict" in low or "confirm" in low):
                return "Age-restricted - sign in with your browser in Settings."
            if "requested format is not available" in low:
                return ("YouTube did not offer this quality for that video. "
                        "Try 'Best available'.")
            return msg[:200]

    return text.splitlines()[-1][:200]


# --------------------------------------------------------------------------
# Download jobs
# --------------------------------------------------------------------------

PROGRESS_TAG = "@@RPX@@"
POST_TAG = "@@RPXPP@@"
PATH_TAG = "@@RPXFILE@@"
# A link sent from a phone was never analysed here, so nothing ever knew what
# it looked like and the Library showed it as a blank tile. The engine already
# knows; it just has to be asked as the download starts.
THUMB_TAG = "@@RPXTHUMB@@"

# First attempt uses whatever yt-dlp picks. The next two ask YouTube through a
# different player client, which is what usually clears a bot check.
_RETRY_CLIENTS = ["", "tv_simply,web_safari", "mweb,android_vr"]

# Other sites have no player client to switch, but the same request often
# works seconds later - a TikTok link that failed with "unable to extract
# universal data for rehydration" succeeded on the very next attempt with the
# same engine. Without this, every site except YouTube got exactly one try.
_PLAIN_RETRIES = ["", "", ""]

_TRANSIENT = (
    "not a bot", "login_required", "429", "too many requests",
    "temporarily", "try again later", "unable to download webpage",
    "read timed out", "timed out", "connection reset", "forcing sabr",
    "missing a url", "unable to connect",
    # Extractor hiccups. These can also mean a site really has changed and the
    # engine needs an update, in which case the retries fail too and cost a few
    # seconds - worth it, because most of the time the page just came back
    # half-built.
    "unable to extract", "rehydration", "unable to parse",
    "no video formats found", "unable to download json metadata",
    # TikTok short links (vt.tiktok.com) answer with this when the page comes
    # back half-built, which it does often and at random. The engine was
    # already on its newest release when this was reported, so waiting for an
    # update would have fixed nothing - the same link works on a second try.
    "unexpected response from webpage",
)


# Enough of the common ones to make the Library's shelves read like places
# rather than domains. Anything else keeps its own bare hostname.
_SITE_NAMES = {
    "youtube": "YouTube", "youtu": "YouTube", "tiktok": "TikTok",
    "instagram": "Instagram", "facebook": "Facebook", "fb": "Facebook",
    "twitter": "X", "x": "X", "reddit": "Reddit", "vimeo": "Vimeo",
    "dailymotion": "Dailymotion", "twitch": "Twitch", "soundcloud": "SoundCloud",
    "pinterest": "Pinterest", "snapchat": "Snapchat", "linkedin": "LinkedIn",
}


# --------------------------------------------------------------------------
# Downloading only at certain hours
# --------------------------------------------------------------------------

def _minutes(text: str, fallback: int) -> int:
    """"HH:MM" as minutes past midnight, or the fallback if it is nonsense."""
    try:
        hour, _, minute = str(text or "").partition(":")
        hour, minute = int(hour), int(minute)
    except (TypeError, ValueError):
        return fallback
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return fallback
    return hour * 60 + minute


def schedule_allows(settings: dict, now=None) -> bool:
    """
    May a new download start right now?

    Off by default, and when it is on it is a window rather than a delay: "from
    01:00 to 08:00" covers the ordinary reason for wanting this, which is to
    leave the queue filled during the day and have it run when nobody is using
    the connection. A window that crosses midnight is the normal case, so it
    is the one that has to work.
    """
    settings = settings or {}
    if not settings.get("schedule_on"):
        return True

    start = _minutes(settings.get("schedule_from"), 0)
    end = _minutes(settings.get("schedule_to"), 0)
    if start == end:
        return True                # a window of no width is not a schedule

    now = now or datetime.now()
    minute = now.hour * 60 + now.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end      # runs across midnight


def schedule_note(settings: dict, now=None) -> str:
    """One line for the screen, or empty when nothing is being held."""
    if schedule_allows(settings, now):
        return ""
    start = settings.get("schedule_from") or "00:00"
    end = settings.get("schedule_to") or "00:00"
    return f"Waiting - downloads start at {start} and stop at {end}."


def site_of(url: str) -> str:
    host = re.sub(r"^https?://", "", (url or "").strip().lower())
    host = host.split("/")[0].split("?")[0]
    if not host:
        return "Other"
    parts = [p for p in host.split(".") if p not in ("www", "m", "mobile", "vt", "vm")]
    for part in parts:
        if part in _SITE_NAMES:
            return _SITE_NAMES[part]
    return (parts[0].capitalize() if parts else "Other")


def known_sites() -> tuple:
    """
    The sites a per-device rule can name, in the same words the Library uses.

    Read from the table above rather than written out a second time, so a site
    added there cannot quietly become one no rule is able to mention.
    """
    return tuple(sorted(set(_SITE_NAMES.values())))


def _is_youtube(url: str) -> bool:
    low = (url or "").lower()
    return "youtube.com" in low or "youtu.be" in low


def _is_transient(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _TRANSIENT)


# The one failure a proof-of-origin token actually helps with. Kept separate
# from _TRANSIENT, which is far broader.
_BOTCHECK = ("not a bot", "login_required", "sign in to confirm")


def is_botcheck(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _BOTCHECK)


def _diagnostic(args: list, stderr_lines: list, code, job) -> str:
    """
    What the user can hand to someone who can read it. The command is included
    because "which flags were actually used" is the first question, but the
    cookie file is a live session so its path is never written down.
    """
    shown = []
    skip_next = False
    for item in args:
        if skip_next:
            shown.append("<cookies>")
            skip_next = False
            continue
        if item == "--cookies":
            shown.append(item)
            skip_next = True
            continue
        shown.append(str(item))

    head = [
        f"Riplox job {job.id}  attempt {job.attempt}",
        f"url      {job.url}",
        f"quality  {job.quality}",
        f"engine   {engine_version()}",
        f"js       {'quickjs bundled' if qjs_path() else 'none found'}",
        f"exit     {code}",
        "command  " + " ".join(shown),
        "",
    ]
    tail = [line for line in stderr_lines if line.strip()][-40:]
    return "\n".join(head + tail)


def environment() -> dict:
    """A one-glance summary of what the engine has to work with."""
    info = {
        "engine": engine_version(),
        "channel": load_settings().get("engine_channel", "stable"),
        "ffmpeg": bool(has_ffmpeg()),
        "js": bool(qjs_path()),
        "potoken": False,
    }
    try:
        import potoken
        info["potoken"] = potoken.installed()
    except Exception:
        pass
    return info


class Job:
    __slots__ = ("id", "url", "title", "thumbnail", "quality", "status", "percent",
                 "speed", "eta", "size", "filepath", "error", "created", "proc",
                 "cancelled", "uploader", "batch", "log", "attempt",
                 "start", "end", "exact", "stage", "paused", "kind", "conv",
                 "opts", "origin", "streams")

    def __init__(self, url, title="", thumbnail="", quality="best", uploader="",
                 batch=False, start="", end="", exact=False, opts=None,
                 origin=""):
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.title = title or url
        self.thumbnail = thumbnail
        self.uploader = uploader
        self.quality = quality
        self.status = "queued"
        self.percent = 0.0
        self.speed = ""
        self.eta = ""
        self.size = ""
        self.filepath = ""
        self.error = ""
        self.created = time.time()
        self.proc = None
        self.cancelled = False
        self.batch = batch
        self.start = _clean_time(start)
        self.end = _clean_time(end)
        self.exact = bool(exact)
        # Whatever "More options" was showing when Download was pressed. It
        # belongs to this one job and is never written back to Settings, so a
        # stray click there cannot change every future download.
        self.opts = clean_opts(opts)
        # Which paired device asked for this, if any. Recorded so a per-device
        # allowance can be measured against what was actually downloaded rather
        # than against what was requested.
        self.origin = str(origin or "")[:24]
        # Stopping to carry on later is not the same as giving up, and the
        # half-written file has to survive the difference.
        self.paused = False
        # Converting shares the queue with downloading: same progress, same
        # Cancel, same notifications, nothing new to invent.
        self.kind = "download"
        self.conv = {}
        # What a trimmed download is doing while yt-dlp reports no percentage.
        self.stage = ""
        # How many streams have finished. A merged download is video then
        # audio, and the one progress bar has to cover both.
        self.streams = 0
        # Kept so the user can hand a real error to someone who can read it,
        # instead of the one friendly sentence the UI shows.
        self.log = ""
        self.attempt = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "uploader": self.uploader,
            "quality": self.quality,
            "qualityLabel": QUALITY_LABELS.get(self.quality, self.quality),
            "status": self.status,
            "percent": round(self.percent, 1),
            "speed": self.speed,
            "eta": self.eta,
            "size": self.size,
            "filepath": self.filepath,
            "error": self.error,
            "attempt": self.attempt,
            "hasLog": bool(self.log),
            "clip": (f"{self.start or '0'}–{self.end or 'end'}"
                     if (self.start or self.end) else ""),
            "stage": self.stage,
            "kind": self.kind,
            # Lets the queue offer "Fix this" exactly when the helper would
            # have made a difference, instead of on every failure.
            "botcheck": self.status == "error" and is_botcheck(self.error + self.log),
        }


class DownloadManager:
    """Fixed-size worker pool over a FIFO queue of jobs."""

    def __init__(self):
        self._jobs = {}
        self._order = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._workers = []
        self._running = True
        self._sync_workers()

    # -- public API ------------------------------------------------------

    ACTIVE = ("queued", "starting", "downloading", "converting", "paused")

    # -- surviving a restart ---------------------------------------------
    # Quitting with ten videos still queued used to throw all ten away. They
    # come back paused rather than running: a queue that starts downloading on
    # its own the moment the app opens is not a nice surprise.

    def _save(self) -> None:
        try:
            with self._lock:
                pending = [
                    {"id": j.id, "url": j.url, "title": j.title,
                     "thumbnail": j.thumbnail, "uploader": j.uploader,
                     "quality": j.quality, "start": j.start, "end": j.end,
                     "exact": j.exact, "batch": j.batch,
                     # Carried across a restart because both change what the
                     # job does: opts holds the folder, the format and any
                     # size ceiling chosen for this one download, and origin
                     # is the device whose allowance it counts against. A
                     # restored job used to quietly lose both.
                     "opts": j.opts, "origin": j.origin}
                    for j in (self._jobs.get(i) for i in self._order)
                    if j is not None and j.status in self.ACTIVE
                ]
            tmp = queue_file().with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(pending, fh)
            tmp.replace(queue_file())
        except OSError:
            pass          # losing the queue file must never break a download

    def restore(self) -> int:
        """Bring back what was still waiting when the app last closed."""
        try:
            with open(queue_file(), "r", encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return 0

        count = 0
        with self._lock:
            for item in saved[:200]:
                url = (item or {}).get("url", "")
                if not url:
                    continue
                job = Job(url, item.get("title", ""), item.get("thumbnail", ""),
                          item.get("quality", "best"), item.get("uploader", ""),
                          bool(item.get("batch")), item.get("start", ""),
                          item.get("end", ""), bool(item.get("exact")),
                          item.get("opts"), item.get("origin", ""))
                job.status = "paused"
                self._jobs[job.id] = job
                self._order.append(job.id)
                count += 1
        return count

    def resume_all(self) -> int:
        with self._lock:
            waiting = [j for j in self._jobs.values() if j.status == "paused"]
            for job in waiting:
                job.status = "queued"
                job.paused = False
                job.cancelled = False
        self._save()
        self._wake.set()
        return len(waiting)

    def add(self, url, title="", thumbnail="", quality="best", uploader="",
            batch=False, start="", end="", exact=False, opts=None,
            origin="") -> Job:
        job = Job(url, title, thumbnail, quality, uploader, batch, start, end,
                  exact, opts, origin)

        with self._lock:
            # The same link at the same quality writes the same file, so two
            # copies running at once would fight over it. Hand back the job
            # that is already doing the work. A different clip range is a
            # different file, so it is not a duplicate - and so is a different
            # format or a different folder chosen in More options.
            for existing in self._jobs.values():
                if (existing.url == url and existing.quality == quality
                        and existing.start == job.start
                        and existing.end == job.end
                        and existing.opts == job.opts
                        and existing.status in self.ACTIVE):
                    return existing

            self._jobs[job.id] = job
            self._order.append(job.id)

        self._save()
        self._wake.set()
        return job

    def add_convert(self, source, fmt: str, quality: str, target_dir="") -> Job:
        """Queue a file already on disk for conversion, not a download."""
        source = Path(source)
        job = Job(str(source), title=source.name, quality=fmt)
        job.kind = "convert"
        job.conv = {"source": str(source), "fmt": fmt, "quality": quality,
                    "target_dir": str(target_dir or source.parent)}

        with self._lock:
            for existing in self._jobs.values():
                if (existing.kind == "convert"
                        and existing.conv.get("source") == str(source)
                        and existing.conv.get("fmt") == fmt
                        and existing.status in self.ACTIVE):
                    return existing
            self._jobs[job.id] = job
            self._order.append(job.id)

        self._save()
        self._wake.set()
        return job

    def snapshot(self) -> list:
        with self._lock:
            return [self._jobs[i].to_dict() for i in self._order if i in self._jobs]

    def _stop(self, job_id: str, pausing: bool) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job or job.status in ("done", "error", "cancelled", "paused"):
            return False

        job.paused = pausing
        job.cancelled = True          # both stop the run; only the label differs
        proc = job.proc
        if proc and proc.poll() is None:
            _kill_tree(proc)
        else:
            job.status = "paused" if pausing else "cancelled"
        return True

    def cancel(self, job_id: str) -> bool:
        return self._stop(job_id, pausing=False)

    def pause(self, job_id: str) -> bool:
        """
        Stop now, keep going later. yt-dlp leaves a .part file behind and
        continues from it on the next run, so resuming costs nothing that has
        already been downloaded - which is the whole point.
        """
        return self._stop(job_id, pausing=True)

    def retry(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in ("error", "cancelled", "paused"):
                return False
            job.status = "queued"
            job.error = ""
            job.percent = 0.0
            job.cancelled = False
            job.paused = False
            job.attempt = 0
        self._save()
        self._wake.set()
        return True

    def job_log(self, job_id: str) -> str:
        with self._lock:
            job = self._jobs.get(job_id)
        return job.log if job else ""

    def remove(self, job_id: str) -> bool:
        self.cancel(job_id)
        with self._lock:
            self._jobs.pop(job_id, None)
            if job_id in self._order:
                self._order.remove(job_id)
        self._save()
        return True

    def clear_finished(self) -> None:
        with self._lock:
            keep = []
            for jid in self._order:
                job = self._jobs.get(jid)
                if job and job.status in ("done", "error", "cancelled"):
                    self._jobs.pop(jid, None)
                else:
                    keep.append(jid)
            self._order = keep
        self._save()

    # -- worker plumbing -------------------------------------------------

    def _sync_workers(self) -> None:
        """Grow the pool to match the configured parallelism."""
        want = max(1, min(5, int(load_settings().get("max_parallel", 2))))
        while len(self._workers) < want:
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()
            self._workers.append(t)

    def _next_job(self):
        settings = load_settings()
        # Only the start of a job is held back. Stopping one already running
        # would throw away what it had, and a half-file at nine in the morning
        # is a worse thing to wake up to than a download that ran late.
        if not schedule_allows(settings):
            return None

        want = max(1, min(5, int(settings.get("max_parallel", 2))))
        with self._lock:
            active = sum(1 for j in self._jobs.values()
                         if j.status in ("downloading", "converting", "starting"))
            if active >= want:
                return None
            for jid in self._order:
                job = self._jobs.get(jid)
                if job and job.status == "queued":
                    job.status = "starting"
                    return job
        return None

    def _worker_loop(self) -> None:
        while self._running:
            job = self._next_job()
            if job is None:
                self._wake.wait(0.4)
                self._wake.clear()
                continue
            try:
                self._run_job(job)
            except EngineMissing:
                job.status = "error"
                job.error = "Download engine is missing. Reinstall Riplox."
            except Exception as exc:  # a crashed job must not kill the worker
                job.status = "error"
                job.error = str(exc)[:200]
            self._save()
            self._wake.set()

    def _outtmpl(self, settings: dict, job: Job) -> str:
        opts = getattr(job, "opts", None) or {}

        if opts.get("dest_dir"):
            # A folder chosen for this one download replaces the usual root,
            # and takes folder-per-site with it: the user named the folder.
            root = Path(opts["dest_dir"])
        else:
            root = Path(settings["download_dir"])
            if settings.get("subfolder_per_site"):
                root = root / "%(extractor_key)s"

        # A name given by hand is used exactly as given. The collision guards
        # below are defaults, not rules to enforce on someone who typed a name.
        if opts.get("outtmpl"):
            return str(root / opts["outtmpl"])

        # A clip is a different file from the whole video, so it needs a
        # different name - the same reason the height is in there.
        clip = ""
        if job.start or job.end:
            clip = " clip " + (job.start or "0").replace(":", ".") + "-" + \
                   (job.end or "end").replace(":", ".")

        if job.quality == "mp3":
            # Audio lands as .mp3, so it can never collide with a video file.
            return str(root / f"%(title).110B [%(id)s]{clip}.%(ext)s")

        # Height belongs in the name: without it, grabbing the same video at
        # 720p and then at 1080p silently overwrote the first file.
        return str(root / f"%(title).100B [%(id)s] %(height)sp{clip}.%(ext)s")

    def _run_job(self, job: Job) -> None:
        """
        Run the job, and give a transient refusal a second and third chance.

        YouTube's "confirm you're not a bot" is usually a passing check on the
        connection rather than a verdict on the video - the same link often
        works moments later, and more often still on a different player
        client. Retrying by hand was the fix; doing it here means the user
        never sees it.
        """
        if job.kind == "convert":
            self._convert(job)
            return

        settings = load_settings()

        # Checked here rather than at queue time: a playlist can be queued with
        # room to spare and run out on the fortieth video.
        room = free_space(settings.get("download_dir", ""))
        if 0 <= room < SPACE_FLOOR:
            job.status = "error"
            job.error = (f"Not enough space left - only {human_bytes(room)} "
                         f"free. Free some up, then press retry.")
            return

        plans = _RETRY_CLIENTS if _is_youtube(job.url) else _PLAIN_RETRIES

        for index, client in enumerate(plans):
            job.attempt = index + 1
            if self._attempt(job, settings, client) or job.cancelled:
                return

            last = index + 1 >= len(plans)
            if last or not _is_transient(job.log):
                return

            # Held in a non-queued state so no other worker takes it as well.
            job.status = "starting"
            job.error = ""
            job.percent = 0.0
            job.speed = job.eta = ""

            # Waited out in small steps so Cancel is noticed now rather than
            # when the next attempt has already started.
            deadline = time.monotonic() + 2 + 4 * index
            while time.monotonic() < deadline:
                if job.cancelled:
                    job.status = "paused" if job.paused else "cancelled"
                    return
                time.sleep(0.2)

    def _convert(self, job: Job) -> None:
        # Imported here rather than at the top: convert.py needs engine, and
        # two modules importing each other at load time is a crash.
        import convert

        job.status = "converting"
        job.error = ""
        job.stage = ""
        spec = job.conv

        result = convert.run(spec["source"], spec["target_dir"], spec["fmt"],
                             spec["quality"], job=job)

        if result.get("cancelled") or job.cancelled:
            job.status = "paused" if job.paused else "cancelled"
            return
        if not result.get("ok"):
            job.status = "error"
            job.error = result.get("error", "The conversion failed.")
            job.log = (f"Riplox convert\nsource   {spec['source']}\n"
                       f"format   {spec['fmt']} ({spec['quality']})\n"
                       f"error    {job.error}")
            return

        job.status = "done"
        job.percent = 100.0
        job.filepath = result["path"]
        job.size = human_bytes(result.get("size", 0))
        job.title = Path(result["path"]).name
        job.stage = "copied" if result.get("copied") else ""
        add_history({
            "title": job.title, "url": spec["source"], "quality": spec["fmt"],
            "size": job.size, "filepath": job.filepath,
            "when": datetime.now().isoformat(timespec="seconds"),
        })

    def _attempt(self, job: Job, settings: dict, client: str) -> bool:
        cookie_path, temp_cookie = open_cookies(settings, job.url)
        try:
            return self._spawn(job, settings, client, cookie_path)
        finally:
            close_cookies(cookie_path, temp_cookie)

    def build_args(self, job: Job, settings: dict, client: str,
                   cookie_path) -> list:
        """
        The exact command this job will run.

        Split out so "More options" can show it without a second copy of this
        logic - a preview that drifts from what actually runs is worse than no
        preview at all.
        """
        opts = getattr(job, "opts", None) or {}
        if opts.get("no_cookies"):
            cookie_path = None
        args = _base_args(settings, cookie_path, batch=job.batch)

        # A client named in More options wins over the retry ladder: it was
        # asked for deliberately, and silently trying a different one would
        # make the panel a lie.
        client = opts.get("player_client") or client
        if client:
            args += ["--extractor-args", f"youtube:player_client={client}"]

        if opts.get("format_id"):
            # Picked from the table by hand, so it is used exactly as picked.
            args += ["-f", opts["format_id"]]
        else:
            args += format_args(job.quality, settings, opts.get("audio_lang", ""))

        trimmed = bool(job.start or job.end)
        args += extra_args(settings, job.quality, trimmed)
        if opts.get("max_mb"):
            args += ["--max-filesize", f"{opts['max_mb']}M"]
        if opts.get("sub_langs"):
            args += ["--write-subs", "--write-auto-subs",
                     "--sub-langs", opts["sub_langs"]]
        args += section_arg(job.start, job.end, job.exact)
        args += [
            "--newline",
            # --print implies --quiet, which would swallow every progress line.
            "--progress",
            "--no-playlist",
            "--windows-filenames",
            "--retries", "5",
            "--fragment-retries", "10",
            "-o", self._outtmpl(settings, job),
            "--progress-template",
            (PROGRESS_TAG + "%(progress.status)s|%(progress.downloaded_bytes)s|"
             "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
             "%(progress.speed)s|%(progress.eta)s"),
            "--progress-template",
            "postprocess:" + POST_TAG + "%(progress.status)s|%(progress.postprocessor)s",
            "--print", "after_move:" + PATH_TAG + "%(filepath)s",
            # Costs nothing - the engine has already read the page by then -
            # and it is the only way a phone-sent download ever gets a picture.
            "--print", "before_dl:" + THUMB_TAG + "%(thumbnail)s|%(title)s",
            "--no-simulate",
        ]

        if settings.get("write_thumbnail"):
            args.append("--write-thumbnail")

        args.append(job.url)
        return args

    def _spawn(self, job: Job, settings: dict, client: str, cookie_path) -> bool:
        args = self.build_args(job, settings, client, cookie_path)

        job.status = "downloading"
        job.error = ""
        # A retry starts the streams again from the top, so the bar's idea of
        # which one is running has to start again too.
        job.streams = 0
        # Working out which streams to cut takes half a minute before ffmpeg
        # says anything, and a blank row for half a minute reads as broken.
        job.stage = "preparing" if (job.start or job.end) else ""

        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_NO_WINDOW,
        )
        job.proc = proc

        # Read stderr as it arrives rather than in one lump at the end. A
        # trimmed download is handed to ffmpeg, which reports its progress
        # here and never through yt-dlp's progress template - so without this
        # the queue sat at 0.0% for three and a half minutes and looked dead.
        stderr_lines = []

        def drain_stderr():
            for raw in proc.stderr:
                line = raw.rstrip("\r\n")
                stderr_lines.append(line)
                del stderr_lines[:-400]
                if job.start or job.end:
                    self._apply_ffmpeg_progress(job, line)

        err_thread = threading.Thread(target=drain_stderr, daemon=True)
        err_thread.start()

        for line in proc.stdout:
            # Killing yt-dlp does not empty the pipe it already filled. There
            # can be ten seconds of buffered progress left, and every line of
            # it used to push the row back to "downloading" with a rising
            # percentage - so Pause looked like it had done nothing at all.
            # The backlog still has to be drained for the pipe to close; it
            # just must not be believed.
            if job.cancelled:
                continue

            line = line.rstrip("\r\n")
            if line.startswith(PROGRESS_TAG):
                self._apply_progress(job, line[len(PROGRESS_TAG):])
            elif line.startswith(POST_TAG):
                job.status = "converting"
            elif line.startswith(PATH_TAG):
                job.filepath = line[len(PATH_TAG):].strip()
            elif line.startswith(THUMB_TAG):
                # Only fills gaps. A link analysed on this PC already carries
                # the picture and the title the user saw before pressing
                # Download, and those must win.
                rest = line[len(THUMB_TAG):].strip()
                thumb, _, title = rest.partition("|")
                if not job.thumbnail and thumb and thumb.startswith("http"):
                    job.thumbnail = thumb
                if title and title != "NA" and job.title in (job.url, ""):
                    job.title = title
            elif "has already been downloaded" in line:
                # Nothing moves, so after_move never fires - take the path here
                # or the Play button would have nothing to open.
                existing = line.split("] ", 1)[-1]
                existing = existing.replace("has already been downloaded", "").strip()
                if existing:
                    job.filepath = existing
            elif "[download] Destination:" in line and not job.filepath:
                job.filepath = line.split("Destination:", 1)[1].strip()

        proc.wait()
        err_thread.join(timeout=2)
        job.proc = None
        job.stage = ""

        # Settle what the user sees before writing the diagnostic. Building
        # that asks yt-dlp for its version, and a Pause that takes a couple of
        # seconds to show up reads as a Pause that did not work.
        if job.cancelled:
            job.status = "paused" if job.paused else "cancelled"
            job.speed = job.eta = ""
            job.log = _diagnostic(args, stderr_lines, proc.returncode, job)
            return True          # nothing left to retry

        job.log = _diagnostic(args, stderr_lines, proc.returncode, job)

        # A size ceiling is not an error to yt-dlp: it prints one line, skips
        # the video and exits 0. Without this the row would read "done" over a
        # file that was never written, which is the worst of both answers.
        if proc.returncode == 0 and job.opts.get("max_mb") and not job.filepath:
            job.status = "error"
            job.error = (f"Bigger than the {job.opts['max_mb']} MB limit set "
                         f"for this device, so it was not downloaded.")
            return True          # a rule was applied; retrying changes nothing

        if proc.returncode == 0:
            job.status = "done"
            job.percent = 100.0
            job.speed = job.eta = ""
            if job.filepath and job.title in (job.url, ""):
                job.title = Path(job.filepath).stem

            # Progress reports one stream at a time, so the running total is
            # only the last stream. The finished file on disk is the truth.
            written = 0
            try:
                written = Path(job.filepath).stat().st_size
                job.size = human_bytes(written)
            except (OSError, ValueError):
                pass
            add_history({
                "title": job.title,
                "url": job.url,
                "filepath": job.filepath,
                "quality": job.quality,
                "thumbnail": job.thumbnail,
                "size": job.size,
                # The same size as a number. "12.4 MB" reads better; an
                # allowance cannot be added up out of it.
                "bytes": written,
                # Which paired device this came from, so its allowance is
                # measured against files that actually landed on the disk.
                "from": job.origin,
                # Where it came from, recorded rather than guessed. The Library
                # groups by this; without it the only clue is the folder name,
                # and folder-per-site is off by default - so everything would
                # end up filed under "Downloads".
                "site": site_of(job.url),
                "when": datetime.now().isoformat(timespec="seconds"),
            })
            return True

        job.status = "error"
        job.error = _clean_error("\n".join(stderr_lines))
        return False

    # ffmpeg writes one status line per second: "... size= 39680KiB
    # time=00:01:46.70 bitrate=... speed=1.26x".
    _FF_TIME = re.compile(r"\btime=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
    # The running lines say "size=", the final one says "Lsize=".
    _FF_SIZE = re.compile(r"(?:^|\s)L?size=\s*(\d+)\s*KiB")
    _FF_SPEED = re.compile(r"\bspeed=\s*([\d.]+)x")

    def _apply_ffmpeg_progress(self, job: Job, line: str) -> None:
        if job.cancelled:
            return                       # same buffered-backlog problem
        found = self._FF_TIME.search(line)
        if not found:
            return

        hours, minutes, seconds = found.groups()
        done = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        job.stage = "cut " + (_human_time(done) or "0s")

        # A percentage is only honest when the clip has both ends. Open-ended
        # clips get the position and the size instead of a made-up number.
        if job.start and job.end:
            length = as_seconds(job.end) - as_seconds(job.start)
            if length > 0:
                job.percent = max(job.percent, min(99.0, done / length * 100.0))

        size = self._FF_SIZE.search(line)
        if size:
            job.size = human_bytes(int(size.group(1)) * 1024)

        speed = self._FF_SPEED.search(line)
        if speed:
            job.speed = speed.group(1) + "x"

        if job.status not in ("converting", "cancelled"):
            job.status = "downloading"

    # Where each stream sits on the one bar the user sees. yt-dlp reports
    # progress per stream, so a merged download used to race to 99% on the
    # video and then sit there, apparently frozen, for the whole of the audio
    # track - which is exactly what it was reported as. Video is the large
    # one, so it gets almost all of the bar and audio gets the rest.
    _STREAM_BANDS = ((0.0, 92.0), (92.0, 99.0))

    def _apply_progress(self, job: Job, payload: str) -> None:
        parts = payload.split("|")
        if len(parts) < 6:
            return
        status, done, total, total_est, speed, eta = parts[:6]

        downloaded = _num(done)
        size = _num(total) or _num(total_est)

        index = min(job.streams, len(self._STREAM_BANDS) - 1)
        low, high = self._STREAM_BANDS[index]

        if size:
            pct = min(1.0, downloaded / size)
            # Only ever forward, and 100% is saved for the moment the file is
            # actually on the disk.
            job.percent = max(job.percent, min(99.0, low + (high - low) * pct))
            job.size = human_bytes(size)

        job.speed = f"{human_bytes(_num(speed))}/s" if _num(speed) else ""
        job.eta = _human_time(_num(eta))

        # Says which half is running, so a bar that slows down still reads as
        # working rather than stuck.
        if not (job.start or job.end):
            job.stage = "audio" if index else ""

        if status == "finished":
            job.speed = job.eta = ""
            job.streams += 1
        elif job.status not in ("converting", "cancelled"):
            job.status = "downloading"


def _num(value: str):
    try:
        f = float(value)
        return f if f == f and f not in (float("inf"), float("-inf")) else 0
    except (TypeError, ValueError):
        return 0


def human_bytes(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return "0 B"


def _human_time(seconds) -> str:
    s = int(seconds or 0)
    if s <= 0:
        return ""
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    return f"{s // 3600}h {(s % 3600) // 60}m"
