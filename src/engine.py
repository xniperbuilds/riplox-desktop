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
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import zipfile
from datetime import datetime, timedelta
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


# Portable mode. A "Data" folder beside the exe means "keep everything in
# here" - the ZIP ships one, the installer never does. The folder is both the
# marker and the destination, so there is nothing to configure and nothing
# that can disagree with itself.
#
# WARNING: it must never reach dist/Riplox. The installer copies that folder
# wholesale, so a marker left there would move every INSTALLED user's data
# root into the install directory - silently, because a per-user install is
# writable. Their settings, history and phone pairing would all look gone.
# build/make_portable.py stages a copy for exactly this reason, and
# installer.iss excludes Data\* as a second line of defence.
_PORTABLE_MARK = "Data"

# A PortableApps.com package is portable too, but it puts Data at the package
# root rather than beside the exe - the app lives down in App\Riplox\, and the
# PortableApps menu's own backup and sync only ever look at the root Data. Left
# to the rule above, Riplox would make a SECOND Data folder three levels down
# and every setting, every history entry and the phone pairing would sit
# outside what that menu backs up. Nothing would fail; it would just quietly
# not be there when the user restored.
#
# Their launcher does not hand any of this over by itself: %PAL:DataDir% is a
# substitution that only exists inside launcher.ini. The name below is
# therefore OURS, declared in the package's [Environment] section as
# RIPLOX_PORTABLE_DATA=%PAL:DataDir% - which is also why nothing else on a
# normal PC ever sets it.
_PAF_DATA_VAR = "RIPLOX_PORTABLE_DATA"

_root = None                  # decided once, on the first call
_root_state = "off"           # off | on | read-only


def _installed_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / APP_NAME


def _app_folder():
    """The folder the packaged app runs from, or None when run from source."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def _writable(folder: Path) -> bool:
    """Can this actually be written to? A read-only stick answers no."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _decide_root():
    """
    Where this copy keeps its things, and why.

    Running from source is never portable: a checkout has no Data folder, and
    if somebody made one by hand it would still be the wrong place for it.
    """
    home = _app_folder()
    if home is not None:
        # A PortableApps.com launcher told us where its Data folder is. It
        # wins, because that is the folder their menu backs up. Required to
        # exist already - the format always ships one, so a path that is not
        # there is a typo in launcher.ini, and inventing the folder would hide
        # that behind a package which looks fine and syncs nothing. Falling
        # through leaves Settings reading "off", which is the visible signal.
        given = os.environ.get(_PAF_DATA_VAR, "").strip()
        if given and Path(given).is_dir():
            spot = Path(given)
            if _writable(spot):
                return spot, "on"
            return _installed_root(), "read-only"

        beside = home / _PORTABLE_MARK
        if beside.is_dir():
            if _writable(beside):
                return beside, "on"
            # Wanted, and not possible - a read-only stick, or unpacked
            # somewhere this user cannot write. Falling back is right.
            # Falling back QUIETLY is not, so Settings gets told.
            return _installed_root(), "read-only"
    return _installed_root(), "off"


def data_dir() -> Path:
    """User-writable folder for settings, history and the live binaries."""
    global _root, _root_state
    if _root is None:
        _root, _root_state = _decide_root()
    _root.mkdir(parents=True, exist_ok=True)
    return _root


def portable_state() -> str:
    """"on", "read-only" or "off". Decides the root if nothing has yet."""
    data_dir()
    return _root_state


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
    # Whether the three opening questions have been answered. False on a fresh
    # install; set once, whether they are answered or skipped, because asking
    # twice is worse than not asking.
    "first_run_done": False,
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
    # Off: a link that failed and then downloaded stays on the Failed page
    # with "downloaded later" beside it. On, the row is cleared away and the
    # page becomes a list of what is still outstanding. Off by default, because
    # a page that promises to keep everything has to keep everything until
    # somebody says otherwise.
    "failed_clear_on_success": False,
    # On: leave a few seconds between two Instagram downloads. It is the one
    # site with a published request budget, and sharing a dozen reels at once
    # is what runs into it. Nothing else is slowed, and the cooldown after a
    # site actually refuses is separate - that one is not optional.
    "pace_sites": True,
    "engine_channel": "stable",      # stable | nightly
    # On: fetch a newer engine when one is published, rather than only
    # reporting that one exists. Checking without fetching is what left
    # installs running an engine months old - the single most common reason a
    # site "stops working" - because the fetch was a button nobody knew about.
    # It never runs while anything is downloading; see api_check_engine.
    "engine_auto": True,
    # On: when the engine is refused, let Riplox try its own way in. It only
    # ever runs on a link that has already failed, so the cost of leaving it on
    # is nothing, and the day the engine is walled it is the whole difference.
    "second_door": True,
    # On. It is what answers YouTube's "prove you are not a bot", and a
    # machine found with it switched off was hitting that wall repeatedly.
    # ⚠️ The helper is a separate 44 MB download: this flag being true does not
    # install it - ensure_running() returns "" and a download still works
    # without it. app.py fetches it in the background when it is on and
    # missing, which is what makes the default mean anything.
    "potoken": True,
    # On: pacing costs a second or two and is the difference between YouTube
    # answering and YouTube asking to confirm you are not a bot.
    "polite_mode": True,
    # On: H.264 plays in Windows' own player and on a phone. AV1 is smaller and
    # sharper and opens to a black screen, which is the bug that was reported.
    "prefer_h264": True,
    "allow_ai_upscale": False,       # take YouTube's AI-enlarged versions too
    "write_subs": False,             # save subtitles alongside the video
    # Which kind: both | real | auto. A video can carry subtitles somebody
    # wrote and subtitles a machine transcribed, and they are not the same
    # thing - the written ones have punctuation and the speaker's own words,
    # the machine ones have neither and exist on almost every video. Asking
    # for both, which is all Riplox could do until now, means a video with
    # real subtitles quietly gets a worse second copy alongside them.
    # "both" is the default because it is what every previous version did.
    "sub_kind": "both",
    "sub_langs": "en",               # which languages, yt-dlp syntax
    "embed_subs": False,             # put them inside the file instead
    "embed_chapters": False,         # chapter marks players can jump between
    "sponsorblock": False,           # cut sponsor segments out of YouTube
    "skip_existing": False,          # remember what has been downloaded
    # Sixteen pieces of the same file at once. This is where the real speed
    # comes from on fragmented video, and it is also why aria2c was not needed.
    # ⚠️ It was four, and four is where the repeated https failures were found:
    # on a second machine, raising this alongside the engine and the helper was
    # what made the downloads run. Higher looks faster on a fast line and
    # starts being refused on a slow one, so this is a ceiling rather than a
    # promise - the setting is still there for anyone it does not suit.
    "fragments": 16,
    "speed_limit": 0,                # KB/s ceiling; 0 means no limit
    # Empty means a direct connection. A site that answers a different line
    # but not this one is the case this exists for - and it is not rare: a
    # TikTok wall here refused every trick for a week and was never about the
    # request at all. yt-dlp understands http, https, socks4, socks5 and
    # socks5h; Riplox's own route understands the first two. See _base_args.
    "proxy": "",
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
    # Notifications. One master switch and one per kind, because "tell me when
    # a download fails" and "tell me every time my phone sends a link" are
    # different appetites - and being unable to turn off only the noisy one is
    # how people end up turning all of them off and missing the failures.
    "notify": True,                  # master: off means silence, whatever else says
    "notify_sent": True,             # a link arrived from a phone or browser
    "notify_done": True,             # a download finished
    "notify_failed": True,           # a download failed
    "notify_watch": True,            # a watched channel has something new
    # Off: the settings screen opens as a short list, and the rows most people
    # never need stay one tick away rather than in the way. A search still
    # finds them while hidden, so nothing becomes unreachable.
    "show_advanced": False,
    "auto_paste": True,              # watch clipboard for links
    "auto_download": False,          # queue a copied link without asking
    # Which sites instant download is allowed to act on. Empty means all of
    # them, which is what it did before this existed - and that is the one
    # setting combination worth being careful about: with both switches on,
    # every link copied anywhere gets downloaded, including one being copied
    # to send to somebody. Named in the same words site_of() produces, so a
    # rule here can actually match a link.
    "clipboard_sites": [],
    # The few "More options" choices that mean the same thing on the next
    # link - audio language, subtitle language, player client, cookies off.
    # Never a format id or a file name: those belong to one video, and
    # reusing them silently picks something nobody asked for.
    "last_opts": {},
    "hotkey": True,                  # Ctrl+Shift+D from anywhere
    "hotkey_combo": "",              # empty means "pick one for me"
    "write_thumbnail": False,
    "theme": "auto",                 # auto | light | dark

    # --- Following channels ---------------------------------------------
    "watch": False,                  # master switch for the checks
    # Not a preference - a record that the warning about repeated automated
    # requests was read. The screen shows it until this is true.
    "watch_ack": False,
    "watch_hours": 12,               # kept only so older files still read
    # How often one item may be checked, in minutes. Something with a
    # published feed can be asked often; anything the engine has to fetch
    # keeps a floor of its own whatever this says. WATCH_MINUTES below is the
    # list this has to be one of.
    "watch_minutes": 60,
    # Downloading on its own. Off, and it stays off until it is turned on
    # here AND on the channel itself - two switches, because "it downloaded
    # something I did not ask for" is the one complaint this must never earn.
    "watch_auto": False,

    # --- Send to Riplox -------------------------------------------------
    "sharing": False,                # master switch for the phone channel
    "share_lan_only": False,         # refuse the relay; home network only
    "share_approve": False,          # hold every incoming link for Approve
    "share_relay": DEFAULT_RELAY,    # which relay to dial
    # Keys and paired devices live in their own file, never in settings.json,
    # so a settings backup can never carry someone's pairing to another PC.
}

# The intervals following may be set to, in minutes. Here rather than in
# watch.py because load_settings has to read an older file's hours against it,
# and watch.py imports this module rather than the other way round.
WATCH_MINUTES = (15, 60, 360, 1440)

_settings_lock = threading.Lock()


def settings_file() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    saved = {}
    try:
        with open(settings_file(), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
            s.update(saved)
    except (OSError, ValueError):
        pass

    # Somebody already using Riplox is not on their first run, whatever a flag
    # added later says. A settings file that predates the flag means the three
    # opening questions were answered by using the app, and asking them on an
    # upgrade would be the app forgetting who it is talking to.
    #
    # Asked of the saved file, not of `s` - the defaults have already put the
    # key there, so `"first_run_done" not in s` is never true.
    if saved and "first_run_done" not in saved:
        s["first_run_done"] = True

    # The same trap, and for the same reason: following used to be set in
    # hours, and the default above has already put the minutes key into `s`,
    # so asking `s` would never see that the file predates it. Somebody who
    # chose "every 24 hours" must not quietly start being checked hourly.
    # Their choice is honoured as closely as the list still allows.
    if saved and "watch_minutes" not in saved and "watch_hours" in saved:
        try:
            wanted = int(saved.get("watch_hours") or 0) * 60
        except (TypeError, ValueError):
            wanted = 0
        fits = [m for m in WATCH_MINUTES if m <= wanted]
        s["watch_minutes"] = max(fits) if fits else min(WATCH_MINUTES)
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
# Downloads that failed
# --------------------------------------------------------------------------
# The queue is a working surface: it is cleared, and it does not survive a
# restart intact. That is right for what is running and wrong for what failed -
# a link that did not download is the one thing worth still having tomorrow,
# and until now it was the one thing that disappeared.
#
# So failures are written down here instead, and nothing removes them on
# anyone's behalf. There is no age limit and no count limit: this list shrinks
# when the person looking at it decides it should, and at no other time.
#
# The one thing that is not kept twice is the same link failing again - that
# updates the entry it already has, with a count and a fresh time, because
# forty rows of one stubborn link is not a record of anything.

_failed_lock = threading.Lock()

# The tail of the log, which is where the reason is. Enough to explain a
# failure a week later, small enough that a thousand of them is still a file
# that opens instantly.
FAILED_LOG_KEEP = 4000


def failed_file() -> Path:
    return data_dir() / "failed.json"


def load_failed() -> list:
    try:
        with open(failed_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write_failed(items: list) -> None:
    tmp = failed_file().with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(items, fh, indent=1)
        tmp.replace(failed_file())
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _failed_key(url: str, quality: str) -> str:
    return f"{url}\n{quality}"


def record_failure(entry: dict) -> None:
    """
    Remember one download that did not happen.

    The same link at a different quality is a different attempt and gets its
    own row: "1080p failed, 720p worked" is a useful thing to be able to see.
    """
    key = _failed_key(entry.get("url", ""), entry.get("quality", ""))
    now = time.time()
    with _failed_lock:
        items = load_failed()
        for item in items:
            if _failed_key(item.get("url", ""), item.get("quality", "")) != key:
                continue
            item["tries"] = int(item.get("tries") or 1) + 1
            item["last"] = now
            item["error"] = entry.get("error", "")
            item["log"] = entry.get("log", "")
            item["title"] = entry.get("title") or item.get("title", "")
            item["thumbnail"] = entry.get("thumbnail") or item.get("thumbnail", "")
            # It failed again, so whatever it is, it is not fixed now.
            item.pop("fixed", None)
            _write_failed(items)
            return

        entry = dict(entry)
        entry.setdefault("id", uuid.uuid4().hex[:12])
        entry["when"] = now
        entry["last"] = now
        entry["tries"] = 1
        items.insert(0, entry)
        _write_failed(items)


_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def _bytes_of(size: str) -> int:
    """"412 MB" as a number. The library stores sizes the way it shows them."""
    try:
        number, unit = str(size).split()
        return int(float(number) * _SIZE_UNITS[unit.upper()])
    except (ValueError, KeyError, AttributeError):
        return 0


def _pretty(total: int) -> str:
    for unit in ("TB", "GB", "MB", "KB"):
        step = _SIZE_UNITS[unit]
        if total >= step:
            return f"{total / step:.1f} {unit}"
    return f"{total} B"


def insights() -> dict:
    """
    What the library already knows, counted.

    Reads history.json and failed.json and nothing else - no request, no new
    file, no new permission. Every number here is one the app already had and
    had never shown back.
    """
    import time

    history = load_history()
    failed = load_failed()

    done_by_site: dict = {}
    bytes_by_site: dict = {}
    total_bytes = 0
    week = 0
    week_bytes = 0
    earliest = ""
    cutoff = time.time() - 7 * 86400

    for item in history:
        site = site_of(item.get("url", "")) or "elsewhere"
        done_by_site[site] = done_by_site.get(site, 0) + 1
        size = _bytes_of(item.get("size", ""))
        bytes_by_site[site] = bytes_by_site.get(site, 0) + size
        total_bytes += size

        when = str(item.get("when", ""))
        if when and (not earliest or when < earliest):
            earliest = when
        try:
            stamp = time.mktime(time.strptime(when[:19], "%Y-%m-%dT%H:%M:%S"))
            if stamp >= cutoff:
                week += 1
                week_bytes += size
        except (ValueError, TypeError):
            pass

    # A failure that was later fixed still failed the first time, which is the
    # thing being measured: how often this site works first go.
    failed_by_site: dict = {}
    for item in failed:
        site = site_of(item.get("url", "")) or "elsewhere"
        failed_by_site[site] = failed_by_site.get(site, 0) + 1

    sites = []
    for site in sorted(set(done_by_site) | set(failed_by_site),
                       key=lambda s: -(done_by_site.get(s, 0))):
        ok = done_by_site.get(site, 0)
        bad = failed_by_site.get(site, 0)
        sites.append({
            "site": site,
            "done": ok,
            "failed": bad,
            "size": _pretty(bytes_by_site.get(site, 0)),
            # Out of everything tried on that site, not out of what worked.
            "rate": round(100.0 * bad / (ok + bad), 1) if (ok + bad) else 0.0,
        })

    total_failed = sum(failed_by_site.values())
    tried = len(history) + total_failed
    top = sites[0]["site"] if sites and sites[0]["done"] else ""

    # The library keeps the last HISTORY_LIMIT downloads and drops the rest, so
    # "since <date>" would read as "this is when you started" when it actually
    # means "this is the oldest one still kept". Said plainly instead - a
    # number that quietly means something else is the thing this app treats as
    # a real bug, not a rounding error.
    capped = len(history) >= HISTORY_LIMIT

    return {
        "ok": True,
        "files": len(history),
        "capped": capped,
        "kept": HISTORY_LIMIT,
        "size": _pretty(total_bytes),
        "since": earliest[:10],
        "week": week,
        "week_size": _pretty(week_bytes),
        "first_try": round(100.0 * len(history) / tried, 1) if tried else 0.0,
        "first_try_of": tried,
        "failed": total_failed,
        "top": top,
        "top_share": round(100.0 * sites[0]["done"] / len(history), 1)
                     if (sites and len(history)) else 0.0,
        "sites": sites[:8],
    }


def note_failure_fixed(url: str, quality: str) -> None:
    """
    A remembered failure has now downloaded. Mark it, or clear it away.

    Marking is the default, and deliberately so: taking a row away is this
    program deciding something on that list should go, which is the one thing
    the list promises not to do. A row that says it worked in the end is worth
    seeing too - it is the difference between "that site is broken" and "that
    day was bad".

    The setting is for people who want the page to be a to-do list rather than
    a record. Their call, made once, in plain sight on that page.
    """
    key = _failed_key(url, quality)
    tidy = bool(load_settings().get("failed_clear_on_success"))
    with _failed_lock:
        items = load_failed()
        if tidy:
            kept = [i for i in items
                    if _failed_key(i.get("url", ""), i.get("quality", "")) != key]
            if len(kept) != len(items):
                _write_failed(kept)
            return

        hit = False
        for item in items:
            if _failed_key(item.get("url", ""), item.get("quality", "")) == key:
                if not item.get("fixed"):
                    item["fixed"] = time.time()
                    hit = True
        if hit:
            _write_failed(items)


def forget_failure(entry_id: str) -> bool:
    with _failed_lock:
        items = load_failed()
        kept = [i for i in items if i.get("id") != entry_id]
        if len(kept) == len(items):
            return False
        _write_failed(kept)
        return True


def clear_failed() -> None:
    with _failed_lock:
        try:
            failed_file().unlink()
        except OSError:
            _write_failed([])


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
            # A file the user picked belongs to no account of ours, so there
            # is nothing here to rest afterwards.
            return path, True, 0
        # Files are set but none of them knows this site: fall through to the
        # sign-in rather than sending nothing, which is what a user who has
        # both would expect.

    if (settings or {}).get("cookies_signin", True):
        try:
            import cookies as cookie_store
            # An account that is resting after a refusal is not offered, so a
            # spare takes the work instead of the whole site waiting.
            path, account = cookie_store.materialize_for(
                url, skip=resting_accounts(site_of(url)))
        except Exception:
            path, account = None, 0   # cookie trouble never blocks a download
        if path:
            return path, True, account

    return None, False, 0


def close_cookies(path, temporary: bool) -> None:
    if not (path and temporary):
        return
    try:
        import cookies as cookie_store
        cookie_store.release(path)
    except Exception:
        pass


# The proxy schemes yt-dlp will accept. Anything else is refused at the point
# it is typed rather than turned into a download that fails an hour later with
# a message about the site.
PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")

# Of those, the two that Riplox's own route can also speak. urllib handles
# http and https proxies by itself; SOCKS needs a library that is not bundled,
# and adding one to reach a fallback route is not a trade worth making.
DIRECT_PROXY_SCHEMES = ("http", "https")


def clean_proxy(raw) -> str:
    """
    The proxy address as it will be used, or "" for a direct connection.

    Returns "" for anything unusable rather than raising: a settings file
    edited by hand should not stop the app from starting. What the user typed
    is checked where they typed it - see check_proxy.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    scheme, sep, rest = text.partition("://")
    if not sep or scheme.lower() not in PROXY_SCHEMES or not rest.strip("/"):
        return ""
    return f"{scheme.lower()}://{rest}"


def check_proxy(raw) -> str:
    """What is wrong with this proxy address, in a sentence, or ""."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        return ("A proxy address needs to start with how to reach it - "
                "http://, https://, socks5:// - then the host and port. "
                f"For example http://{text.split('/')[0] or '127.0.0.1:8080'}")
    scheme = text.split("://", 1)[0].lower()
    if scheme not in PROXY_SCHEMES:
        return (f"{scheme}:// is not a kind of proxy the downloader can use. "
                f"Use one of: {', '.join(PROXY_SCHEMES)}.")
    if not text.split("://", 1)[1].strip("/"):
        return "The proxy address is missing its host and port."
    return ""


def _base_args(settings: dict, cookie_path=None, batch: bool = False,
               polite: bool = True) -> list:
    exe = ytdlp_path()
    if exe is None:
        raise EngineMissing("yt-dlp binary not found")

    # ⚠️ --encoding utf-8 is not a nicety. Without it yt-dlp writes its output
    # in the console codepage - cp1252 here - while we read the pipe as utf-8.
    # Every title with a curly quote, an accent or an emoji came back mangled,
    # and the "Destination:" line is where Riplox learns the path it saved. So
    # the LIBRARY recorded a name that did not exist: the file on disk had
    # "Wahdi Ba’dak" and history had "Wahdi Ba<?>dak", and Play quietly opened
    # the folder instead of the video. 56 of 244 entries on one real machine.
    #
    # Measured, because the obvious fix is the wrong one: PYTHONIOENCODING in
    # the child's environment does NOTHING here - yt-dlp is frozen and sets up
    # its own stdout. Only this flag changes the bytes (0x91 -> e2 80 98).
    args = [str(exe), "--no-warnings", "--ignore-config", "--no-colors",
            "--encoding", "utf-8"]

    ff = ffmpeg_path()
    if ff is not None:
        args += ["--ffmpeg-location", str(ff.parent)]

    # Without a JavaScript runtime YouTube's newer streaming path hands back
    # formats with no URL at all, so this is not optional any more.
    qjs = qjs_path()
    if qjs is not None:
        args += ["--js-runtimes", f"quickjs:{qjs}"]

    proxy = clean_proxy((settings or {}).get("proxy"))
    if proxy:
        args += ["--proxy", proxy]

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

    # `polite=False` is for the one read a person is sitting and waiting for.
    # The pause exists for bursts - a playlist being grabbed, a timer checking
    # channels - and a channel listing pays it once per page of results, which
    # measured 86.7s against 38.2s on the same channel. Reading one link is
    # not a burst, and the cost lands entirely on someone watching a spinner.
    if polite and (settings or {}).get("polite_mode", True):
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


def _resume_key(url: str) -> str:
    """
    What has to match for a half-finished .part to be worth continuing.

    Host and path, never the query. A signed CDN address carries its signature
    and expiry as query parameters, so including them would mean no download
    ever resumed; leaving out the path would mean a different encode resumed
    over the last one. The path is what names the file at the other end.
    """
    parts = urlsplit(url or "")
    return f"{parts.hostname or ''}{parts.path or ''}"


def pull_to_file(url: str, part: Path, headers: dict, deadline: float,
                 on_progress=None, timed_out: str = "", proxy: str = "") -> int:
    """
    Fetch a URL into a .part file, continuing one that is already there.

    Shared by the engine update and by the direct extractors in doors.py: both
    want the same three things - chunks so a percentage can be shown, a Range
    header so a dropped connection does not start the file over, and a message
    a person can read when it gives up. `on_progress(done, total)` is called
    per chunk. Raises OSError with that readable message.

    The .part is left behind on purpose; the next attempt continues from it -
    but only when the next attempt is the same file. See _resume_key.
    """
    have = part.stat().st_size if part.exists() else 0
    headers = dict(headers or {})
    headers.setdefault("User-Agent", "Riplox")

    # A .part only belongs to the address that wrote it. TikTok signs a fresh
    # address every time it is asked and can offer its encodes in a different
    # order, so a retry is not guaranteed to be the same file - and appending
    # the second file to half of the first produces something of exactly the
    # right length that plays as far as the join and then falls apart, with
    # nothing anywhere saying so. Measured: two 200,000-byte encodes spliced
    # into a 200,000-byte file that matched neither.
    stamp = part.with_suffix(part.suffix + ".from")
    key = _resume_key(url)
    if have:
        try:
            if stamp.read_text("utf-8").strip() != key:
                part.unlink(missing_ok=True)
                have = 0
        except OSError:
            # No record of what wrote it - the safe reading is "not ours".
            part.unlink(missing_ok=True)
            have = 0
    if not have:
        try:
            stamp.write_text(key, encoding="utf-8")
        except OSError:
            pass                            # resuming is a nicety, not the job

    if have:
        headers["Range"] = f"bytes={have}-"

    # The bytes have to travel the same way the address was fetched. Resolving
    # a link through a proxy and then pulling the video around it is the leak
    # nobody would notice: the download works, and the site was handed the
    # address the user was hiding.
    request = urllib.request.Request(url, headers=headers)
    fetch = urllib.request.urlopen
    if proxy:
        fetch = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})).open
    with fetch(request, timeout=_ENGINE_READ_TIMEOUT) as response:
        # A server that ignores Range answers 200 with the whole file. Appending
        # to what is already there would quietly corrupt the file, so start over.
        if have and getattr(response, "status", 200) != 206:
            have = 0
            part.unlink(missing_ok=True)

        length = int(response.headers.get("Content-Length") or 0)
        total = have + length if length else 0
        if on_progress:
            on_progress(have, total)

        with open(part, "ab" if have else "wb") as out:
            while True:
                if time.monotonic() > deadline:
                    raise OSError(timed_out or
                                  "Gave up waiting - the connection is too slow "
                                  "or keeps dropping. What arrived is kept, so "
                                  "trying again carries on from there.")
                chunk = response.read(_ENGINE_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                have += len(chunk)
                if on_progress:
                    on_progress(have, total)

    if total and have < total:
        raise OSError("The connection dropped part-way through.")

    # Finished, so there is nothing left to resume and no note to keep.
    try:
        stamp.unlink(missing_ok=True)
    except OSError:
        pass
    return have


def _download_engine_zip(url: str, part: Path, deadline: float) -> int:
    """One attempt at the engine zip, reporting into the update button."""
    def say(done, total):
        percent = (done / total * 100.0) if total else 0.0
        _engine_say(total=total, bytes=done, percent=round(percent, 1),
                    message=(f"Downloading {percent:.0f}%" if total
                             else f"Downloading {human_bytes(done)}"))

    # Through the proxy as well. Somebody who set one did so because this
    # connection cannot reach something, and the engine update is a download
    # like any other - leaving it out means the one setting meant to fix a
    # blocked connection does not apply to the file that fixes broken sites.
    return pull_to_file(
        url, part, {"User-Agent": "Riplox"}, deadline, on_progress=say,
        proxy=clean_proxy(load_settings().get("proxy")),
        timed_out="Gave up after ten minutes - the connection is too slow or "
                  "keeps dropping. What arrived is kept, so pressing update "
                  "again carries on from there.")


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
    if portable_state() == "on":
        return False              # never claims a key an installed copy left
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
    if portable_state() == "on":
        return {"ok": False, "on": False,
                "message": "A portable Riplox writes nothing outside its own "
                           "folder, and starting with Windows needs a registry "
                           "entry. Install Riplox to use this."}

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
    # Deliberately not called "best" anything. It is not better for watching -
    # it is a bigger, less playable file that survives being uploaded again,
    # and the name has to carry that or it will be picked by mistake.
    "max": "Max",
    "4320": "8K · 4320p",
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
#
# ⚠️ The first five after "" are the ones measured (1 Sep 2026) to offer every
# format with no proof-of-origin token at all; the rest are kept because the
# whole point of this control is working around a refusal, and a refused
# download at 360p still beats no download. What changed is that the dropdown
# now SAYS which is which - four of the six it used to list stop at 360p or
# 180p however much quality was asked for, and it said nothing.
PLAYER_CLIENTS = ("", "tv_embedded", "visionos", "web_embedded",
                  "android_music", "ios_music",
                  "tv_simply", "mweb",
                  "web_safari", "android_vr", "ios", "web")

_OPT_KEYS = ("format_id", "audio_lang", "sub_langs", "outtmpl", "dest_dir",
             "player_client", "no_cookies", "max_mb",
             # One download's shape, not a setting: wanting only the subtitles
             # of this video says nothing about the next one.
             "subs_only", "live_from_start", "thumb_all",
             # The text under the video, saved beside it. Asked for on the
             # biggest competitor and never answered there.
             "write_desc",
             # Which cover picture to keep, chosen from the ones the site
             # offers. An address, so it is checked like one - see safe_image.
             "thumb_url",
             # The chapters ticked on the list, and the tick that means all of
             # them. Two keys rather than one, because "all" is a single
             # pattern to the engine and a list of two hundred titles is not.
             "chapters", "chapters_all", "parts_expected",
             # The most-replayed moments to cut out, as ranges in seconds.
             "clips")


def safe_image(raw: str) -> str:
    """
    An https image address Riplox is willing to fetch, or "".

    These arrive from the page after a round trip through the browser, and
    whatever comes back is fetched by the app itself - so an address pointing
    at 127.0.0.1 would have Riplox make a request to its own API on somebody
    else's behalf. Scheme and host are checked for that reason rather than for
    tidiness. The same argument, and the same answer, as doors._address_ok.
    """
    text = str(raw or "").strip()
    if not text or len(text) > 1000:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme != "https":
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost"):
        return ""
    # A bare address is never a thumbnail CDN, and is how everything
    # interesting on this machine and its network gets reached.
    if re.fullmatch(r"[\d.]+", host) or ":" in host:
        return ""
    return text


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
        # Anything empty means "not set", whatever shape it arrives in. The
        # narrower check this replaces let an empty list through, and a
        # yes/no option built from one came out as yes.
        if not value:
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
        elif key in ("no_cookies", "subs_only", "live_from_start", "thumb_all",
                     "chapters_all", "write_desc"):
            out[key] = True
        elif key == "chapters":
            # Chapter titles ticked on the list. Each becomes an anchored
            # regex where it is used, so nothing here trusts their contents;
            # what this has to stop is a value that is not a list of titles.
            # How long the whole selection is gets checked by the caller,
            # which can say so out loud - dropping it quietly here would turn
            # "these three chapters" into "the entire video".
            titles = []
            for item in value if isinstance(value, list) else []:
                text = str(item or "").strip()
                if text and text not in titles:
                    titles.append(text)
            if titles:
                out[key] = titles[:500]
        elif key == "parts_expected":
            # How many files the screen believes it asked for. It knows things
            # this does not - that two chapters share a title and so arrive as
            # two files from one pattern - so it says the number rather than
            # having it guessed here.
            try:
                out[key] = max(1, min(int(value), 500))
            except (TypeError, ValueError):
                pass
        elif key == "clips":
            # Time ranges in seconds, as the screen worked them out. Checked
            # rather than trusted: these go straight onto a command line, and
            # a pair that is not two numbers would become a pattern yt-dlp
            # reads as a chapter name.
            spans = []
            for span in value if isinstance(value, list) else []:
                if not isinstance(span, dict):
                    continue
                try:
                    start, end = int(span.get("start")), int(span.get("end"))
                except (TypeError, ValueError):
                    continue
                if 0 <= start < end:
                    spans.append({"start": start, "end": end})
            if spans:
                out[key] = spans[:50]
        elif key == "thumb_url":
            address = safe_image(value)
            if address:
                out[key] = address
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

    # ⚠ Every quality that is not one of these is a NUMBER, and goes straight
    # into [height<=?N]. A new name added without this guard would build
    # "[height<=?max]" and break the download outright.
    uncapped = quality in ("best", "max")

    if not ff:
        # Without ffmpeg we can only take streams that are already muxed, and
        # a muxed stream carries whatever audio it was made with - so there is
        # nothing to choose between here.
        if uncapped:
            return ["-f", f"best{sr}[ext=mp4]/best{sr}/best[ext=mp4]/best"]
        cap = f"[height<=?{quality}]"
        return ["-f", f"best{cap}{sr}[ext=mp4]/best{cap}{sr}/"
                      f"best{cap}[ext=mp4]/best{cap}/best"]

    cap = "" if uncapped else f"[height<=?{quality}]"

    # Built as a list and de-duplicated rather than concatenated, because with
    # no audio language chosen several of these strings come out identical -
    # and this selector is now shown to the user in More options, where a
    # repeated branch just looks like a mistake.
    branches = []

    def add(branch):
        if branch not in branches:
            branches.append(branch)

    # ⚠ H264 used to be a FILTER here, which is why "Best available" returned
    # 1080p while 4K sat on the shelf: on YouTube h264 stops at 1080p and 4K
    # exists only as VP9 or AV1, so filtering for h264 threw the resolution
    # away. It is a tie-break in the sort below now - h264 still wins wherever
    # h264 can reach the same height, and nothing is lost when it cannot.
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

    # Highest resolution first, always. Then:
    #
    #  * "max" wants the best SOURCE for uploading again, so it takes the
    #    fattest video stream - VP9 at 13.4 Mbps carries more into a re-encode
    #    than AV1 at 9.0 - and AAC audio, because Opus inside an MP4 is unusual
    #    and some sites refuse it, while the two bitrates are equal in practice.
    #  * otherwise h264 wins ties, because it plays in Windows Media Player,
    #    WhatsApp and every phone with no codec to install.
    #
    # ⚠ A codec CHAIN does not work: "res,vcodec:h264:av01:vp9" was measured and
    # the chain is ignored. Only one codec preference is honoured.
    if quality == "max":
        order = "res,vbr,abr"
    else:
        order = "res,vcodec:h264,acodec:aac" if safe else "res"

    return ["-f", "/".join(branches), "-S", order,
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

# The extension's listing. Here rather than in app.py because this is the file
# that owns the addresses, and because a URL the app hands to a browser has to
# be on the list below - which is what the Browser extension button fell over:
# it asked, and was refused, and nothing said so.
STORE_PAGE = ("https://chromewebstore.google.com/detail/"
              "riplox-%E2%80%94-send-to-your-dow/hacbllnggmnnajhobdgcklhdmaoddnnh")

# The only addresses Riplox will ever hand to the real browser. An allowlist
# rather than a check on the string, so a page that talked its way past the
# token still cannot use the app as a launcher for anything it likes.
OPENABLE = (RELEASES_PAGE, HOME_PAGE, ISSUES_PAGE, STORE_PAGE)


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


# yt-dlp takes --download-sections as a REGEX, never as a literal title. A
# chapter called "C++ (part 1)" is not a string to it, it is a pattern - and
# a broken one: measured on the bundled 2026.07.04 binary it refuses to start,
# with `invalid --download-sections regex "C++ (part 1)" - multiple repeat at
# position 2`. The titles that compile are the dangerous ones, because they
# compile into a pattern that is not the title the user ticked.
def chapter_regex(title: str) -> str:
    """One chapter title as a pattern matching that title and nothing else."""
    title = (title or "").strip()
    if not title:
        return ""
    # Anchoring is not tidiness here, it is the point. yt-dlp searches the
    # pattern anywhere inside a chapter title, so an unanchored "Data Types"
    # also selects "Data Types (List, Tuple, Set, Dictionary)" - measured on
    # a real video: one ticked box, two sections back, the second twenty
    # minutes long and never asked for. ^...$ is also what stops a title that
    # begins with * from being read as a time range instead of a chapter.
    return "^" + re.escape(title) + "$"


def chapter_args(titles: list, every: bool = False,
                 exact: bool = False) -> list:
    """--download-sections once per wanted chapter, or nothing."""
    # Every chapter is one pattern, not two hundred. A title runs to eighty
    # characters once escaped, and Windows takes 32767 in a whole command -
    # so "split all of it" on a long video is the one selection that could
    # not fit. It still matches on the title, so a video with no chapters
    # selects nothing rather than quietly selecting the whole video.
    if every:
        return ["--no-quiet", "--download-sections", ".*"] + _exact_cut(exact)
    args, seen = [], set()
    for title in titles or []:
        pattern = chapter_regex(title)
        # A chapter ticked twice would be fetched and written twice.
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        args += ["--download-sections", pattern]
    if not args:
        return []
    # Sections are cut by ffmpeg, and --print turns --quiet on implicitly,
    # which silences ffmpeg completely - the same silence that left the queue
    # reading 0.0% for three and a half minutes on trimmed downloads.
    return ["--no-quiet"] + args + _exact_cut(exact)


def needs_ffmpeg(settings: dict, quality: str) -> list:
    """
    Which switched-on options cannot be honoured without ffmpeg.

    Every one of these is a post-processing step: yt-dlp accepts the flag,
    downloads the video, and then quietly skips the step it cannot run. The
    result is a file that is missing exactly the thing the user turned on,
    with nothing anywhere saying so - which is this app's worst failure mode,
    not a cosmetic one. Naming them is what lets the UI say it out loud.
    """
    if has_ffmpeg():
        return []

    audio_only = quality == "mp3"
    wanted = []
    if settings.get("write_subs") and not audio_only:
        # The subtitle file itself still arrives; converting and embedding it
        # is what needs ffmpeg, so only those are claimed as lost.
        if settings.get("embed_subs"):
            wanted.append("subtitles inside the video")
    if settings.get("embed_chapters") and not audio_only:
        wanted.append("chapter marks")
    if settings.get("sponsorblock"):
        wanted.append("skipping sponsor segments")
    return wanted


def extra_args(settings: dict, quality: str, trimmed: bool = False) -> list:
    """
    Everything optional the user has switched on.

    Options that need ffmpeg are left out entirely when it is missing, rather
    than sent and silently ignored. Sending them costs nothing visible and is
    worse than useless: it makes the command look like it did what was asked.
    """
    args = []
    audio_only = quality == "mp3"
    have_ff = has_ffmpeg()

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
        kind = str(settings.get("sub_kind") or "both").lower()
        if kind not in ("both", "real", "auto"):
            kind = "both"
        if kind != "auto":
            args.append("--write-subs")
        if kind != "real":
            args.append("--write-auto-subs")
        args += ["--sub-langs", langs, "--sub-format", "srt/vtt/best"]
        # Converting to srt and embedding are both ffmpeg's work. Without it
        # the subtitle file still lands next to the video in its original
        # format, which is the useful half and arrives either way.
        if have_ff:
            args += ["--convert-subs", "srt"]
            if settings.get("embed_subs"):
                args.append("--embed-subs")

    # Not on a piece of a video. The marks describe the WHOLE video, and
    # embedding them in a cut writes every one of them into it - measured on a
    # 17-second chapter of a 20-minute upload: 63 chapter marks, the last of
    # them ending at 1214 seconds. Players read that track and show the length
    # of the original, so a seventeen-second file reports twenty minutes.
    if settings.get("embed_chapters") and not audio_only and have_ff \
            and not trimmed:
        args.append("--embed-chapters")

    if settings.get("sponsorblock") and have_ff:
        args += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]

    # The archive remembers video ids, not files. A clip of a video you have
    # already saved in full is a different file the user is asking for on
    # purpose, so trimming ignores the archive rather than silently refusing.
    # ⚠ The archive remembers video ids, not files - so a video already saved
    # at 1080p would be skipped when asked for again at the highest quality,
    # and the user would get nothing with no reason shown. Same carve-out, same
    # reason, as trimming above: it is a different file, asked for on purpose.
    if settings.get("skip_existing") and not trimmed and quality != "max":
        args += ["--download-archive", str(archive_file())]

    return args


# How much of a long list the first read asks for. A channel is walked a page
# at a time and each page is a request, so "all of it" is minutes: the same
# channel measured 86.7s whole against 3.3s for the first hundred. Nobody
# reads past the first screen before pressing something, and the rest is one
# button away - so the wait belongs to the person who asked for all of it.
ANALYZE_LIMIT = 100


def analyze(url: str, settings: dict, limit: int = ANALYZE_LIMIT) -> dict:
    """
    Inspect a URL without downloading.
    Returns a single video dict, or a playlist dict with entries.

    `limit` caps how many entries a playlist or channel comes back with; pass
    0 for all of them. A capped result says so, so the screen can offer the
    rest rather than quietly showing a hundred of eight hundred.
    """
    # The same ladder a download climbs. Reading a link failed on a passing
    # bot check and stopped there, while queueing the very same link retried
    # and went through - and the message shown even said the retries were
    # spent, which they were not. Pasting is the first thing anyone does, so
    # it is the worst place to be the one path that gives up immediately.
    plans = _RETRY_CLIENTS if _is_youtube(url) else _PLAIN_RETRIES
    out = None

    cookie_path, temp_cookie, _account = open_cookies(settings, url)
    try:
        for index, client in enumerate(plans):
            # Somebody is watching this one happen, so it does not pay the
            # pause meant for bursts.
            args = _base_args(settings, cookie_path, polite=False)
            if client:
                args += ["--extractor-args", f"youtube:player_client={client}"]
            args += ["-J", "--flat-playlist", "--no-progress"]
            if limit and limit > 0:
                args += ["--playlist-end", str(int(limit))]
            args += [url]

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
        raise RuntimeError(
            _clean_error(out.stderr if out is not None else "", during="reading"))

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

        # A list that came back exactly as long as the cap is a list that was
        # cut off - there is no way to tell from here whether it stopped at a
        # hundred or happens to be a hundred, so the screen is told "there may
        # be more" rather than either lie. `playlist_count`, where the site
        # gives one, settles it properly.
        total = info.get("playlist_count")
        more = bool(limit and len(entries) >= limit
                    and (not isinstance(total, int) or total > len(entries)))

        return {
            "kind": "playlist",
            "title": info.get("title") or "Playlist",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "count": len(entries),
            "more": more,
            "total": total if isinstance(total, int) else 0,
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
    heat = _heatmap_rows(info)
    peaks = heatmap_peaks(heat)
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
        "sizes": rungs["sizes"],
        # Live now, as opposed to a finished stream. Only a live one can be
        # joined from the beginning, so this is what decides whether that
        # choice is offered at all - a checkbox on every ordinary video is
        # clutter on the one screen that has to stay simple.
        "is_live": bool(info.get("is_live")),
        # The video's own chapters. Read-only here: the screen can say
        # "15 chapters" and list them, which is worth having on its own
        # and is the list the ticking will hang off next.
        "chapters": _chapter_rows(info),
        # What people actually rewatched. Riplox has been fetching this on
        # every YouTube analyse since long before anything looked at it, and
        # throwing it away. An empty list is the ordinary answer, not a
        # failure - see _heatmap_rows.
        "heatmap": heat,
        "peaks": peaks,
        # The ranges each offered length would cut, worked out here rather
        # than in the browser: the screen then shows exactly what it is about
        # to ask for, and there is only one copy of the merging rule.
        "clips": {str(n): peak_clips(peaks, n, info.get("duration") or 0)
                  for n in CLIP_LENGTHS},
        # Everything below feeds "More options". The closed screen never shows
        # any of it, so it costs nothing to carry.
        "formats": _format_rows(info),
        "audio_langs": _audio_langs(info),
        "sub_langs": _sub_langs(info),
        "thumbs": _thumb_rows(info),
        # Whether there is one, not what it says. The screen only needs to
        # offer "save the description", and a description can run to thousands
        # of characters that nothing on this side would ever read.
        "has_description": bool((info.get("description") or "").strip()),
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
    started = time.time()
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

    # Counted rather than discarded in silence. A page with sixty links that
    # comes back with twelve owes the reader an account of the other
    # forty-eight, and the three reasons are genuinely different: a repeat, a
    # site we have no extractor for, and simply too many.
    read_ms = int((time.time() - started) * 1000)

    entries, seen = [], {page.rstrip("/")}
    skipped = {"duplicates": 0, "unsupported": 0, "capped": 0}
    # The links themselves, not only how many. A count can be printed; a list
    # can be looked at, which is the difference between "48 left out" and
    # being able to see that the one you wanted is among them. Capped
    # separately from the entries - a page of navigation must not send back
    # four hundred rows nobody asked for - and the counts above stay true
    # whatever this cap does.
    LEFT_CAP = 40
    left_out = {"duplicates": [], "unsupported": []}
    for raw, title in parser.found:
        raw = (raw or "").strip()
        if not raw or raw.lower().startswith(_SKIP_SCHEME):
            continue
        full = urljoin(page, raw)
        if not full.lower().startswith(("http://", "https://")):
            continue

        bare = full.split("#")[0].rstrip("/")
        if bare in seen:
            skipped["duplicates"] += 1
            if len(left_out["duplicates"]) < LEFT_CAP:
                left_out["duplicates"].append(
                    {"url": full, "title": title[:120], "site": site_of(full)})
            continue

        path = urlsplit(full).path.lower()
        site = site_of(full)
        # Worth listing if a site we know serves media from it, or if the
        # address is plainly a media file. Everything else on a page is
        # navigation.
        if not (path.endswith(_MEDIA_EXT) or site in known_sites()):
            # A page's own navigation was never a candidate, so it is not
            # something that was "left out" - counting it would put "48 from
            # sites Riplox has no reader for" under every page and make the
            # line worth ignoring. Only links that leave the site count.
            if site != site_of(page):
                skipped["unsupported"] += 1
                if len(left_out["unsupported"]) < LEFT_CAP:
                    left_out["unsupported"].append(
                        {"url": full, "title": title[:120], "site": site})
            continue

        seen.add(bare)
        entries.append({
            "url": full,
            "title": title[:120] or path.rsplit("/", 1)[-1] or site,
            "duration": None,
            "thumbnail": "",
            "timestamp": None,
            # Where it came from. site_of() has already been called to decide
            # whether to keep it at all, so this is free.
            "site": site,
        })
        if len(entries) >= _GRAB_CAP:
            skipped["capped"] = 1
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
        "skipped": skipped,
        "left_out": left_out,
        # The address that was actually read, after any redirect - which is
        # not always the one that was pasted.
        "page": page,
        "read_ms": read_ms,
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
    cookie_path, temp_cookie, _account = open_cookies(settings, url)
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
        raise RuntimeError(_clean_error(out.stderr, during="reading"))

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
        # Both identifiers, unchanged, for whoever needs to name this thing
        # somewhere else - a published feed, for one. Nothing is derived here
        # because what counts as a usable id is not this function's business.
        "id": info.get("id") or "",
        "channel_id": info.get("channel_id") or "",
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


def _thumb_rows(info: dict) -> list:
    """
    The cover pictures on offer, biggest first, for the one that is chosen.

    Sites often lead with a poor default - a frame from the first second, or a
    grey box - while carrying a good one further down the list. Until now the
    only answer was to save every one of them and sort it out in the folder.

    Deduplicated by size, because a site listing the same picture at the same
    dimensions three times turns a choice into a guessing game. Capped, so a
    site that publishes forty storyboard tiles does not become the screen.
    """
    # Sorted here rather than trusted from the site. Reversing the list the
    # site sent looked right - they generally publish smallest first - and put
    # an entry carrying no dimensions at the top, where it reads as the best
    # one on offer. Measured, not guessed: it is what the first run did.
    ordered = sorted((info.get("thumbnails") or []),
                     key=lambda t: ((t.get("width") or 0) * (t.get("height") or 0)),
                     reverse=True)

    seen = set()
    rows = []
    for entry in ordered:
        address = safe_image(entry.get("url"))
        if not address:
            continue
        width = entry.get("width") or 0
        height = entry.get("height") or 0
        shape = (width, height)
        if shape in seen:
            continue
        seen.add(shape)
        rows.append({
            "url": address,
            "width": width,
            "height": height,
            # What to call it when the site gives no dimensions, which happens
            # more often than the field suggests.
            "label": (f"{width}×{height}" if width and height
                      else str(entry.get("id") or "unsized")),
        })
        if len(rows) >= 8:
            break
    return rows


def _chapter_rows(info: dict) -> list:
    """
    The video's own chapters, as the screen needs them.

    Most videos have none, and a site can say so in two ways - by leaving the
    field out, or by setting it to null - so both have to arrive here as the
    same empty list. A chapter carrying no title is dropped rather than shown
    as a blank row: it cannot be read, and it cannot be asked for by name.

    Not capped, deliberately. Eight thumbnails out of forty is a tidier
    screen; eight chapters out of forty is the app quietly deciding which
    parts of the video exist.
    """
    entries = info.get("chapters")
    if not isinstance(entries, list):
        return []

    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        start, end = entry.get("start_time"), entry.get("end_time")
        rows.append({
            "title": title,
            # A time is shown only when there really is one. Sites have sent
            # strings and nulls here, and "0:00" invented from a missing
            # start would be a claim about the video, not a blank.
            "start": start if isinstance(start, (int, float)) else None,
            "end": end if isinstance(end, (int, float)) else None,
        })
    return rows


def written_bytes(path) -> int:
    """
    What a finished download actually left on the disk.

    A chapter download produces a folder rather than a file, and .stat() on a
    directory does not raise. It succeeds, and answers with the size of the
    directory entry - a few kilobytes - so a 500 MB folder of chapters was
    recorded, shown and counted against the allowance as about 4 KB, with
    nothing anywhere saying it was wrong. The except clause around the caller
    made it worse: it is written to swallow a failure, and there was no
    failure to swallow.
    """
    target = Path(path)
    if target.is_dir():
        return sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    return target.stat().st_size


def _heatmap_rows(info: dict) -> list:
    """
    The rewatch curve, when YouTube has one.

    ⚠️ `heatmap` does not appear in `yt-dlp --help` at all. It is not a
    documented field, so no deprecation applies to it and it can stop
    arriving the day YouTube changes the shape of its answer - with no error
    anywhere, because the field simply becomes absent. So "missing" is the
    ordinary case here, not the exceptional one, and everything above this
    has to be able to say so out loud.

    Measured on real videos: YouTube always sends exactly 100 buckets
    covering the whole video, so a bucket is a hundredth of the duration -
    2.5 seconds on a four-minute video and 73 seconds on a two-hour one. It
    is not a fixed window, and nothing here may assume one.
    """
    points = info.get("heatmap")
    if not isinstance(points, list):
        return []

    rows = []
    for point in points:
        if not isinstance(point, dict):
            continue
        start = point.get("start_time")
        end = point.get("end_time")
        value = point.get("value")
        if not all(isinstance(n, (int, float)) for n in (start, end, value)):
            continue
        if end <= start:
            continue
        rows.append({
            "start": float(start),
            "end": float(end),
            # Sent as 0-1 with the busiest bucket at 1.0. Clamped rather than
            # trusted: the graph's height is drawn straight from this.
            "value": max(0.0, min(1.0, float(value))),
        })
    return rows


def heatmap_peaks(rows: list, want: int = 5) -> list:
    """The moments people went back to, most replayed first."""
    if not rows or max(r["value"] for r in rows) <= 0:
        # A curve that is flat at zero has no moments in it. Marking five of
        # them anyway would be the app inventing a claim about the video.
        return []

    order = sorted(range(len(rows)), key=lambda i: rows[i]["value"], reverse=True)
    taken = []
    for i in order:
        if len(taken) >= want:
            break
        # One hump is several buckets wide and its shoulders are not separate
        # moments. Without this, "the five most replayed" came back as one
        # peak and the four buckets leaning against it.
        if any(abs(i - j) < 3 for j in taken):
            continue
        taken.append(i)
    return [dict(rows[i], rank=n + 1) for n, i in enumerate(taken)]


# How long a cut-out moment is. YouTube's own buckets cannot be used as clips:
# it always sends exactly 100 of them, so a bucket is a hundredth of the video
# - two and a half seconds on a four-minute upload and seventy-three on a
# two-hour one. Neither is a clip. These are lengths a person would actually
# post, and the moment sits in the middle of one.
CLIP_LENGTHS = (15, 30, 60)


def peak_clips(peaks: list, seconds: int, duration: float = 0) -> list:
    """
    Clip ranges around the most replayed moments, in order, merged where they
    would overlap.

    Two moments can be seconds apart - the peaks are only required to be three
    buckets from each other, and on a short video three buckets is under ten
    seconds. Cutting both would hand back two clips of nearly the same footage
    and call them different moments. Where the windows touch they become one
    longer clip instead, which is what a person would have done by hand.
    """
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return []
    if seconds <= 0 or not peaks:
        return []

    limit = float(duration or 0)
    spans = []
    for peak in peaks:
        try:
            middle = (float(peak["start"]) + float(peak["end"])) / 2
        except (KeyError, TypeError, ValueError):
            continue
        # A clip that runs off either end of the video is pulled back inside
        # it rather than shortened: a moment near the start is still worth the
        # full length, it just cannot begin before the video does.
        start = middle - seconds / 2
        if limit and start + seconds > limit:
            start = limit - seconds
        start = int(max(0, start))
        # The end is measured from the whole-second start rather than rounded
        # on its own. Rounding the two independently made a "60 second clip"
        # that was sixty-one, which is the kind of small lie that turns up
        # later as an off-by-one somewhere it matters.
        end = start + seconds
        if limit and end > limit:
            end = int(limit)
        if end > start:
            spans.append((start, end))

    spans.sort()
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{"start": s, "end": e} for s, e in merged]


def _exact_cut(exact: bool) -> list:
    """
    Cut on the mark rather than on the video's own keyframes.

    Without this ffmpeg copies the streams, so it has to start at the keyframe
    before the mark and a moment of whatever came before appears at the start -
    reported from real use as about half a second. Removing it means
    re-encoding the part, which measured 218s against 94s for the same
    two-minute clip. So it is offered rather than assumed, the same way the
    trim has offered it all along.
    """
    return ["--force-keyframes-at-cuts"] if exact else []


def clip_args(clips: list, exact: bool = False) -> list:
    """--download-sections once per wanted moment, or nothing."""
    args = []
    for span in clips or []:
        try:
            start, end = int(span["start"]), int(span["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        args += ["--download-sections", f"*{start}-{end}"]
    if not args:
        return []
    # Same reason as a trim and a chapter: ffmpeg does the cutting, and --print
    # turns --quiet on implicitly, which silences its progress completely.
    return ["--no-quiet"] + args + _exact_cut(exact)


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

    def bytes_of(f):
        return f.get("filesize") or f.get("filesize_approx") or 0

    videos = [f for f in (info.get("formats") or [])
              if isinstance(f.get("height"), int) and bytes_of(f)]
    audios = [bytes_of(f) for f in (info.get("formats") or [])
              if f.get("acodec") not in (None, "none") and not f.get("height")
              and bytes_of(f)]
    # The audio Riplox would take rides along with every video-only stream, so
    # a size that left it out would be wrong by the same amount every time.
    audio_bytes = max(audios) if audios else 0

    def size_at(limit):
        """Roughly what this rung costs: the stream the selector would land on."""
        fits = [f for f in videos if not limit or f["height"] <= limit]
        if not fits:
            return 0
        tallest = max(f["height"] for f in fits)
        top = [f for f in fits if f["height"] == tallest]
        # Mirrors the sort: h264 wins a tie, and "max" takes the fattest - so
        # the biggest at that height is the honest upper bound either way.
        return max(bytes_of(f) for f in top) + audio_bytes

    real, fake = set(), set()
    for f in info.get("formats") or []:
        h = f.get("height")
        if not isinstance(h, int):
            continue
        (fake if _is_upscale(f) else real).add(h)

    best_real = max(real) if real else 0

    rungs, notes = [], {}
    for key in ("4320", "2160", "1440", "1080", "720", "480", "360"):
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

    # h264 is a tie-break in the selector, not a filter, so a height that
    # exists only as VP9 or AV1 comes back as VP9 or AV1 - and above 1080p
    # that is usually the case, while at 4320p it always is. The chip says so
    # rather than letting the file say it later, in a player that will not
    # open it. Only heights that really exist are judged: a rung reachable
    # only through an upscale makes no claim either way.
    friendly = set()
    for f in info.get("formats") or []:
        h = f.get("height")
        if isinstance(h, int) and re.match(r"^(avc1|h264)", str(f.get("vcodec") or "")):
            friendly.add(h)

    modern = []
    for key in rungs:
        at_or_below = [h for h in real if h <= int(key)]
        if at_or_below and max(at_or_below) not in friendly:
            modern.append(key)

    # Shown on the chip so nobody meets a 3.6 GB download by surprise - which
    # is exactly what "highest" means on an 8K video.
    sizes = {}
    for key in ["best", "max"] + rungs:
        found = size_at(0 if key in ("best", "max") else int(key))
        if found:
            sizes[key] = human_bytes(found)

    return {"rungs": ["best", "max"] + rungs + ["mp3"],
            "upscaled": notes, "sizes": sizes, "noH264": modern}


# What the engine says when Instagram is refusing the post rather than the
# request. Any one of these, followed by the door finding a page with no video
# in it, is the same story told twice.
_IG_WALLED = ("certain audiences", "not available to everyone",
              "empty media response", "http error 400",
              "instagram turned this one down", "limits who can see this one")

# The subset that means something narrower and far more useful: Instagram is
# withholding this post from *the account asking*, not from everybody. Measured
# on four real failing reels - the API answered 400 with both "certain
# audiences" and "inappropriate" in the body, on the mobile and the web route
# alike, while the same posts played normally in the phone app on that same
# account and downloaded immediately once a second account with its sensitive
# content setting open was used instead.
#
# This distinction is the whole point of the function below. Told "this is the
# post, not you", someone re-signs-in for three days and gets nowhere; told
# which setting to open, they fix it in a minute.
_IG_AUDIENCE = ("certain audiences", "not available to everyone",
                "inappropriate")

# Two more that look exactly like the one above from a distance and need the
# opposite advice, which is the whole reason they are separated out. Sending
# someone to the sensitive-content setting when Instagram is challenging their
# login, or when the post is blocked in their country, is the same class of
# wrong turning as the sentence this function used to end with.
_IG_CHECKPOINT = ("checkpoint_required", "challenge_required",
                  "suspicious login", "confirm it's you", "checkpoint")
_IG_GEO = ("not available in your country", "not available from your location",
           "geo restrict", "geo-restrict", "blocked in your country")

# Private and removed are deliberately not separated. doors.py already says
# why in its own words: Instagram gives no signal that tells them apart from
# outside, and naming the wrong one sends someone off to fix something that was
# never the problem. Guessing here would undo the point of this whole function.


def _door_verdict(engine_error: str, door_error: str,
                  tried_signed_in: bool = False) -> str:
    """
    Which of the two refusals to show the user, and what to tell them to do.

    The door's answer is normally the better one - it names a removed post or
    an age gate where yt-dlp leaves a stack trace. Not here. When the engine
    was refused for a restricted post and the door then reports a page with no
    video in it, the door's list of maybes - "private, or removed, or sign in"
    - is the weaker sentence: it asks for a sign-in that was already tried and
    already refused.

    What this must never do again is guess at the cause and sound certain about
    it. The sentence that used to live here said the refusal was "the post
    rather than anything on this end", which was exactly backwards: the cause
    was a setting on the asking account, and the person reading it was the only
    one who could fix it. So the specific advice is given only on the specific
    signal, and what is claimed about the attempts is only ever what the job
    actually recorded doing - hence tried_signed_in, which is the job's history
    rather than its last attempt's state.
    """
    low_engine = (engine_error or "").lower()
    low_door = (door_error or "").lower()
    # Checkpoint and geo join the gate rather than sitting behind it: neither
    # is guaranteed to arrive with one of the _IG_WALLED wordings, and a
    # challenged login that fell through to the door's list of maybes is
    # exactly the case worth catching.
    if ("page but no video" not in low_door
            or not any(sign in low_engine
                       for sign in _IG_WALLED + _IG_CHECKPOINT + _IG_GEO)):
        return door_error

    # Checked before the audience gate, because a challenged login can answer
    # with the audience wording too and the advice for it is different.
    if any(sign in low_engine for sign in _IG_CHECKPOINT):
        return ("Instagram is challenging the sign-in Riplox has saved rather "
                "than refusing the post. Open Instagram in your browser, "
                "confirm it is you when it asks, then sign in again under "
                "Settings. Nothing about the post needs changing.")

    if any(sign in low_engine for sign in _IG_GEO):
        return ("Instagram is not serving this one to your part of the world. "
                "No setting and no sign-in changes that - only reaching it "
                "from somewhere it is available will, which is what the proxy "
                "in Settings is for.")

    if any(sign in low_engine for sign in _IG_AUDIENCE):
        return ("Instagram is holding this one back from your account rather "
                "than from everyone - it says the post is meant for certain "
                "audiences. In Instagram, open Settings -> Suggested content "
                "-> Sensitive Content Control and choose \"More\". If that "
                "choice is not there, Instagram has not age-verified the "
                "account, and a birthday on the profile is not enough on its "
                "own. Signing in here with an account that can already see the "
                "post works too.")

    if tried_signed_in:
        return ("Instagram refused this one signed in, signed out, and by "
                "Riplox's own route. That usually means the account is "
                "private, or the post was removed. An account that follows "
                "them is the only thing that would reach it.")

    return ("Instagram refused this one, and there is no Instagram sign-in "
            "saved here to try it with. Sign in under Settings and try again - "
            "if it still fails after that, the account is private or the post "
            "is gone.")


# The connection itself going away, told apart from a site saying no.
#
# ⚠️ Deliberately narrow: only failures that cannot be a site's own answer. A
# name that will not resolve, and a network with no route, are nobody's verdict
# on the video. "giving up after N retries", "max retries exceeded",
# "connection reset" and "timed out" are all things a site does to a request it
# is refusing, so they are NOT here - reading those as an outage would put a
# genuinely dead download back on the queue forty times over, which is the
# failure this whole area exists to avoid.
_NETWORK_LOST = (
    "getaddrinfo failed", "failed to resolve", "errno 11001",
    "name resolution", "network is unreachable", "no route to host",
    "failed to establish a new connection",
    "winerror 10051",                    # network is unreachable
    "winerror 10065",                    # no route to host
)


def network_lost(text: str) -> bool:
    """Does this say the connection went, rather than the site refusing?"""
    low = (text or "").lower()
    return any(sign in low for sign in _NETWORK_LOST)


def _site_name(text: str) -> str:
    """The site, from the engine's own [extractor] tag, or a plain word."""
    tag = re.match(r"\s*\[([a-z0-9_]+)", (text or "").lower())
    known = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok",
             "facebook": "Facebook", "twitter": "X", "reddit": "Reddit"}
    return known.get(tag.group(1) if tag else "", "The site")


def _plain_line(line: str) -> str:
    """Strip the engine's own prefix off a line before a person reads it.

    yt-dlp writes "[youtube] dQw4w9WgXcQ: message". The tag is which extractor
    ran and the token after it is the video id - neither is an explanation,
    and both used to reach the screen whenever no branch above matched.
    """
    out = re.sub(r"^\[[^\]]{1,40}\]\s*", "", (line or "").strip())
    # Only an id-shaped token, so a real sentence that happens to contain a
    # colon keeps all of itself.
    out = re.sub(r"^[A-Za-z0-9_-]{1,24}:\s+", "", out)
    # ⚠️ And never the part in brackets. yt-dlp appends "(caused by <HTTPError
    # 404: Not Found ...>)", which is the library's exception repr - it is cut
    # off by the length limit and leaves the sentence ending on an unclosed
    # bracket, which is what a reader was being shown.
    out = out.split("(caused by")[0].strip()
    return out.strip()


def _clean_error(stderr: str, during: str = "download") -> str:
    """Turn a yt-dlp stack of ERROR lines into one human sentence.

    ⚠️ `during` exists because the same failures are shown in two very
    different moments. "reading" is someone pasting a link or checking a
    playlist - nothing has been fetched, so nothing may be described as
    partly done. "download" is a job that really was moving bytes. The
    default is the download wording, which is what it was written for.
    """
    text = (stderr or "").strip()
    if not text:
        return "That link could not be opened."

    low_all = text.lower()

    # Before any reading of what the site said: a name that would not resolve
    # means nothing was reached, so nothing after it is the site's opinion of
    # anything. And what comes after it is loud - yt-dlp goes on to join
    # fragments it never wrote and reports THAT, "[Errno 2] No such file or
    # directory: ...part-Frag3", which is the consequence rather than the
    # cause, and was what the user was being shown.
    if network_lost(low_all):
        if during == "reading":
            # Nothing was being fetched yet, so there is nothing kept and
            # nothing to carry on from - saying otherwise sends someone
            # looking for a half-finished file that does not exist.
            return ("No connection, so that link could not be read. Check the "
                    "connection and try again.")
        return ("The connection dropped while this was downloading. What "
                "already arrived is kept, so carrying on continues from "
                "there rather than starting again.")

    # Chrome-family cookie stores cannot be decrypted by anything but the
    # browser itself, and this is the error that says so. Riplox no longer
    # needs to: Settings has a sign-in that asks the browser for them.
    if ("dpapi" in low_all
            or "app-bound" in low_all
            or "object has no attribute 'decode'" in low_all
            or ("cookie" in low_all and "decrypt" in low_all)):
        return ("Chrome-based browsers will not let another program read their "
                "cookies. Use 'Sign in with your browser' in Settings instead.")

    # TikTok answers this machine with a bot wall rather than the video: the
    # page that comes back is a 1.4 KB shell whose only job is to run
    # JavaScript and decide whether you are a browser. It carries no video data
    # at all, which is what the engine is reporting.
    #
    # Everything that could be tried through the engine was tried and measured
    # on 11 Aug 2026: plain, five different browser impersonations, a desktop
    # user-agent, both of the engine's alternate TikTok API hosts, and finally
    # the cookies taken from a real Chrome that HAD got through the wall. Every
    # one came back with this same message, and a different connection did not
    # help either - so neither "sign in" nor "try a VPN" is worth saying.
    #
    # What did work was going around the engine entirely: doors.py asks the
    # site the way the site expects to be asked, keeping the cookie it hands
    # back. Reaching this message therefore means that route was refused too,
    # or was switched off in Settings.
    if "unexpected response from webpage" in low_all:
        return ("TikTok answered with a bot check instead of the video, and "
                "Riplox's own direct route could not get the post either. "
                "Signing in and changing connection have both been measured "
                "and neither gets past it. Press retry - this one often "
                "clears on its own.")

    # Instagram's own ruling, not a failure to fetch. Measured on a real link:
    # signed out it says this, signed in with a rejected session it says 400,
    # and Riplox's own route gets the page with no video in it. Three
    # different answers, one meaning - the account being used is not one this
    # post is shown to. No technique gets past that, so the message says so
    # instead of implying another attempt might work.
    if "certain audiences" in low_all or "isn't available to everyone" in low_all:
        return ("Instagram limits who can see this one. It is not a download "
                "problem - the same post is hidden from the account being "
                "used, so no setting or retry reaches it. Signing in with an "
                "account that can see it is the only thing that will.")

    # Too many requests in a short time. Named plainly because the useful
    # response is to stop, not to retry: Instagram answers this way after a
    # burst, and continuing to ask is what turns a pause into a locked
    # account. Measured on this machine after roughly twenty extraction
    # attempts inside a few minutes.
    if "429" in low_all and ("instagram" in low_all or "too many" in low_all):
        return ("Instagram is asking this computer to slow down - too many "
                "requests in a short time. Stop for a while rather than "
                "retrying: carrying on is what gets an account limited. It "
                "clears on its own.")

    # A post the API answers with nothing at all. Not the same as a refusal,
    # and not something the user can act on by signing in - so it says what is
    # actually known instead of guessing at a cause.
    if "empty media response" in low_all:
        return ("Instagram answered without the video. That happens on posts "
                "it will not serve to the account being used - most often "
                "age-restricted ones. Riplox's own route was tried too.")

    # A session that is being turned down, rather than one that is missing.
    # Worth separating: the fix is to sign in again, not to change anything
    # about the download.
    # Instagram's other way of saying no: the media call answers with an empty
    # body, which the engine can only report as a JSON parse failure - ending
    # in "please report this issue", so a refusal by Instagram reads as a bug
    # in Riplox. Measured on a post Instagram's own page calls age-restricted,
    # with a session it had just accepted.
    if "instagram" in low_all and ("failed to parse json" in low_all
                                   or "expecting value" in low_all):
        return ("Instagram answered with nothing at all for that post. That "
                "is how it refuses one it will not serve to the account being "
                "used - an age-restricted post, most often. If other "
                "Instagram links still work, it is this post rather than the "
                "sign-in.")

    # Instagram answers 400 for two different things and does not say which:
    # a session it has stopped accepting, and a post it will not serve to the
    # account being used. Measured on this machine - a restricted reel gave
    # 400 with a session that fetched an ordinary reel seconds later - so a
    # message that names only the sign-in sends people to sign in again for
    # nothing. Both are named, with the way to tell them apart.
    if "400" in low_all and "instagram" in low_all:
        return ("Instagram turned this one down. If other Instagram links "
                "still work, it is this post rather than your sign-in - a "
                "restricted or age-gated one, which nothing here can open. If "
                "they have all stopped, sign in again in Settings.")

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
            # yt-dlp puts the word on one stream and the reason on another when
            # the download itself fails, so stderr can carry a bare "ERROR:"
            # with nothing after it. Returning that emptied the message
            # altogether and the Failed page said "No reason was recorded."
            # over a reason that was plainly recorded - measured on a real job,
            # where seventeen of these stood ahead of the only useful line.
            if not msg:
                continue
            low = msg.lower()
            if "unsupported url" in low:
                return "This site is not supported."
            # ⚠️ Read BEFORE the word-matching branches below. "Service
            # Unavailable" contains "unavailable", so a 503 was being reported as
            # the uploader having removed the video - one of those clears by
            # itself and the other never does.
            #
            # The status code, not the engine's sentence. Measured: a 404
            # came back as "Unable to download webpage: HTTP Error 404: Not
            # Found (caused by <HTTPError 404: Not Found" - cut mid-bracket -
            # and on a long URL the message was the URL itself, because the
            # leading token in that line is a piece of what was just pasted.
            status = re.search(r"http error (\d{3})", low)
            if status:
                code = status.group(1)
                if code == "404":
                    return "There is nothing at that link - check it and try again."
                if code in ("401", "403"):
                    return ("The site would not open that one for this "
                            "computer. Signing in with your browser in "
                            "Settings sometimes helps.")
                if code == "429":
                    return ("The site is asking this computer to slow down. "
                            "Wait a few minutes before trying again.")
                if code.startswith("5"):
                    return ("The site is having trouble at its end (%s). It is "
                            "worth trying again shortly." % code)
                return "The site answered with an error (%s)." % code
            if "unable to download webpage" in low:
                return "That page could not be opened."
            if "no host supplied" in low or "invalid url" in low:
                return "That link has no site in it."
            if "not a bot" in low or "login_required" in low:
                # Usually a passing IP-level check: Riplox already retries on
                # its own, so by the time this is shown the retries are spent.
                # ⚠️ The site comes from the line, not from a guess. This said
                # "YouTube" for every site, so an Instagram post that wanted a
                # login sent the reader somewhere they had never been.
                return ("%s asked for proof you are a real viewer and kept "
                        "asking. Wait a few minutes, or sign in with your "
                        "browser in Settings." % _site_name(msg))
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
            return _plain_line(msg)[:200] or msg[:200]

    return _plain_line(text.splitlines()[-1])[:200] or text.splitlines()[-1][:200]


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
#
# ⚠️⚠️ Every client here was MEASURED on 1 Sep 2026 (yt-dlp 2026.08.19, three
# videos), because the previous list quietly cost the user their quality:
#
#     client        no PO token   with PO token
#     tv_simply         360p          2160p
#     mweb              360p          2160p
#     web_safari        180p           180p     <- was on rung 2
#     android_vr        360p           360p     <- was on rung 3
#     tv_embedded      2160p          2160p
#     visionos         2160p          2160p
#
# Two separate faults were in one line. `web_safari` and `android_vr` are
# capped EVEN WITH a token, so they could never contribute a good format -
# they were pure downside. And `tv_simply`/`mweb` are only whole while a
# proof-of-origin token can be minted, which is exactly what a network outage
# takes away - so the ladder collapsed to 360p at the precise moment it ran.
# Reported by a user as a full-quality request arriving as a 360p file.
#
# So each rung now pairs its token-dependent client with one measured to give
# every format WITHOUT a token. `tv_embedded` and `visionos` were both checked
# by actually fetching bytes at 1080p+, not merely by listing formats.
# ⚠️ `visionos` does not serve "Made for kids" videos (yt-dlp's own note), the
# same limitation the `android_vr` it replaces already had.
#
# ⚠️ This list ages. yt-dlp adds and retires clients every few weeks
# (`tv_downgraded` is newer than the engine shipped here, which does not know
# it). That is why _quality_short below checks the RESULT rather than trusting
# any list - a name that is right today will be wrong, and the outcome check
# does not care which client let the user down.
_RETRY_CLIENTS = ["", "tv_simply,tv_embedded", "mweb,visionos"]

# How long an engine may say NOTHING before Riplox stops waiting for it.
#
# Deliberately generous. The thing this catches sat there for five hours, so
# fifteen minutes loses nothing real, and the cost of being wrong is high: a
# download killed at the wrong moment throws away what it had. Silence is a
# safe signal because a slow download is not a quiet one - yt-dlp prints a
# progress line every second at any speed, and the merge talks on stderr.
_SILENCE_LIMIT = 900.0

# Other sites have no player client to switch, but the same request often
# works seconds later - a TikTok link that failed with "unable to extract
# universal data for rehydration" succeeded on the very next attempt with the
# same engine. Without this, every site except YouTube got exactly one try.
_PLAIN_RETRIES = ["", "", ""]

# 🔴 The connection broke - which says nothing at all about the player client
# being used. Climbing the ladder here was the bug behind "best available
# went to an https error halfway and came back 360p": the fallback clients
# are only ever offered small formats, so a Wi-Fi hiccup silently bought a
# worse file. These are retried on the SAME rung; only a refusal moves down.
_NETWORK_TROUBLE = (
    "read timed out", "timed out", "timeout", "connection reset",
    "unable to connect", "connection aborted", "connection refused",
    "temporary failure in name resolution", "getaddrinfo", "network is unreachable",
    "remote end closed", "incompleteread", "ssl", "eof occurred",
    "unable to download webpage",
    # urllib wraps the real reason - a DNS failure, a refused socket - inside
    # "<urlopen error ...>", so the words below it are often the only ones
    # that survive into the log.
    "urlopen error",
)

# How many times one rung is tried again for a broken connection before the
# ladder is spent on it. Three is enough for a switch between Wi-Fi and
# mobile; more would keep a genuinely dead link busy for minutes.
_SAME_RUNG_TRIES = 3


def _is_network_trouble(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _NETWORK_TROUBLE)


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
# Pacing, and what to do when a site says no
# --------------------------------------------------------------------------
# Two separate things, and only one of them ever slows anything down.
#
# The one that costs nothing is the cooldown. yt-dlp has no handling for HTTP
# 429 at all - checked in its own extractor/common.py - so a site that answers
# "too many requests" is currently asked again immediately, by the next job in
# the queue. Asking harder is exactly what turns a temporary refusal into a
# blocked account. After a refusal, that site is left alone for a while.
# Nothing about this is active until a site has already said no.
#
# The one that does cost something is the gap between jobs, and it is applied
# to Instagram alone because Instagram is the only site anyone has published a
# budget for. Two maintained projects agree on the number: gallery-dl ships
# 6-12 seconds between Instagram requests, and instaloader's own limiter allows
# 75 requests per 11 minutes - about 8.8 seconds each. Everywhere else the
# honest answer is that nobody knows, so nothing else is slowed down.
#
# Fourteen links shared at once - the way this app is actually used - is 40 to
# 80 requests, which lands squarely on that budget. That is the case this is
# for; a single link is never held back.

# site -> seconds between STARTING two jobs for it.
PACE_GAP = {"Instagram": 8.0}

# What "go properly slowly" means for a site that has already refused once:
# seconds between the engine's own page requests inside a single download.
# gallery-dl ships 6-12s for Instagram; this is the floor of that.
#
# Not the everyday setting. Every download already carries the polite 0.75s
# from _base_args, and the honest arithmetic says that plus the job gap is
# still faster than Instagram's published budget - roughly one request every
# two seconds against a budget of one every nine. Closing that gap completely
# would turn fourteen shared reels into eight minutes of waiting, every time,
# for a refusal that may never come. So the strict number is held back until
# the site itself says no, and then it is used.
PACE_STRICT_REQUESTS = {"Instagram": 6.0}

# The gap between starting two jobs grows the same way, for the same reason.
PACE_GAP_MAX = 40.0

COOLDOWN_FIRST = 45 * 60         # after the first refusal
COOLDOWN_MAX = 6 * 3600          # doubling, but never past this
STRIKE_WINDOW = 12 * 3600        # after this long with no refusal, forgiven

# What a refusal actually looks like. Deliberately narrow: a site put to sleep
# for the wrong reason is a broken app as far as anyone can tell, so this
# matches the things sites say when they are rate-limiting and nothing else.
_REFUSALS = (
    "http error 429", "429 too many requests", "too many requests",
    "rate-limit reached", "rate limit exceeded",
    "please wait a few minutes before you try again",
    "checkpoint_required", "challenge_required",
)

_pace_lock = threading.Lock()
_pace_started = {}               # site -> when a job for it last started


def pace_file() -> Path:
    return data_dir() / "pace.json"


def load_pace() -> dict:
    try:
        with open(pace_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_pace(data: dict) -> None:
    tmp = pace_file().with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        tmp.replace(pace_file())
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def looks_rate_limited(text: str) -> bool:
    low = (text or "").lower()
    return any(phrase in low for phrase in _REFUSALS)


# A refusal that is known to pass on its own, and when to try it again.
#
# TikTok answers some requests with a 1.4 KB check page and the same link with
# the real thing minutes later - measured twice in one evening: four failures
# at 21:51, the same link handed over at 22:2x, with nothing changed in
# between. The door already retries six times inside eleven seconds, which is
# the wrong timescale entirely: what clears this is minutes, not seconds.
#
# Two goes, five minutes and then fifteen. Not more: a link that is still
# refused twenty minutes later is not the kind that clears, and a queue that
# quietly re-asks for ever is how a machine ends up walled properly.
AUTO_RETRY_AFTER = (5 * 60, 15 * 60)

_CLEARS_ON_ITS_OWN = (
    "check page instead of the post", "often clears on its own",
    # ⚠️ The media URL expiring part-way through, which is what a big file
    # invites: YouTube issues those URLs with a lifetime, and an 8K download of
    # three and a half gigabytes can outlive one. yt-dlp says exactly this, and
    # only after extraction has already succeeded - so the video IS reachable
    # and the address simply went stale. A later attempt resumes from the .part
    # file rather than starting over.
    #
    # Deliberately this whole phrase and NOT a bare "403": _AUTH_REFUSED
    # already treats "http error 403" as a site refusing the request, which is
    # a different thing with a different recovery, and a video that is private
    # or blocked fails during extraction with a different message entirely.
    "unable to download video data",
)


# Is there a network at all?
#
# Riplox used to walk its whole ladder of retry clients in about twenty seconds
# - 2s, 6s, 10s between attempts - which is shorter than a Wi-Fi-to-mobile
# switch takes. So every attempt was spent while there was no network, the job
# landed in Failed, and nothing ever picked it up again: AUTO_RETRY_AFTER only
# fires for errors that clears_on_its_own() recognises, and that list is two
# Instagram phrases.
#
# ⚠️ This answers "is there a network", NOT "is this site up". A site that is
# refusing must still fail normally - waiting for ever is worse than failing.
# ⚠️ Several, on different ports and different owners, because ONE answer
# deciding "nothing may download" is a dangerous amount of power for a probe.
# Corporate networks and some ISPs block 1.1.1.1 and 8.8.8.8 outright - on such
# a machine a single-host probe would say "offline" for ever and Riplox would
# never start a download again, which is far worse than the outage it is meant
# to handle.
_NET_HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 53),
              ("9.9.9.9", 53), ("208.67.222.222", 443))

# And even then it fails OPEN. If the probe has claimed offline for this long
# while work is waiting, one job is let through to find out with real traffic:
# a wrong probe costs a single quick attempt that puts itself back, and a right
# one costs nothing at all.
_NET_DOUBT_AFTER = 120.0
_net_offline_since = [0.0]
_NET_CACHE_FOR = 4.0                 # _next_job runs constantly; do not probe per call
_net_last = [0.0, True]              # (when, answer)
_net_lock = threading.Lock()


def network_ok(force: bool = False) -> bool:
    """Can this machine reach anything? Cached, because it is asked often."""
    now = time.monotonic()
    with _net_lock:
        if not force and now - _net_last[0] < _NET_CACHE_FOR:
            return _net_last[1]

    answer = False
    for host, port in _NET_HOSTS:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(2.0)
        try:
            probe.connect((host, port))
            answer = True
        except OSError:
            continue
        finally:
            probe.close()
        break

    with _net_lock:
        _net_last[0], _net_last[1] = time.monotonic(), answer
    return answer


def here_now() -> str:
    """
    This machine's own address, as a fingerprint of which network it is on.

    A network CHANGE is the case the probe above cannot see: the internet is
    fine, it is simply a different internet, and the media URL in hand was
    issued to the old address. YouTube answers those with 403, which is correct
    of it and useless to us.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("1.1.1.1", 1))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def clears_on_its_own(text: str) -> bool:
    """Is this the kind of refusal that passes if you simply wait?"""
    low = (text or "").lower()
    return any(sign in low for sign in _CLEARS_ON_ITS_OWN)


def cool_key(site: str, account: int = 0) -> str:
    """
    What is being rested: one account, or the site itself.

    A refusal answers the request that was made, and that request carried one
    account's session. Resting the whole site because one account was asked to
    slow down wastes the spare that exists for exactly this - so the account is
    named when it is known. A request that carried no session at all has only
    the site to blame, and that is what the bare key is.
    """
    site = site or ""
    return f"{site}#{int(account)}" if int(account or 0) >= 1 else site


def start_cooldown(site: str, why: str = "", account: int = 0) -> float:
    """
    Leave this site - or one of its accounts - alone for a while.
    Returns when it may be used again.

    A second refusal doubles the wait, because the first one plainly was not
    long enough. It is written to disk on purpose: restarting the app is the
    most natural thing to try when downloads stop, and a cooldown that a
    restart clears is a cooldown that never happens.
    """
    if not site:
        return 0.0
    now = time.time()
    key = cool_key(site, account)
    with _pace_lock:
        data = load_pace()
        cooling = data.get("cooldown") or {}
        before = cooling.get(key) or {}
        strikes = int(before.get("strikes") or 0)
        # A refusal months ago says nothing about today.
        if now - float(before.get("at") or 0) > STRIKE_WINDOW:
            strikes = 0
        strikes += 1

        wait = min(COOLDOWN_FIRST * (2 ** (strikes - 1)), COOLDOWN_MAX)
        cooling[key] = {"until": now + wait, "at": now, "strikes": strikes,
                        "why": (why or "")[:200]}
        data["cooldown"] = cooling
        _save_pace(data)
        return now + wait


def cooldown_left(site: str, account: int = 0) -> float:
    """
    Seconds until this may be asked again. 0 when it is free.

    A site-wide rest covers every account: it is what a refusal with no
    session attached leaves behind, and that one was not about any account.
    """
    cooling = load_pace().get("cooldown") or {}
    now = time.time()
    left = 0.0
    for key in {cool_key(site, 0), cool_key(site, account)}:
        entry = cooling.get(key) or {}
        left = max(left, float(entry.get("until") or 0) - now)
    return max(0.0, left)


def clear_cooldown(site: str, account: int = 0) -> bool:
    """
    The user saying "no, go now".

    Their call, and it wins - but the strike count is kept, so if the site
    refuses again the next wait is still the longer one rather than starting
    over at forty-five minutes.
    """
    with _pace_lock:
        data = load_pace()
        cooling = data.get("cooldown") or {}
        entry = cooling.get(cool_key(site, account))
        if not entry:
            return False
        entry["until"] = 0
        data["cooldown"] = cooling
        _save_pace(data)
        return True


def cooling_sites() -> list:
    """Everything currently being left alone, for the screen."""
    now = time.time()
    out = []
    for key, entry in (load_pace().get("cooldown") or {}).items():
        left = float(entry.get("until") or 0) - now
        if left <= 0:
            continue
        site, _, account = str(key).partition("#")
        out.append({"site": site, "account": int(account or 0),
                    "left": int(left), "why": entry.get("why", "")})
    return sorted(out, key=lambda e: -e["left"])


def _strikes(site: str, account: int = 0) -> int:
    """
    How many times this has refused recently.

    Counted against the site as a whole rather than the account: a second
    account being refused is the same connection being told to slow down, so
    the strict pacing that follows belongs to everything using it.
    """
    cooling = load_pace().get("cooldown") or {}
    now, most = time.time(), 0
    for key, entry in cooling.items():
        if str(key).partition("#")[0] != (site or ""):
            continue
        if now - float(entry.get("at") or 0) > STRIKE_WINDOW:
            continue
        most = max(most, int(entry.get("strikes") or 0))
    return most


def pace_requests(site: str, settings: dict = None):
    """
    Seconds between page requests for a site that has been refused, or None.

    None means "leave it alone" - the ordinary polite pause every download
    already carries is enough for a site that has never complained.
    """
    settings = settings if settings is not None else load_settings()
    if not settings.get("pace_sites", True):
        return None
    strict = PACE_STRICT_REQUESTS.get(site or "")
    if not strict or not _strikes(site):
        return None
    return strict


def _accounts_of(site: str) -> list:
    """
    This site's accounts, or [] when it has none Riplox signs into.

    Imported here rather than at the top: cookies.py is built on this module,
    so the arrow only goes one way and a lazy import is what keeps it that way.
    """
    if not site:
        return []
    try:
        import cookies as cookie_store
        return [a for a in cookie_store.accounts_for(_site_key(site))
                if a.get("signedIn") and not a.get("paused")]
    except Exception:                                  # noqa: BLE001
        return []


def _site_key(site: str) -> str:
    """The cookie store's key for a display name like "Instagram"."""
    return (site or "").lower()


def resting_accounts(site: str) -> set:
    """Account numbers for this site that are waiting out a refusal."""
    return {a["n"] for a in _accounts_of(site) if cooldown_left(site, a["n"])}


def account_wait(site: str) -> float:
    """
    How long this site's downloads must wait, given who could sign them.

    Zero when something can go now: either an account that is not resting, or
    a site with no accounts at all, where only the site's own rest counts.

    The point of a spare is exactly this - one account being told to slow down
    should not stop the other one, and before this it stopped everything for
    that site.
    """
    site_rest = cooldown_left(site, 0)
    accounts = _accounts_of(site)
    if not accounts:
        return site_rest

    waits = [cooldown_left(site, a["n"]) for a in accounts]
    if any(w <= 0 for w in waits):
        return site_rest                    # somebody is free to take this one
    return max(site_rest, min(waits))       # everyone is resting; the soonest


def note_started(site: str) -> None:
    """One job for this site has just begun."""
    if site:
        _pace_started[site] = time.monotonic()


def pace_left(site: str, settings: dict = None) -> float:
    """
    How long before another job for this site may start.

    In memory rather than on disk: this is about the gap between two downloads
    minutes apart, and after a restart there is nothing to space out from.
    """
    settings = settings if settings is not None else load_settings()
    if not settings.get("pace_sites", True):
        return 0.0
    gap = PACE_GAP.get(site or "")
    if not gap:
        return 0.0
    last = _pace_started.get(site)
    if last is None:
        return 0.0
    # A site that has refused gets more room, doubling each time, because the
    # gap it was given plainly was not enough.
    gap = min(gap * (2 ** _strikes(site)), PACE_GAP_MAX)
    return max(0.0, gap - (time.monotonic() - last))


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


def next_time_at(text: str, now=None) -> float:
    """
    "HH:MM" as the next moment it will actually be, or 0.0 for nothing asked.

    Today when it is still ahead, tomorrow when it has gone by. "02:00" typed
    at nine in the morning is a request for tonight, not for right now, and
    reading it the other way would start the very download the user was
    trying to put off.
    """
    text = str(text or "").strip()
    if not text:
        return 0.0
    minutes = _minutes(text, -1)
    if minutes < 0:
        return 0.0
    now = now or datetime.now()
    when = now.replace(hour=minutes // 60, minute=minutes % 60,
                       second=0, microsecond=0)
    if when <= now:
        when += timedelta(days=1)
    return when.timestamp()


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


# --------------------------------------------------------------------------
# Everything the engine can reach
# --------------------------------------------------------------------------
# "Which sites does this work with?" is the first question anyone asks, and
# until now the honest answer lived nowhere in the app. The engine knows - it
# will list every extractor it carries - so the list is read from it rather
# than written out here and left to rot one release later.

_EXTRACTORS = {"stamp": "", "names": []}
_EXTRACTORS_LOCK = threading.Lock()


def _extractors_file() -> Path:
    return data_dir() / "extractors.json"


def extractor_names(refresh: bool = False) -> list:
    """
    Every site the installed engine has an extractor for.

    Cached against the engine's own version: listing them takes seconds, and
    the answer only changes when the engine does. The cache is a plain list of
    names, so a corrupt or missing file costs one relisting and nothing else.
    """
    stamp = engine_version()

    with _EXTRACTORS_LOCK:
        if not refresh and _EXTRACTORS["stamp"] == stamp and _EXTRACTORS["names"]:
            return list(_EXTRACTORS["names"])

    if not refresh:
        try:
            saved = json.loads(_extractors_file().read_text("utf-8"))
            if isinstance(saved, dict) and saved.get("stamp") == stamp:
                names = [str(n) for n in saved.get("names") or []]
                if names:
                    with _EXTRACTORS_LOCK:
                        _EXTRACTORS.update({"stamp": stamp, "names": names})
                    return list(names)
        except (OSError, ValueError):
            pass

    exe = ytdlp_path()
    if exe is None:
        return []
    try:
        out = _run([str(exe), "--list-extractors"], timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []

    names = []
    for line in (out.stdout or "").splitlines():
        name = line.strip()
        # The generic fallbacks are not sites anyone would look for, and
        # listing them as though they were is worse than leaving them out.
        if not name or name.lower() in ("generic", "default"):
            continue
        names.append(name)

    names = sorted(set(names), key=str.lower)
    with _EXTRACTORS_LOCK:
        _EXTRACTORS.update({"stamp": stamp, "names": names})
    try:
        _extractors_file().write_text(
            json.dumps({"stamp": stamp, "names": names}), encoding="utf-8")
    except OSError:
        pass
    return list(names)


# --------------------------------------------------------------------------
# Is it me, or is it the site?
# --------------------------------------------------------------------------
# The most-voted complaint on every downloader is a variant of "can't download
# from X", and the most-voted feature request is a status indicator - because
# what people actually want to know first is whether the thing is broken for
# everyone or only for them. Riplox can answer that better than most: it has
# two ways in, so it can say not just "working" but "working the hard way",
# which is the early warning that the usual route has gone.
#
# Recorded from this machine's own results only. No service is asked, nothing
# is reported anywhere, and one bad link does not condemn a site - it is what
# happened here, last time, per site.

HEALTH_OK = "ok"            # the engine handled it
HEALTH_DOOR = "door"        # the engine failed, Riplox's own route worked
HEALTH_DOWN = "down"        # neither got it

_health = {}
_health_lock = threading.Lock()
# Windows' clock only moves in ~16ms steps, so two sites recorded in the same
# instant get identical timestamps and "newest first" becomes whatever order
# the dict happens to be in. A counter alongside the time keeps the order
# meaning what it says. Found by a test that failed two runs in six.
_health_seq = 0


def _health_file() -> Path:
    return data_dir() / "health.json"


def _load_health() -> dict:
    global _health, _health_seq
    with _health_lock:
        if _health:
            return dict(_health)
    try:
        data = json.loads(_health_file().read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    with _health_lock:
        _health = data
        # Carry on from where the saved file left off, so an app restart does
        # not reset the ordering of rows that are already on disk.
        _health_seq = max([int(e.get("seq") or 0) for e in data.values()
                           if isinstance(e, dict)] or [0])
    return dict(data)


def note_health(url: str, state: str, why: str = "") -> None:
    """Record how a site behaved just now."""
    site = site_of(url)
    if not site or site == "Other" or state not in (HEALTH_OK, HEALTH_DOOR,
                                                    HEALTH_DOWN):
        return
    global _health_seq
    _load_health()
    with _health_lock:
        _health_seq += 1
        _health[site] = {"state": state, "when": time.time(),
                         "seq": _health_seq, "why": str(why or "")[:160]}
        snapshot = dict(_health)
    try:
        _health_file().write_text(json.dumps(snapshot), encoding="utf-8")
    except OSError:
        pass


def health() -> list:
    """
    One row per site tried recently, newest first.

    Deliberately not a claim about the site in general - only about what
    happened here. A row older than a week says nothing useful about today,
    so it is dropped rather than shown as if it were current.
    """
    week = time.time() - 7 * 24 * 3600
    rows = []
    for site, entry in _load_health().items():
        when = float(entry.get("when") or 0)
        if when < week:
            continue
        rows.append({"site": site, "state": entry.get("state", HEALTH_OK),
                     "when": when, "seq": int(entry.get("seq") or 0),
                     "why": entry.get("why", ""), "ago": _ago(when)})
    # The counter breaks ties the clock cannot: same instant, later entry wins.
    return sorted(rows, key=lambda r: (r["when"], r["seq"]), reverse=True)


def accounts() -> list:
    """
    Who you actually download from, counted out of your own history.

    Nothing is fetched to build this - the uploader is already recorded with
    every finished download, so this is reading what is there rather than
    asking any site for a list. Newest activity first, because the useful
    question is "what have I been saving lately", not "who is biggest".
    """
    found = {}
    for row in load_history():
        name = str(row.get("uploader") or "").strip()
        if not name:
            continue
        entry = found.setdefault(name, {"name": name, "count": 0,
                                        "site": row.get("site", ""), "last": ""})
        entry["count"] += 1
        when = str(row.get("when") or "")
        if when > entry["last"]:
            entry["last"] = when
            # The site of the most recent one, so a name that moved platforms
            # is filed where it is now rather than where it started.
            entry["site"] = row.get("site", "") or entry["site"]

    return sorted(found.values(), key=lambda e: (e["last"], e["count"]),
                  reverse=True)


def _redacted(path) -> str:
    """A path with the account name taken out of it."""
    text = str(path or "")
    home = os.path.expanduser("~")
    if home and home in text:
        text = text.replace(home, "%USERPROFILE%")
    user = os.environ.get("USERNAME", "")
    if user and len(user) > 2:
        text = text.replace(user, "<user>")
    return text


def diagnostics(version: str = "") -> str:
    """
    One block of text describing this install, for reporting a problem.

    Written to be pasted somewhere, so it holds what actually decides whether
    a download works and nothing that identifies anybody: no cookies, no
    tokens, no pairing keys, no links, and paths with the account name taken
    out. What is left is the version, the tools, the space, and the last
    result per site - which is the set of questions any answer starts with.
    """
    env = environment()
    settings = load_settings()
    room = free_space(settings.get("download_dir", ""))

    lines = [
        "Riplox diagnostics",
        f"app            {version or 'unknown'}",
        f"engine         {engine_version()} ({settings.get('engine_channel', 'stable')})",
        f"media tools    {'yes' if env.get('ffmpeg') else 'MISSING'}",
        f"js runtime     {'yes' if env.get('js') else 'missing'}",
        f"youtube helper {'installed' if env.get('potoken') else 'off'}",
        f"windows        {platform.platform()}",
        f"free space     {human_bytes(room) if room >= 0 else 'unknown'}",
        f"download to    {_redacted(settings.get('download_dir'))}",
        "",
        "settings that change downloads",
        f"  prefer h264        {settings.get('prefer_h264')}",
        f"  polite pacing      {settings.get('polite_mode')}",
        f"  use saved sign-in  {settings.get('cookies_signin')}",
        f"  second door        {settings.get('second_door')}",
        f"  own cookie files   {len(cookie_files(settings))}",
        f"  parallel downloads {settings.get('max_parallel')}",
        f"  schedule           {'on' if settings.get('schedule_on') else 'off'}",
        "",
        "sign-ins saved (names only, never the session)",
    ]

    try:
        import cookies as cookie_store
        status = cookie_store.status()
        for row in status.get("known", []):
            if row.get("signedIn") or row.get("paused"):
                state = "paused" if row.get("paused") else "signed in"
                lines.append(f"  {row['label']:<14} {state}")
        if not any(r.get("signedIn") for r in status.get("known", [])):
            lines.append("  none")
    except Exception:                       # noqa: BLE001
        lines.append("  could not be read")

    lines += ["", "last result per site (this machine only)"]
    rows = health()
    if not rows:
        lines.append("  nothing recorded yet")
    for row in rows:
        lines.append(f"  {row['site']:<14} {row['state']:<5} {row['ago']}"
                     + (f" - {row['why'][:60]}" if row.get("why") else ""))

    return "\n".join(lines) + "\n"


USERNAMES_FILE = "usernames.txt"


def write_usernames(settings: dict = None) -> str:
    """
    One plain-text file in the download folder listing everyone, grouped by
    platform. Returns the path written, or "".

    One file rather than one per platform, deliberately: the question this
    answers is "whose stuff have I got", and an answer split across seven
    files is not an answer. Plain text because it wants to open in Notepad on
    any machine, be searchable, and be pasted somewhere - none of which a
    JSON or CSV does better here.

    Rewritten from history each time rather than appended to, so a cleared
    history leaves a file that agrees with it.
    """
    settings = settings or load_settings()
    folder = Path(settings.get("download_dir") or default_download_dir())
    rows = accounts()

    lines = ["Riplox - who you download from",
             "Updated " + datetime.now().strftime("%Y-%m-%d %H:%M"),
             ""]

    if not rows:
        lines += ["Nothing recorded yet.",
                  "",
                  "This fills in as you download. Anything downloaded before",
                  "this file existed has no uploader saved, so it cannot",
                  "appear here - only new downloads will."]
    else:
        by_site = {}
        for row in rows:
            by_site.setdefault(row.get("site") or "Other", []).append(row)

        lines.append(f"{len(rows)} names across {len(by_site)} platforms")
        lines.append("")
        for site in sorted(by_site):
            lines.append(site)
            lines.append("-" * len(site))
            for row in sorted(by_site[site], key=lambda r: -r["count"]):
                when = (row.get("last") or "")[:10]
                count = row["count"]
                lines.append("  {:<34} {:>4}  {}".format(
                    row["name"][:34], count, when))
            lines.append("")

    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / USERNAMES_FILE
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(target)
    except OSError:
        return ""


def _ago(when: float) -> str:
    gap = max(0, time.time() - when)
    if gap < 90:
        return "just now"
    if gap < 3600:
        return f"{int(gap // 60)} min ago"
    if gap < 86400:
        return f"{int(gap // 3600)} h ago"
    return f"{int(gap // 86400)} d ago"


_BAD_IN_NAMES = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(text: str) -> str:
    """
    A title Windows will accept as a file name.

    yt-dlp does this itself for the names it writes; a direct download has to
    do it here, and getting it wrong means an exception at the very end of a
    download that otherwise worked.
    """
    cleaned = _BAD_IN_NAMES.sub("", str(text or "")).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    # CON, PRN, NUL and friends are still reserved, whatever the extension.
    if cleaned.upper().split(".")[0] in (
            "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
            "LPT1", "LPT2", "LPT3"):
        cleaned = "_" + cleaned
    return cleaned or "video"


def _url_tail(url: str) -> str:
    """The last meaningful part of a URL, for when nothing supplied an id."""
    path = urlsplit(url or "").path.rstrip("/")
    tail = path.rsplit("/", 1)[-1] if path else ""
    return _safe_name(tail)[:40] or "link"


def _is_youtube(url: str) -> bool:
    low = (url or "").lower()
    return "youtube.com" in low or "youtu.be" in low


def _is_transient(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _TRANSIENT)


# The height each named quality is asking for. "best" and "max" name no
# number - they ask for whatever the site has - which is why they are absent,
# and why the check below has to go and look rather than compare.
_ASKED_HEIGHT = {"4320": 4320, "2160": 2160, "1440": 1440, "1080": 1080,
                 "720": 720, "480": 480, "360": 360}

# Above this a fallback route's answer is not worth questioning. Measured
# 1 Sep 2026: the clients Riplox falls back to top out at exactly 360p when no
# proof-of-origin token can be minted, so anything taller did not come from a
# degraded route and needs no second look.
_FALLBACK_CEILING = 360

# How close to the request still counts as answering it. 1080 asked and 1080
# given is exact; sites do sometimes hand back the rung just below, and asking
# the site again over that would spend a listing to say almost nothing.
_SHORT_ENOUGH = 0.9


def best_height(url: str, settings: dict, cookie_path=None) -> int:
    """
    The tallest video the MAIN route can see for this link. 0 when it cannot
    be asked - and 0 deliberately means "do not complain", so a link that
    cannot be re-read never produces a warning about a file that is fine.

    This exists for one question: a small file is either a small video or a
    download that went wrong, and those need opposite answers. Without asking,
    the honest message would have to say "may be", and a maybe-warning on a
    video that only ever had 360p is exactly the kind of misleading line that
    teaches people to ignore warnings.

    The default client on purpose: it is the one measured to see every format,
    and the question being asked is "was there a better one we missed".
    """
    args = _base_args(settings, cookie_path)
    args += ["-J", "--no-playlist", "--no-progress", url]
    try:
        out = _run(args, timeout=90)
    except (subprocess.TimeoutExpired, OSError):
        return 0
    if out.returncode != 0 or not (out.stdout or "").strip():
        return 0
    try:
        info = json.loads(out.stdout)
    except ValueError:
        return 0
    return max((int(f.get("height") or 0)
                for f in (info.get("formats") or [])), default=0)


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
                 "speed", "eta", "size", "got", "filepath", "error", "created", "proc",
                 "cancelled", "uploader", "batch", "log", "attempt",
                 "start", "end", "exact", "stage", "paused", "kind", "conv",
                 "opts", "origin", "streams", "parts", "sent_cookies", "tried_signed_in",
                 "account", "retry_at", "auto_retries", "height", "started_on",
                 "net_waits", "start_after",
                 # When the engine last said ANYTHING, and whether it went
                 # quiet for so long that we gave up on it. See _SILENCE_LIMIT.
                 "heard", "went_quiet",
                 # Where the current fragment began: its index, and the byte
                 # count when it started. That pair is what lets the bar
                 # measure its way across a fragment instead of dividing by an
                 # estimate that moves. See _apply_progress.
                 "frag_base_at", "frag_base_bytes",
                 # The size currently on screen, kept so it can stay there
                 # while the estimate behind it wobbles. See _settled_size.
                 "size_shown",
                 # Whether the size beside it is measured or extrapolated. Kept
                 # apart from the string on purpose - three different readers
                 # parse that string back into a number.
                 "size_estimated")

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
        # The last time the engine said anything at all, and whether it stopped
        # saying anything for long enough that Riplox gave up waiting.
        self.heard = 0.0
        self.went_quiet = False
        # Whether the last attempt actually carried a saved session. Recorded
        # rather than worked out again, because "would cookies be sent for
        # this URL" is a question with several answers and only one of them
        # is what happened.
        self.sent_cookies = False
        # Whether a saved session was carried at any point in this job's life,
        # which is a different question from the one above and the only one an
        # error message may speak for. The signed-out retry deliberately runs a
        # second attempt without cookies, and that attempt overwrites both
        # sent_cookies and log - so by the time anyone reads the record, a job
        # that really was tried signed in looks as though it never was. Sticky
        # once set: this is the job's history, not the last attempt's state.
        self.tried_signed_in = False
        # Which of the site's accounts signed the last attempt. 0 means none -
        # either the site has no sign-in here or this attempt went signed out.
        self.account = 0
        # The height the file actually came out at, once it exists. 0 until
        # then, because before it lands the only honest answer is what was
        # asked for.
        self.height = 0
        # Which network this attempt began on, and how many times it has been
        # put back because the network went away. The count is what stops a
        # laptop shut in a bag for a week from coming back to a job that has
        # re-run four hundred times.
        self.started_on = ""
        self.net_waits = 0
        # What has arrived so far, as text. The percentage alone does not
        # answer "how much longer on this connection" - "45.2 MB of 342.0 MB"
        # does, and yt-dlp was already sending both numbers.
        self.got = ""
        # When to try this one again on its own, and how many of those goes
        # have been used. Only ever set for a refusal that is known to pass.
        self.retry_at = 0.0
        self.auto_retries = 0
        # Not before this time, when the user has named one. The window in
        # Settings says "not during the day"; this says "this one, at two in
        # the morning" - a different question, and neither answers the other.
        self.start_after = 0.0
        # Converting shares the queue with downloading: same progress, same
        # Cancel, same notifications, nothing new to invent.
        self.kind = "download"
        self.conv = {}
        # What a trimmed download is doing while yt-dlp reports no percentage.
        self.stage = ""
        # How many streams have finished. A merged download is video then
        # audio, and the one progress bar has to cover both.
        self.streams = 0
        self.frag_base_at = 0.0
        self.frag_base_bytes = 0.0
        self.size_shown = 0.0
        self.size_estimated = False
        # How many of a cut's parts have arrived. One job, many files.
        self.parts = 0
        # Kept so the user can hand a real error to someone who can read it,
        # instead of the one friendly sentence the UI shows.
        self.log = ""
        self.attempt = 0

    def _quality_label(self) -> str:
        """
        What to show beside a finished row.

        "Best available" is the app's word, not an answer: it does not say
        whether 4K or 720p came back, and that is the one thing somebody
        looking at a finished download wants to know. So once the file exists
        it is replaced by the height it really came out at.

        Only for "best". "Highest" was chosen deliberately and already says
        what it means, and every numbered rung is its own answer already.
        """
        if self.quality == "best" and self.height:
            return f"{self.height}p"
        return QUALITY_LABELS.get(self.quality, self.quality)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "uploader": self.uploader,
            "quality": self.quality,
            "qualityLabel": self._quality_label(),
            "status": self.status,
            "percent": round(self.percent, 1),
            "sizeEstimated": self.size_estimated,
            "speed": self.speed,
            "eta": self.eta,
            "size": self.size,
            "got": self.got,
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
            # Seconds until Riplox tries this one again by itself, or 0. Said
            # out loud because a row that sits there saying "failed" while a
            # retry is coming is the app keeping a secret.
            "retryIn": max(0, int(self.retry_at - time.time())) if self.retry_at else 0,
            # Seconds until a start time the user set arrives, or 0. Same
            # reason as retryIn: a row that waits without saying why reads as
            # a row that is stuck.
            "startsIn": (max(0, int(self.start_after - time.time()))
                         if self.start_after else 0),
        }


# How far the truth must move before the number on screen is worth changing.
# 25% was measured: it takes the changes over a whole download from 89 to 7,
# and the error at the halfway mark does not move at all - 8% either way,
# because the underlying estimate is already wrong by more than the band.
_SIZE_BAND = 0.25


def _settled_size(byte_count: float, held: float = 0.0) -> float:
    """A size to read, not a size to watch.

    yt-dlp's total is an extrapolation - the average fragment so far times how
    many there are - and it moves the whole way through a download: 147 MB to
    353 MB on one measured run, and 174 MB downwards on another. Showing every
    reading made it change 484 times, and rounding alone still left 89.

    So the number already on screen stays there until the truth has left a band
    around it. Nothing is held back and nothing is smoothed: when the estimate
    genuinely moves, this follows it in one step.

    ⚠️ Deliberately NOT one-directional. Holding the maximum scores better on
    the two downloads measured here - 2 changes instead of 7 - and both of them
    happen to have estimates that climb. One that FELL, 88 MB to 37 MB on a
    37.3 MB file, was measured earlier and reported: holding the maximum showed
    88 for the entire download. A band recovers from that by itself.
    """
    mb = byte_count / 1048576.0
    # Below this, rounding costs more than it buys - and rounding a 100-byte
    # file to the nearest megabyte reports zero, which is how this was caught.
    if mb < 10:
        return byte_count
    step = 5.0 if mb < 200 else 25.0
    rounded = round(mb / step) * step * 1048576.0

    if held and abs(byte_count - held) / held < _SIZE_BAND:
        return held
    return rounded


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
                     "opts": j.opts, "origin": j.origin,
                     # A start time outlives a restart, or "at two in the
                     # morning" would quietly become "now" the next time the
                     # app opened.
                     "start_after": j.start_after,
                     # Written for the browser extension, which reads this file
                     # to put a count on its toolbar icon. It was looking for a
                     # status that was never saved here, so it read every queue
                     # as empty and the badge could never show anything at all.
                     # restore() does not consult it - it deliberately brings
                     # everything back paused - so nothing here changes.
                     "status": j.status}
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
                try:
                    job.start_after = float(item.get("start_after") or 0)
                except (TypeError, ValueError):
                    job.start_after = 0.0
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

    def pause_all(self) -> int:
        """
        Stop everything that is running or waiting, keeping the part-files.

        The ids are taken under the lock and then paused outside it: pausing
        kills a process, which is not something to do while holding the lock
        every other worker needs.
        """
        with self._lock:
            ids = [i for i in self._order
                   if i in self._jobs and self._jobs[i].status in self.ACTIVE]
        stopped = sum(1 for i in ids if self.pause(i))
        self._save()
        return stopped

    def retry_all(self) -> int:
        """
        Put every failed download back in the queue.

        Deliberately not the paused ones, even though retry() would take them:
        those were stopped on purpose and Resume all is the button for them.
        A Retry all that also restarted them would be a surprise.
        """
        with self._lock:
            ids = [i for i in self._order
                   if i in self._jobs
                   and self._jobs[i].status in ("error", "cancelled")]
        return sum(1 for i in ids if self.retry(i))

    def add(self, url, title="", thumbnail="", quality="best", uploader="",
            batch=False, start="", end="", exact=False, opts=None,
            origin="", start_after=0.0) -> Job:
        job = Job(url, title, thumbnail, quality, uploader, batch, start, end,
                  exact, opts, origin)
        # Set before the job is visible to anything else. Setting it on the
        # way out instead leaves a gap - the job is queued, the workers have
        # been woken, and one of them can start it in the moment before its
        # start time is written. Measured, not imagined: a test caught a
        # 2 a.m. download beginning immediately.
        job.start_after = float(start_after or 0.0)

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

    def add_convert(self, source, fmt: str, quality: str, target_dir="",
                    scale: str = "") -> Job:
        """Queue a file already on disk for conversion, not a download."""
        source = Path(source)
        job = Job(str(source), title=source.name, quality=fmt)
        job.kind = "convert"
        job.conv = {"source": str(source), "fmt": fmt, "quality": quality,
                    "target_dir": str(target_dir or source.parent),
                    "scale": str(scale or "")}

        with self._lock:
            for existing in self._jobs.values():
                # Same file, same format, same height is the same job. A
                # different height is not - asking for 1080p and 720p of one
                # video is two answers, and merging them would silently drop
                # the second.
                if (existing.kind == "convert"
                        and existing.conv.get("source") == str(source)
                        and existing.conv.get("fmt") == fmt
                        and existing.conv.get("scale", "") == str(scale or "")
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
            # Pressed by hand, so the wait Riplox had planned is beside the
            # point - and the goes it had left are given back, because this is
            # someone saying "now", not "instead".
            job.retry_at = 0.0
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

        # Nothing goes out while there is no network: starting a download now
        # would spend its retries on an outage and land it in Failed, which is
        # what this exists to remove.
        #
        # ⚠️ But never for ever. A probe that is simply wrong - a network that
        # blocks the hosts it asks - would otherwise stop this machine
        # downloading anything, permanently and silently. So after a couple of
        # minutes of claimed silence one job goes out anyway and settles it
        # with real traffic.
        if not network_ok():
            first = _net_offline_since[0] or time.monotonic()
            _net_offline_since[0] = first
            if time.monotonic() - first < _NET_DOUBT_AFTER:
                return None
        else:
            _net_offline_since[0] = 0.0

        want = max(1, min(5, int(settings.get("max_parallel", 2))))
        with self._lock:
            # Anything whose own wait is up goes back in the queue. Here
            # rather than on a timer of its own: this runs constantly, and a
            # second thread to move one field would be a second thing to get
            # wrong.
            now = time.time()
            for job in self._jobs.values():
                if job.retry_at and job.retry_at <= now and job.status == "error":
                    job.retry_at = 0.0
                    job.status = "queued"
                    job.error = ""
                    job.percent = 0.0
                    job.attempt = 0

            active = sum(1 for j in self._jobs.values()
                         if j.status in ("downloading", "converting", "starting"))
            if active >= want:
                return None
            for jid in self._order:
                job = self._jobs.get(jid)
                if not job or job.status != "queued":
                    continue

                # A start time the user set holds back that one row and
                # nothing else: the rest of the queue carries on around it.
                if job.start_after and job.start_after > now:
                    continue

                # A site that has already refused, or one that was asked
                # moments ago, is skipped rather than the whole queue being
                # stopped: everything else carries on downloading while one
                # site waits its turn.
                # A site whose every account is resting waits; one with a spare
                # that is free does not, and neither does any other site.
                site = site_of(job.url) if job.kind != "convert" else ""
                if site and (account_wait(site) or pace_left(site, settings)):
                    continue

                job.status = "starting"
                if site:
                    note_started(site)
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
            self._remember_outcome(job)
            self._save()
            self._wake.set()

    def _remember_outcome(self, job: Job) -> None:
        """
        Write down a job that failed - or note that a remembered one worked.

        Here rather than at each of the dozen places that set "error": this is
        the one point every job passes through on its way out, whichever route
        it took and however it went wrong. A list of failures with holes in it
        would be worse than no list, because the holes are invisible.
        """
        if job.cancelled or job.status in ("cancelled", "paused"):
            return
        if job.status == "done":
            note_failure_fixed(job.url, job.quality)
            return
        if job.status != "error":
            return

        # A wall that lifts by itself is worth waiting out rather than making
        # the user press retry at the right moment. Set before the cooldown
        # below, which is about the opposite kind of refusal.
        if (job.kind != "convert" and clears_on_its_own(job.error)
                and job.auto_retries < len(AUTO_RETRY_AFTER)):
            wait = AUTO_RETRY_AFTER[job.auto_retries]
            job.auto_retries += 1
            job.retry_at = time.time() + wait
            job.error = (f"{job.error}\n\nRiplox will try this one again in "
                         f"{wait // 60} minutes on its own.").strip()

        # "Too many requests" is not a failure to retry - it is the site
        # asking to be left alone, and the next job in the queue asking again
        # a second later is what turns that into something worse. Read from
        # this job's own words only.
        if job.kind != "convert" and looks_rate_limited(
                f"{job.error}\n{job.log}"):
            site = site_of(job.url)
            account = int(getattr(job, "account", 0) or 0)
            until = start_cooldown(site, (job.error or "")[:120], account)
            whose = "that account is" if account else f"{site} is"
            job.error = (f"{job.error}\n\n{site} asked Riplox to slow down, so "
                         f"{whose} being left alone until "
                         f"{time.strftime('%H:%M', time.localtime(until))}. "
                         f"Everything else carries on as normal.").strip()

        record_failure({
            "url": job.url,
            "title": job.title or job.url,
            "quality": job.quality,
            "thumbnail": job.thumbnail,
            "uploader": job.uploader,
            "site": site_of(job.url),
            "from": job.origin,
            "kind": job.kind,
            # Kept so retrying from that page is the same download again -
            # same folder, same trim - rather than a plainer one that happens
            # to have the same address.
            "opts": dict(getattr(job, "opts", None) or {}),
            "error": (job.error or "")[:400],
            "log": (job.log or "")[-FAILED_LOG_KEEP:],
        })

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

        # And so does a chosen dub, for exactly the same reason: the Hindi and
        # the English of one video are the same title at the same height, so
        # without this the second one lands on top of the first and nothing
        # says so. Only added when a language was actually chosen, so every
        # ordinary download keeps the name it has always had.
        dub = ""
        lang = (getattr(job, "opts", None) or {}).get("audio_lang") or ""
        if lang:
            dub = f" [{_safe_name(lang)[:12]}]"

        # And so does the re-upload quality, for the third time and the worst
        # reason. "Best available" and "Highest" can settle on the same height
        # - 2160p picked with H.264 preferred and 2160p picked at the highest
        # bitrate are different files with identical names - and when they
        # collide yt-dlp does not overwrite. It prints "has already been
        # downloaded", exits 0, and the row goes green over the very file the
        # user chose this quality to improve on. The rest of the intent was
        # already here: extra_args deliberately keeps max out of the archive.
        pick = " [max]" if job.quality == "max" else ""

        if opts.get("clips") and not (opts.get("chapters")
                                      or opts.get("chapters_all")):
            # One folder per video, holding one file per moment.
            #
            # Named by the second it starts at, zero-padded so the folder
            # sorts in the order the moments happen. It cannot be named after
            # the moment itself: measured on the bundled binary, a time range
            # comes back with section_title AND section_number both NA - those
            # only exist when the section was picked by chapter name.
            stamp = " [mp3]" if job.quality == "mp3" else " %(height)sp"
            folder = f"%(title).100B [%(id)s]{stamp}{pick}{dub}"
            return str(root / "Clips" / folder /
                       "%(section_start)05ds-%(section_end)05ds.%(ext)s")

        if opts.get("chapters") or opts.get("chapters_all"):
            # One folder per video, holding one file per chapter.
            #
            # The id is in the folder name because two different videos can
            # share a title, and the quality is there for the same reason it
            # is in an ordinary file name: the same chapters at 720p and at
            # 1080p are different files, and without it the second run finds
            # the first already downloaded and stops with nothing said.
            #
            # Numbered from one. yt-dlp counts sections from zero, and a
            # folder that starts at "00 - Intro" reads as a fault; the
            # template can do the arithmetic - measured, not assumed.
            stamp = " [mp3]" if job.quality == "mp3" else " %(height)sp"
            folder = f"%(title).100B [%(id)s]{stamp}{pick}{dub}"
            return str(root / "Chapters" / folder /
                       "%(section_number+1)02d - %(section_title)s.%(ext)s")

        # The app's own name, at the end.
        #
        # At the END and not the front on purpose: a folder of downloads still
        # sorts by title, which is how people actually look for them. It rides
        # along when a file is shared or uploaded again, which is the point.
        #
        # ⚠️ A name typed by hand above never gets this - somebody who named
        # the file meant the name they typed.
        mark = " Riplox"

        if job.quality == "mp3":
            # Audio lands as .mp3, so it can never collide with a video file.
            return str(root / f"%(title).110B [%(id)s]{dub}{clip}{mark}.%(ext)s")

        # Height belongs in the name: without it, grabbing the same video at
        # 720p and then at 1080p silently overwrote the first file.
        return str(root / f"%(title).100B [%(id)s] %(height)sp{pick}{dub}{clip}{mark}.%(ext)s")

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

        if self._run_engine(job, settings) or job.cancelled:
            if job.status == "done":
                note_health(job.url, HEALTH_OK)
            return

        # ⚠️ Put back on the queue to wait for the network - not failed, and so
        # not finished with. Going on from here undid that entirely: the second
        # door below sets a status of its own whatever it finds, so a job that
        # was safely waiting became a failed one seconds later and the wait
        # counted for nothing. The engine never got a fair attempt, so nothing
        # has been ruled out and there is nothing yet for a fallback to fall
        # back from. This is the one route out of _run_engine that is not an
        # answer about the video.
        if job.status in self.ACTIVE:
            return

        # A refusal aimed at the session rather than at the video: worth one
        # more go with the session left out, before anything more elaborate.
        if self._signed_out_retry(job, settings) or job.cancelled:
            if job.status == "done":
                note_health(job.url, HEALTH_OK, "after signing out")
            return

        # yt-dlp has had every attempt it is going to get and the link is
        # still not downloaded. Before the user is told no, try Riplox's own
        # way in - which exists precisely for the days yt-dlp is refused.
        self._second_door(job, settings)

        # Whichever way that went is the useful thing to remember: the engine
        # failing while the direct route works is exactly the early warning a
        # status line exists to give.
        if job.status == "done":
            # No reason given: the state already says the engine was refused,
            # and repeating it beside itself reads as two separate facts.
            note_health(job.url, HEALTH_DOOR)
        elif not job.cancelled:
            note_health(job.url, HEALTH_DOWN, job.error)

    # Enough for a network that comes and goes; short of a machine that has
    # been asleep for a week and would otherwise re-run this for ever.
    _NET_WAIT_CAP = 40

    def _network_went(self, job: Job) -> bool:
        """
        Did this attempt fail because the network left, or changed?

        Two different things, and neither is the download's fault:

          * the network is GONE - the probe says so outright.

          * the network CHANGED. The probe cannot see this: the internet is
            fine, it is simply a different internet, and the media URL in hand
            was issued to the old address. YouTube answers 403, which is
            correct of it and useless here. A fresh run gets fresh URLs.

        Returns True when the job has been put back on the queue rather than
        failed, so the caller stops spending its ladder of retries.
        """
        if job.cancelled:
            return False

        # ⚠️ The LOG is asked before the probe. The probe answers "is there a
        # network NOW", and by the time an attempt has failed the answer is
        # usually yes again - the connection came back while yt-dlp was still
        # spending its retries. Measured on a real job: seventeen "getaddrinfo
        # failed" lines, the network back before the process exited, the probe
        # green and the same Wi-Fi as before, so both of the old signals said
        # "not our problem" and the job went to Failed for ever over an outage
        # that had already ended. What happened was written down; it does not
        # have to be guessed at afterwards.
        #
        # ⚠️ But not while a proxy is in play. A proxy hostname that will not
        # resolve produces these very same lines, and that is a setting to
        # correct rather than an outage to wait out - waiting would hide the
        # one thing the user has to change. The log is believed only when
        # Riplox is going out directly, where the name that failed can only
        # have been the site's own.
        by_log = network_lost(job.log) and not clean_proxy(
            load_settings().get("proxy"))
        gone = by_log or not network_ok(force=True)
        moved = bool(job.started_on) and here_now() not in ("", job.started_on)
        if not (gone or moved):
            return False

        job.net_waits += 1
        if job.net_waits > self._NET_WAIT_CAP:
            # Said plainly rather than left waiting for ever: at some point
            # "the network never came back" is the honest answer.
            job.status = "error"
            job.error = ("The network kept dropping, so Riplox stopped trying. "
                         "Press Retry when the connection is steady.")
            return True

        job.status = "queued"
        job.error = ""
        job.percent = 0.0
        job.speed = job.eta = ""
        job.attempt = 0
        job.started_on = ""
        self._save()
        return True

    def _run_engine(self, job: Job, settings: dict) -> bool:
        """yt-dlp's attempts. True when the file is on disk."""
        plans = _RETRY_CLIENTS if _is_youtube(job.url) else _PLAIN_RETRIES
        job.started_on = here_now()

        for index, client in enumerate(plans):
            job.attempt = index + 1

            # 🔴 A broken connection is retried on THIS rung. The rungs below
            # are different player clients, chosen to get past a refusal, and
            # they are only ever offered small formats - so spending one on a
            # Wi-Fi hiccup answers "best available" with 360p and calls it
            # done. Reported from real use: an https error halfway through,
            # and the file that arrived was a fraction of what was asked for.
            for again in range(_SAME_RUNG_TRIES):
                if self._attempt(job, settings, client) or job.cancelled:
                    return job.status == "done"
                if not _is_network_trouble(job.log):
                    break                  # a refusal - that is what rungs are for
                # The network leaving, or changing, is already handled better
                # elsewhere: the job goes back on the queue and starts again
                # with fresh URLs. Asked first so this loop never competes
                # with it.
                if self._network_went(job) or again + 1 >= _SAME_RUNG_TRIES:
                    break
                job.status = "starting"
                job.error = ""
                job.speed = job.eta = ""
                deadline = time.monotonic() + 2 + 3 * again
                while time.monotonic() < deadline:
                    if job.cancelled:
                        job.status = "paused" if job.paused else "cancelled"
                        return False
                    time.sleep(0.2)
            if job.cancelled or job.status in ("queued", "paused", "cancelled"):
                return False

            # ⚠ Asked BEFORE another rung is spent. The whole ladder used to
            # run inside about twenty seconds - shorter than a Wi-Fi-to-mobile
            # switch takes - so every attempt was burned while there was no
            # network to use, and the job landed in Failed over a fault that
            # had already fixed itself.
            if self._network_went(job):
                return False

            last = index + 1 >= len(plans)
            if last or not _is_transient(job.log):
                return False

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
                    return False
                time.sleep(0.2)

        return False

    def _quality_short(self, job: Job, settings: dict) -> str:
        """
        Did a fallback route quietly hand over a far smaller video?
        Returns the sentence to show, or "" when there is nothing wrong.

        Exiting 0 is not the same as doing what was asked. When the main route
        is refused, Riplox asks YouTube as a different player client - and some
        of those are only ever offered small formats. The download then
        succeeds, the row goes green, and a request for 4K is answered with a
        360p file whose name still says [max]. Reported from real use.

        ⚠️ The check is on the RESULT, not on a list of client names. Which
        clients are degraded changes every few weeks; that a 4K request came
        back at 360p does not.
        """
        # The main route's answer IS the truth about this video - if it says
        # 360p, 360p is what there is. Only a fallback's answer is suspect.
        if job.attempt <= 1 or job.kind != "download":
            return ""
        # A folder of chapters or clips, an audio extraction, subtitles only:
        # no single height to judge, and the parts check above owns those.
        if (job.opts.get("chapters") or job.opts.get("chapters_all")
                or job.opts.get("clips") or job.opts.get("subs_only")):
            return ""

        height = int(getattr(job, "height", 0) or 0)
        if not height:
            return ""

        asked = _ASKED_HEIGHT.get(job.quality, 0)
        if asked and height >= asked:
            return ""            # they asked for small and got small

        # ⚠️ This used to stop at 360p, because that was the ceiling measured
        # on the fallback clients of the day. It made the check true and
        # narrow: a request for 4K answered with 720p walked straight past it
        # and went green. Which heights a degraded route can reach is a fact
        # with a shelf life; "far short of what was asked" is not. Anything
        # visibly below the request is now worth the one listing it costs to
        # find out - and a listing is only spent when a fallback rung was
        # used at all, which is rare.
        if asked and height >= asked * _SHORT_ENOUGH:
            return ""            # near enough - not worth a request to confirm
        if not asked and height > _FALLBACK_CEILING:
            # "max" and "best" name no number, so there is nothing to be short
            # of until the answer is small enough to be suspicious on its own.
            return ""

        # Now, and only now, is it worth a request: ask the main route what
        # this video actually has. Costs one listing, on a path that should be
        # rare, and buys a message that is true rather than hedged.
        cookie_path, temp_cookie, _account = open_cookies(settings, job.url)
        try:
            available = best_height(job.url, settings, cookie_path)
        finally:
            close_cookies(cookie_path, temp_cookie)

        # Could not ask, or there really is nothing better: say nothing.
        if available <= height:
            return ""

        return (f"This came back at {height}p, but the video has "
                f"{available}p. Riplox's usual way in was refused, and the "
                f"way round it only carries small formats. The {height}p file "
                f"is saved - press Retry to ask again for the full one.")

    # A site turning a request down flat, rather than the download going wrong.
    _AUTH_REFUSED = ("http error 400", "http error 401", "http error 403",
                     "login required", "login_required", "checkpoint",
                     "requested content is not available")

    def _signed_out_retry(self, job: Job, settings: dict) -> bool:
        """
        One more attempt with the saved session deliberately left out.

        A stale login is not a neutral thing to send. The site rejects the
        request outright and the engine stops there, instead of falling back
        to the signed-out route it would have taken had there been no session
        at all - so an expired login takes public videos down with it. That is
        not a guess: forgetting the Instagram session here made two reels that
        had failed repeatedly download on the next press.

        Pausing that site in Settings is the deliberate version of this. This
        is the automatic one, for the case where nobody knew to.
        """
        if job.cancelled or not job.sent_cookies:
            return False
        low = (job.error or "").lower() + "\n" + (job.log or "").lower()
        if not any(mark in low for mark in self._AUTH_REFUSED):
            return False

        refused = job.error
        # Kept because the attempt below overwrites job.log wholesale, and that
        # log is the only record that a session was ever sent.
        signed_in_log = job.log
        job.status = "starting"
        job.error = ""
        job.percent = 0.0
        if self._attempt(job, settings, "", with_cookies=False):
            if job.status == "done":
                job.log += ("\n\nThe saved sign-in was refused, so this was "
                            "downloaded signed out instead. If that keeps "
                            "happening, sign in again or pause that site in "
                            "Settings.")
                return True

        # Signed out did not help either, so the first refusal is the one worth
        # showing - it is the one that says a session was rejected.
        if job.status != "done" and refused:
            job.error = refused
            # And the log has to match the error. _spawn assigns job.log rather
            # than adding to it, so by now the signed-out attempt has replaced
            # the record of the signed-in one - leaving an error that speaks of
            # a rejected session next to a log with no session in it. Anyone
            # reading that record afterwards concludes the session was never
            # sent, which is exactly the wrong turning this whole diagnosis
            # took once already.
            if signed_in_log:
                job.log = (f"{signed_in_log}\n\n"
                           f"--- signed in was refused, so this was retried "
                           f"signed out ---\n\n{job.log}")
        return job.status == "done"

    # ----------------------------------------------------------------------
    # The second door
    # ----------------------------------------------------------------------

    # Long enough for a slow line on a short video, short enough that a stalled
    # fallback does not hold a queue slot all afternoon.
    _DOOR_CAP = 8 * 60

    def _door_path(self, settings: dict, job: Job, info: dict) -> Path:
        """Where a direct download lands, named the way yt-dlp's would be."""
        opts = getattr(job, "opts", None) or {}
        if opts.get("dest_dir"):
            root = Path(opts["dest_dir"])
        else:
            root = Path(settings["download_dir"])
            if settings.get("subfolder_per_site"):
                root = root / (site_of(job.url) or info.get("site") or "Riplox")

        # Same shape as _outtmpl, minus the height: there is one stream on
        # offer here, so there is nothing for a height to tell two files apart.
        stem = _safe_name(info.get("title") or job.title or "video")[:100]
        name = f"{stem} [{info.get('id') or _url_tail(job.url)}]"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{name}.{info.get('ext') or 'mp4'}"

    # Containers whose own spec has a place to put a cover picture. Anything
    # else keeps the picture beside it as a file, which every media library
    # reads anyway - and is a great deal better than an ffmpeg run that
    # rewrites a finished download and might not survive it.
    _COVER_INSIDE = {".mp4", ".m4a", ".mp3", ".m4v", ".mov"}

    def _cover(self, job: Job, address: str, proxy: str = "") -> str:
        """
        Keep the cover picture the user chose, beside the file and - where the
        container allows - inside it.

        The engine has one thumbnail flag and it means "the default one", so a
        chosen cover cannot be asked for on the command line; it is fetched
        afterwards. Which is also why nothing here is allowed to fail loudly:
        the video downloaded, and a cover picture is not worth turning a
        finished download into a failed one over. Returns a line for the log.
        """
        target = Path(job.filepath or "")
        if not address or not target.exists():
            return ""

        beside = target.with_suffix(".jpg")
        try:
            pull_to_file(address, beside, {"User-Agent": "Riplox"},
                         time.monotonic() + 60, proxy=proxy)
        except Exception as exc:                # noqa: BLE001
            beside.unlink(missing_ok=True)
            return f"the cover picture could not be fetched: {exc}"

        ffmpeg = ffmpeg_path()
        if ffmpeg is None or target.suffix.lower() not in self._COVER_INSIDE:
            return f"cover picture saved as {beside.name}"

        stamped = target.with_suffix(".cover" + target.suffix)
        try:
            done = subprocess.run(
                [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(target), "-i", str(beside),
                 "-map", "0", "-map", "1", "-c", "copy",
                 "-disposition:v:1", "attached_pic", str(stamped)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            stamped.unlink(missing_ok=True)
            return f"cover picture saved as {beside.name} (not embedded: {exc})"

        if done.returncode != 0 or not stamped.exists():
            stamped.unlink(missing_ok=True)
            return f"cover picture saved as {beside.name} (it would not embed)"

        try:
            stamped.replace(target)
        except OSError:
            stamped.unlink(missing_ok=True)
            return f"cover picture saved as {beside.name} (it would not embed)"
        # Kept on disk as well as inside: it costs a few KB and it is what a
        # media library looks for when it will not open the file itself.
        return f"cover picture embedded, and saved as {beside.name}"

    def _door_pull(self, address: str, where: Path, headers: dict,
                   deadline: float, progress, job: Job,
                   proxy: str = "") -> None:
        """
        Fetch one stream, picking the connection back up when it drops.

        pull_to_file already knows how to continue a .part it wrote itself -
        it stamps the address beside the file and asks for a byte range on the
        way back in. What it does not do is come back on its own, and for
        YouTube it has to.

        Measured on 2026-08-18, reading 12 MB off a 1080p stream from this
        machine: 0.41 MB/s on a plain GET, 0.34 with a whole-file Range, 0.40
        in 4 MB segments and 0.40 in 10 MB ones. So the rate is the same
        whatever is asked for - the address is simply served slowly, and a
        connection held open that long is dropped part-way more often than
        not. Retrying is the only thing that helps, and because each attempt
        resumes, three of them are three parts of one download rather than
        three starts.

        Gives up when an attempt adds nothing: a stream that will not move is
        a different problem, and hammering it is how a queue slot is wasted
        all afternoon.
        """
        last = ""
        for attempt in range(6):
            before = where.stat().st_size if where.exists() else 0
            try:
                pull_to_file(address, where, headers, deadline,
                             on_progress=progress, proxy=proxy,
                             timed_out="The direct download kept dropping. "
                                       "What arrived is kept, so retry "
                                       "carries on.")
                return
            except Exception as exc:            # noqa: BLE001
                last = str(exc)
                after = where.stat().st_size if where.exists() else 0
                if job.cancelled or time.monotonic() > deadline or after <= before:
                    break
                # A short pause rather than straight back in: the drops arrive
                # in runs, and the next second is the worst time to ask.
                time.sleep(min(1.5 * (attempt + 1), 5.0))
        raise OSError(last or "The direct download stopped and would not resume.")

    def _door_join(self, video: Path, audio: Path, job: Job,
                   container: str = "mp4") -> str:
        """
        Put the two halves YouTube hands out back together.

        Copied, never re-encoded. The streams already carry the codecs the
        file is meant to hold, and re-encoding a 2160p video to attach its own
        audio would cost minutes of CPU and some quality to change nothing.

        Returns "" when it worked, or a sentence worth showing when it did not.
        """
        ffmpeg = ffmpeg_path()
        if ffmpeg is None:
            # Should be unreachable: two streams are only ever requested when
            # has_ffmpeg() was true. Kept because "unreachable" and "never
            # happens" are different things, and a silent half-file is the
            # outcome this whole module exists to prevent.
            return ("Both halves of that video arrived, but the tool that "
                    "joins them is missing. Reinstall Riplox.")

        joined = video.with_suffix(video.suffix + ".joined")
        try:
            done = subprocess.run(
                [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(video), "-i", str(audio),
                 # Named rather than guessed from the name. These are working
                 # files called .part.joined, and ffmpeg reads the container
                 # off the extension - which is how this first came back as
                 # "Error opening output files: Invalid argument".
                 "-f", container,
                 "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
                 # So the file can start playing before it has all arrived,
                 # which is what a viewer expects of anything in a folder.
                 "-movflags", "+faststart", str(joined)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            joined.unlink(missing_ok=True)
            return f"Joining the video and its audio failed: {exc}"

        if done.returncode != 0 or not joined.exists():
            joined.unlink(missing_ok=True)
            said = [line for line in (done.stderr or "").splitlines() if line.strip()]
            return ("Joining the video and its audio failed"
                    + (f": {said[-1].strip()}" if said else "."))

        try:
            joined.replace(video)
        except OSError as exc:
            joined.unlink(missing_ok=True)
            return f"Could not save the joined file: {exc}"
        # Only once the join is safely in place - a half that is deleted before
        # its replacement exists is a download thrown away.
        audio.unlink(missing_ok=True)
        return ""

    def _second_door(self, job: Job, settings: dict) -> None:
        """
        Riplox's own way in, once yt-dlp has given up.

        Kept strictly as a fallback. A door that ran first would quietly take
        over sites yt-dlp handles better, and the day it broke nobody would
        know why - so it only ever runs on a link that has already failed.
        """
        import doors

        if not doors.handles(job.url) or not settings.get("second_door", True):
            return

        # Told every time rather than once at startup, so that changing the
        # proxy in Settings takes effect on the next download instead of the
        # next launch.
        proxy = clean_proxy(settings.get("proxy"))
        doors.configure(proxy)

        engine_error = job.error                # kept: it may be the truer one
        job.status = "downloading"
        job.error = ""
        job.stage = "direct"
        job.percent = 0.0

        try:
            # YouTube is the one door with a choice of streams, so it is told
            # what was asked for. The others ignore all three and take the one
            # file they are handed.
            info = doors.resolve(
                job.url,
                quality=job.quality,
                prefer_h264=bool(settings.get("prefer_h264", True)),
                can_merge=has_ffmpeg())
        except doors.DoorError as exc:
            # The door usually knows something worth saying - a removed post,
            # an age-gate - so its answer beats yt-dlp's stack trace. Usually.
            job.status = "error"
            job.stage = ""
            job.error = _door_verdict(engine_error, str(exc),
                                      job.tried_signed_in)
            return
        except Exception:                       # noqa: BLE001
            # It simply did not work. The user keeps the error they can act
            # on rather than one about a route they never asked for.
            job.status = "error"
            job.stage = ""
            job.error = engine_error
            return

        target = self._door_path(settings, job, info)
        part = target.with_suffix(target.suffix + ".part")
        audio_part = target.with_suffix(target.suffix + ".audio.part")
        two_streams = bool(info.get("audio_url"))
        started = time.monotonic()

        # A slice of the bar per stream rather than one bar restarting at zero
        # when the audio begins. Video is far the larger of the two, so it gets
        # nearly all of the room and the join gets the last of it.
        def span(low, high):
            def progress(done, total):
                share = (done / total) if total else 0.0
                job.percent = round(low + (high - low) * share, 1)
                job.size = human_bytes(done)
                elapsed = max(time.monotonic() - started, 0.001)
                job.speed = human_bytes(done / elapsed) + "/s"
            return progress

        streams = [(info["url"], part, span(0.0, 90.0 if two_streams else 100.0))]
        if two_streams:
            streams.append((info["audio_url"], audio_part, span(90.0, 98.0)))

        try:
            for address, where, progress in streams:
                if job.cancelled:
                    job.status = "paused" if job.paused else "cancelled"
                    return
                self._door_pull(address, where, info.get("headers") or {},
                                time.monotonic() + self._DOOR_CAP,
                                progress, job, proxy=proxy)
        except Exception as exc:                # noqa: BLE001
            job.status = "error"
            job.stage = ""
            job.error = f"Riplox's own route reached {info['site']} but the "\
                        f"download failed: {exc}"
            return

        if job.cancelled:
            job.status = "paused" if job.paused else "cancelled"
            return

        if two_streams:
            job.stage = "joining"
            problem = self._door_join(part, audio_part, job,
                                      container=info.get("ext") or "mp4")
            if problem:
                job.status = "error"
                job.stage = ""
                job.error = problem
                return

        try:
            part.replace(target)
        except OSError as exc:
            job.status = "error"
            job.stage = ""
            job.error = f"Could not save the file: {exc}"
            return

        written = target.stat().st_size
        job.status = "done"
        job.percent = 100.0
        job.speed = job.eta = ""
        job.stage = "direct"
        job.filepath = str(target)
        job.size = human_bytes(written)
        if info.get("title"):
            job.title = info["title"]
        if info.get("thumbnail"):
            job.thumbnail = info["thumbnail"]
        if info.get("uploader"):
            job.uploader = info["uploader"]
        job.log = (f"{job.log}\n\nyt-dlp could not fetch this link, so Riplox "
                   f"used its own route to {info['site']} instead.\n"
                   + (f"{info['note']}\n" if info.get("note") else "")
                   + ("video and audio arrived separately and were joined.\n"
                      if two_streams else "")
                   + f"saved    {target}")
        add_history({
            "title": job.title,
            "url": job.url,
            "filepath": job.filepath,
            "quality": job.quality,
            "thumbnail": job.thumbnail,
            "uploader": job.uploader,
            "size": job.size,
            "bytes": written,
            "from": job.origin,
            "site": site_of(job.url),
            "when": datetime.now().isoformat(timespec="seconds"),
        })
        write_usernames(settings)

    def _convert(self, job: Job) -> None:
        # Imported here rather than at the top: convert.py needs engine, and
        # two modules importing each other at load time is a crash.
        import convert

        job.status = "converting"
        job.error = ""
        job.stage = ""
        spec = job.conv

        result = convert.run(spec["source"], spec["target_dir"], spec["fmt"],
                             spec["quality"], job=job,
                             scale=spec.get("scale", ""))

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

    def _attempt(self, job: Job, settings: dict, client: str,
                 with_cookies: bool = True) -> bool:
        cookie_path, temp_cookie, account = (open_cookies(settings, job.url)
                                             if with_cookies else (None, False, 0))
        job.sent_cookies = bool(cookie_path)
        # Sticky, unlike the line above: the signed-out retry runs a second
        # attempt with cookies deliberately left out, and without this the job
        # would end up claiming it had never been tried signed in at all.
        job.tried_signed_in = job.tried_signed_in or bool(cookie_path)
        # Which account signed this attempt, so that a refusal rests the one
        # that was actually refused rather than the whole site.
        job.account = account
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

        # Chapters and a trim both speak through --download-sections, and
        # yt-dlp unions everything it is given - so asking for both would hand
        # back the chapters AND the trimmed range. They are exclusive here,
        # and the screen hides the trim while chapters are ticked.
        wants_chapters = bool(opts.get("chapters") or opts.get("chapters_all"))
        wants_clips = bool(opts.get("clips")) and not wants_chapters
        # Either way this is part of a video rather than the video, so it stays
        # out of the download archive for the same reason a trim does.
        trimmed = bool(job.start or job.end) or wants_chapters or wants_clips
        args += extra_args(settings, job.quality, trimmed)

        # A site that has actually refused gets the strict treatment: a real
        # gap between the engine's own page requests, not the polite 0.75s
        # every download already carries. Passed after the base arguments on
        # purpose - yt-dlp keeps the last value given for an option, so this
        # replaces the polite one rather than fighting with it.
        strict = pace_requests(site_of(job.url), settings)
        if strict:
            args += ["--sleep-requests", str(strict)]

        if opts.get("max_mb"):
            args += ["--max-filesize", f"{opts['max_mb']}M"]
        if opts.get("sub_langs"):
            args += ["--write-subs", "--write-auto-subs",
                     "--sub-langs", opts["sub_langs"]]
        if wants_chapters:
            args += chapter_args(opts.get("chapters"),
                                 every=bool(opts.get("chapters_all")),
                                 exact=job.exact)
        elif wants_clips:
            args += clip_args(opts["clips"], exact=job.exact)
        else:
            args += section_arg(job.start, job.end, job.exact)
        args += [
            "--newline",
            # --print implies --quiet, which would swallow every progress line.
            "--progress",
            "--no-playlist",
            "--windows-filenames",
            "--retries", "5",
            "--fragment-retries", "10",
            # ⚠️⚠️ The engine's own default is to SKIP a fragment it cannot
            # get, merge what it has with the complete audio track, exit 0 and
            # call that a download. Measured on this machine after one network
            # drop: a 2160p file whose video runs 36 seconds against 390
            # seconds of audio, and a 1080p one at 85 against 197 - both marked
            # done, both with twenty abandoned .part-Frag files beside them. A
            # file that plays for a few seconds and then goes to a still frame
            # is worse than a failure, because nothing anywhere says so. Now it
            # fails, which puts it back on the queue to resume from what it
            # already has.
            #
            # The cost, stated: a site with a fragment that is permanently gone
            # will now fail instead of handing over a partial video. That is
            # the right way round - a partial video that claims to be whole is
            # the one outcome nobody can act on.
            "--abort-on-unavailable-fragments",
            "-o", self._outtmpl(settings, job),
            "--progress-template",
            (PROGRESS_TAG + "%(progress.status)s|%(progress.downloaded_bytes)s|"
             "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
             "%(progress.speed)s|%(progress.eta)s|"
             # Counted, unlike the byte totals below - see _apply_progress.
             "%(progress.fragment_index)s|%(progress.fragment_count)s"),
            "--progress-template",
            "postprocess:" + POST_TAG + "%(progress.status)s|%(progress.postprocessor)s",
            "--print", "after_move:" + PATH_TAG + "%(filepath)s|%(height)s",
            # Costs nothing - the engine has already read the page by then -
            # and it is the only way a phone-sent download ever gets a picture.
            "--print", "before_dl:" + THUMB_TAG + "%(thumbnail)s|%(title)s",
            "--no-simulate",
        ]

        # Thumbnails. The engine has no "give me the third one" flag - what it
        # has is one (the default) or all of them. So "choose the thumbnail"
        # means saving the set and letting a person pick, which is what the
        # request actually wanted: sites often serve a poor default.
        if opts.get("write_desc"):
            # yt-dlp writes nothing when a site gives no description, and says
            # nothing about it either. That is the site's answer rather than a
            # failure, so it is not claimed as one - but it is why the label
            # promises the description and not a file.
            args.append("--write-description")
        if opts.get("thumb_all"):
            args.append("--write-all-thumbnails")
        elif settings.get("write_thumbnail"):
            args.append("--write-thumbnail")

        # A live stream is normally joined wherever it happens to be. Starting
        # at the beginning is what people mean by "download the stream", and
        # it is the one thing that cannot be added afterwards.
        if opts.get("live_from_start"):
            args.append("--live-from-start")

        # Subtitles without the video. Placed last so it overrides the format
        # selection above rather than fighting it: --skip-download makes every
        # -f argument moot, which is exactly the intent.
        if opts.get("subs_only"):
            langs = opts.get("sub_langs") or settings.get("sub_langs") or "en"
            args += ["--skip-download", "--write-subs", "--write-auto-subs",
                     "--sub-langs", langs, "--sub-format", "srt/vtt/best"]
            if has_ffmpeg():
                args += ["--convert-subs", "srt"]

        args.append(job.url)
        return args

    def _spawn(self, job: Job, settings: dict, client: str, cookie_path) -> bool:
        args = self.build_args(job, settings, client, cookie_path)

        job.status = "downloading"
        job.error = ""
        # A retry starts the streams again from the top, so the bar's idea of
        # which one is running has to start again too.
        job.streams = 0
        # And so does the count of parts that arrived: a retry re-lists every
        # one of them, so carrying the previous attempt's count forward would
        # make a short folder add up to a full one.
        job.parts = 0
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
        # ⚠️ THIS CALL IS THE WHOLE POINT OF tie_to_app, AND IT WAS MISSING.
        #
        # The job object was written, tested and then never wired up, so every
        # yt-dlp Riplox started outlived it. Found on a real machine: three
        # yt-dlp processes from dead Riploxes, started 4:02, 4:37 and 5:35 pm,
        # holding 3.7, 2.6 and 2.1 HOURS of CPU between them - about 87% of a
        # core each, for ever, on links that were never going to finish. Two of
        # them were on the same URL the running copy was retrying.
        #
        # Nothing surfaced it: Riplox had forgotten them, Task Manager showed
        # "yt-dlp" and nothing else, and the machine just felt slow.
        tie_to_app(proc)

        # Read stderr as it arrives rather than in one lump at the end. A
        # trimmed download is handed to ffmpeg, which reports its progress
        # here and never through yt-dlp's progress template - so without this
        # the queue sat at 0.0% for three and a half minutes and looked dead.
        stderr_lines = []

        def drain_stderr():
            for raw in proc.stderr:
                job.heard = time.monotonic()
                line = raw.rstrip("\r\n")
                stderr_lines.append(line)
                del stderr_lines[:-400]
                if job.start or job.end:
                    self._apply_ffmpeg_progress(job, line)

        err_thread = threading.Thread(target=drain_stderr, daemon=True)
        err_thread.start()

        # The watchdog. Nothing used to notice an engine that simply stopped.
        #
        # Found on a real machine: one TikTok video that yt-dlp cannot extract
        # left FOUR yt-dlp processes spinning at a full core each - one of them
        # for five hours - while the row sat on "downloading" for ever. Other
        # videos downloaded fine, so nothing looked broken; the machine was just
        # slow and that download never finished.
        #
        # Silence is the signal, not slowness. With --newline --progress a
        # download prints constantly however slow the connection is, and the
        # merge talks on stderr, which is why BOTH pipes feed job.heard. A job
        # that has said nothing on either for _SILENCE_LIMIT is not working.
        job.heard = time.monotonic()
        job.went_quiet = False

        def watchdog():
            while proc.poll() is None:
                time.sleep(5)
                # Paused and cancelled are the user's doing, not a fault, and a
                # paused job is silent on purpose.
                if job.cancelled or job.paused:
                    job.heard = time.monotonic()
                    continue
                if time.monotonic() - job.heard < _SILENCE_LIMIT:
                    continue
                job.went_quiet = True
                _kill_tree(proc)
                return

        threading.Thread(target=watchdog, daemon=True).start()

        for line in proc.stdout:
            job.heard = time.monotonic()
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
                rest = line[len(PATH_TAG):].strip()
                # Split from the RIGHT: a path cannot contain "|" on Windows,
                # but reading it that way costs nothing and cannot be wrong.
                path, sep, tail = rest.rpartition("|")
                if sep and tail.strip().isdigit():
                    job.filepath = path.strip()
                    job.height = int(tail.strip())
                else:
                    # An older engine, or a format with no height at all.
                    job.filepath = rest
                # A chapter download writes this line once per chapter, so the
                # last one would win and the job would point at whichever
                # chapter happened to finish last. What it produced is the
                # folder, so that is what it remembers - Play opens it, the
                # Library names it, and the size below adds it up.
                if (job.opts.get("chapters") or job.opts.get("chapters_all")
                        or job.opts.get("clips")):
                    # Counted before the path is turned into its folder, because
                    # afterwards there is nothing left to count: one job, many
                    # files, and this line is the only place each one is seen.
                    job.parts = getattr(job, "parts", 0) + 1
                    job.filepath = str(Path(job.filepath).parent)
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
                # A part that was already on disk is a part the user has, even
                # though this run did not write it. Counted, or a second run
                # over the same folder would look like a run that lost things.
                if (job.opts.get("chapters") or job.opts.get("chapters_all")
                        or job.opts.get("clips")):
                    job.parts = getattr(job, "parts", 0) + 1
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

        # The watchdog stopped it. Checked before anything reads the exit code,
        # because a killed process looks like an ordinary failure and this one
        # has a much more useful thing to say than whatever yt-dlp last wrote.
        #
        # It ends here rather than going round the retry ladder again: fifteen
        # minutes of silence is not a hiccup, and three more attempts would be
        # forty-five more minutes of the same nothing.
        if job.went_quiet:
            job.status = "error"
            job.error = ("The download engine stopped responding - nothing at "
                         "all for %d minutes - so Riplox stopped waiting for "
                         "it. This usually means the site changed and the "
                         "engine cannot read this video yet. Try Update engine "
                         "in Settings, or this link again later."
                         % int(_SILENCE_LIMIT / 60))
            job.speed = job.eta = ""
            return True          # not a retry - it already had its time

        # A size ceiling is not an error to yt-dlp: it prints one line, skips
        # the video and exits 0. Without this the row would read "done" over a
        # file that was never written, which is the worst of both answers.
        if proc.returncode == 0 and job.opts.get("max_mb") and not job.filepath:
            job.status = "error"
            job.error = (f"Bigger than the {job.opts['max_mb']} MB limit set "
                         f"for this device, so it was not downloaded.")
            return True          # a rule was applied; retrying changes nothing

        if proc.returncode == 0:
            # One job, many files - so "it exited 0" is not the same as "you
            # got what you ticked". Every section is fetched on its own, and a
            # site can refuse one of them while handing over the rest; without
            # this the job goes green over a folder that is short, and the only
            # way to notice is to count the files by hand. Reported by Nazim
            # after ticking five chapters and finding three.
            wanted = int(job.opts.get("parts_expected") or 0)
            if wanted and getattr(job, "parts", 0) < wanted:
                job.status = "error"
                job.error = (
                    f"Only {job.parts} of the {wanted} parts you asked for "
                    f"arrived. The rest were refused by the site or could not "
                    f"be cut. What did arrive is in the folder; Retry fetches "
                    f"the missing ones without downloading these again.")
                job.speed = job.eta = ""
                return True

            # And the second way exiting 0 is not the same as doing the job:
            # the right file at the wrong quality, because the main route was
            # refused and the way round it only carries small formats. Same
            # shape as the parts check above - the file is kept, the row says
            # what happened, and Retry means something.
            undersized = self._quality_short(job, settings)
            if undersized:
                job.status = "error"
                job.error = undersized
                job.speed = job.eta = ""
                return True          # it downloaded; it just downloaded small

            job.status = "done"
            job.percent = 100.0
            job.speed = job.eta = ""
            if job.filepath and job.title in (job.url, ""):
                job.title = Path(job.filepath).stem

            # A chosen cover has to wait until the file exists, and it changes
            # the file, so it happens before the size below is read.
            if job.opts.get("thumb_url"):
                said = self._cover(job, job.opts["thumb_url"],
                                   clean_proxy(settings.get("proxy")))
                if said:
                    job.log = f"{job.log}\n{said}"

            # Progress reports one stream at a time, so the running total is
            # only the last stream. The finished file on disk is the truth.
            written = 0
            try:
                written = written_bytes(job.filepath)
                job.size = human_bytes(written)
            except (OSError, ValueError):
                pass
            add_history({
                "title": job.title,
                "url": job.url,
                "filepath": job.filepath,
                "quality": job.quality,
                "thumbnail": job.thumbnail,
                # Who made it. The job has carried this since it was read, and
                # it was being dropped here - so the Accounts list was reading
                # a field nothing ever wrote and stayed empty forever.
                "uploader": job.uploader,
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
            write_usernames(settings)
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
        # An older engine does not send these, and an unfragmented download
        # sends "NA". Both read as "no fragments", which is the safe way round.
        frag_at, frag_of = (parts[6], parts[7]) if len(parts) >= 8 else ("", "")

        downloaded = _num(done)
        # Told apart deliberately. total_bytes is measured; total_bytes_estimate
        # is yt-dlp's own extrapolation and moves all through a download. On the
        # two downloads measured here, total_bytes arrived on 14 lines out of
        # 1453 - so nearly everything below is running on the estimate, and the
        # difference decides both how the bar is worked out and whether the
        # size shown is blurred.
        # ⚠️ NOT the file's size when the download is fragmented: there,
        # total_bytes is the CURRENT FRAGMENT's size, and its opening line
        # reads 1024 of 1024. That is why the bar below asks the fragments
        # first and only falls back to bytes when there are none - and why this
        # is used for nothing except deciding whether the size shown may be
        # rounded, which is a question that only arises when there are no
        # fragments to begin with.
        exact = _num(total) if not _num(frag_of) else 0.0
        size = _num(total) or _num(total_est)

        # ⚠️⚠️ Two things are true here at once, and getting either one wrong
        # produces a bar somebody reports. Both were measured on real
        # downloads, after three wrong tries that were reasoned about instead.
        #
        # 1. The engine's estimate is its OWN extrapolation. From its source:
        #        (bytes_so_far + this_fragment) / (fragment_index + 1) * total
        #    - the average fragment so far, times how many there are. On the
        #    opening lines that average comes from one part-finished fragment
        #    and reads 1024 of 1024: a ratio of 1.0, which sent the bar to the
        #    top of its band on line one and, with a furthest-reached guard,
        #    kept it there. Reported as "start hote hi 92% pe chala jata hai".
        #
        # 2. Counting fragments instead fixed that and broke the other half.
        #    Fragments complete in bursts: over 1,316 progress lines of one
        #    download the fragment bar moved 38 times and stood still for 149
        #    lines in a row. Reported as "percentage stuck".
        #
        # So the fragment count is a FLOOR - exact, and it never goes back -
        # and the byte ratio moves the bar between fragments. Whichever is
        # further along wins. Measured against the alternatives on one
        # download: longest freeze 63 lines against 149, 279 moves against 38,
        # and it never passes 3.3% in the opening tenth.
        whole = _num(frag_of)
        at = _num(frag_at)

        # 3. And the fix for both was still dividing by that estimate. Measured
        #    again on two real downloads at "max": total_bytes arrived on 14 of
        #    1453 lines, so "size" below was the estimate nearly always - and
        #    that estimate CLIMBED from 147 MB to 353 MB during one of them. A
        #    denominator that grows drags the ratio down with it, and the bar
        #    went backwards 144 times, by up to 3.44%. Reported as "% peeche
        #    jaati thi", with the freezes that follow it being pinned to the
        #    floor in between.
        #
        # So the estimate is gone from here entirely. A finished fragment's
        # size needs no extrapolation: it is however many bytes arrived while
        # the index stood still. That gives an exact boundary and a measured
        # position inside the current fragment, which cannot reach the next
        # boundary and cannot fall below the last one.
        #
        #    rule                          moves  back   worst  freeze
        #    what shipped                    782   144   3.44%      26
        #    fragments alone                  54     0       -      18
        #    fragments + bytes inside them   741     0       -      14
        if whole:
            # A new stream restarts the count, and so must this.
            if at < job.frag_base_at:
                job.frag_base_at = 0.0
                job.frag_base_bytes = 0.0
                # The size is reported per stream, so the number held for the
                # video half must not anchor the audio half's.
                job.size_shown = 0.0
                # ⚠️ And the band moves here too. It used to move only on a
                # "finished" line, which is not guaranteed: one real capture
                # in tests/fixtures has 1,438 lines, two streams and no
                # "finished" at all, so the audio half was drawn in the video
                # half's band and the bar fell from 92% to 0%. A falling
                # fragment index is the signal that cannot go missing - this
                # branch already trusts it for everything else.
                job.streams += 1
            if at > job.frag_base_at:
                job.frag_base_at = at
                job.frag_base_bytes = downloaded
            base_at = job.frag_base_at
            base_bytes = job.frag_base_bytes
            typical = (base_bytes / base_at) if base_at else 0.0
            inside = ((downloaded - base_bytes) / typical) if typical else 0.0
            # Never the whole of the next fragment: that one is not in yet.
            pct = min(1.0, (base_at + min(0.999, max(0.0, inside))) / whole)
        elif size:
            # No fragments at all - and only then is total_bytes the file's own
            # size rather than the current fragment's. The audio half of every
            # download arrives this way, and the byte maths was always right
            # here.
            pct = min(1.0, downloaded / size)
        else:
            pct = None

        # ⚠️ Worked out HERE, not at the top of the function: the block above
        # is what discovers that a new stream has started, and a band chosen
        # before that discovery describes the stream that just ended.
        index = min(job.streams, len(self._STREAM_BANDS) - 1)
        low, high = self._STREAM_BANDS[index]

        if pct is not None:
            # ⚠️ No furthest-reached guard any more, deliberately: holding the
            # maximum is what froze the bar for 326 lines in that same
            # measurement, which is worse than the wobble it prevented - and
            # the floor caps that wobble at 1.3%. 100% is still kept for the
            # moment the file is actually on the disk.
            job.percent = min(99.0, low + (high - low) * pct)

        if size:
            # ⚠️⚠️ A fragmented download reports NO total_bytes at all - only
            # total_bytes_estimate, and for its first few fragments that is an
            # extrapolation from almost nothing. Measured on a file that turned
            # out to be 37.3 MB: the opening readings were 4, 14, 56 and 88 MB,
            # and they fell as often as they rose.
            #
            # Holding the LARGEST reading was the first attempt at this, and it
            # was worse than the problem: it froze on the 88 and never came
            # down, so an 83 MB download called itself 510 MB the whole way.
            # Reported, and measured again afterwards - 88.8 MB shown for that
            # 37.3 MB file, wrong from a tenth of the way in to the end.
            #
            # The estimate does settle. The same run read 47, 45, 40, 37, 39
            # once a tenth of the fragments were in. So nothing clever is
            # needed - only patience: say nothing until the estimate is an
            # estimate, then say what it says.
            settled = (not whole) or at >= max(2.0, whole * 0.1)
            if settled and size >= downloaded:
                # ⚠️ Rounded, because the estimate keeps moving and a total is
                # read to know roughly how big the file is. Measured on the same
                # two downloads: the exact number changed 484 times in one of
                # them; rounded, 89 - for the same error, 8% at the halfway
                # point either way.
                #
                # Waiting longer was measured and rejected: showing it only
                # past a third, or past halfway, leaves the same 8% error and
                # simply says nothing for 256 to 399 lines. And holding the
                # largest reading stays dead - the estimate FALLS as well as
                # rises, 54 times in one download and once by 174 MB in the
                # other, which is the 51% overstatement that rule produced
                # the first time it was tried.
                job.size_estimated = not exact
                if exact:
                    job.size = human_bytes(size)
                else:
                    job.size_shown = _settled_size(size, job.size_shown)
                    # ⚠️ Marked, because this one is not measured - it is
                    # yt-dlp's extrapolation and it moves. Measured on two real
                    # downloads: shown as 550 MB and then 350 MB on a file that
                    # turned out to be 319.
                    #
                    # Hiding it was the alternative and it is worse: the number
                    # is useful even at 10% out, and it is the only answer to
                    # "how much longer". Saying it is approximate costs one
                    # character and stops it reading as a measurement.
                    #
                    # Only "Max" ever reaches this on YouTube. Every capped
                    # rung, 360 through 2160, carries a real filesize and takes
                    # the branch above - so this mark appears exactly where the
                    # number really is a guess, and nowhere else.
                    job.size = human_bytes(job.size_shown)
            # ⚠️ Per STREAM, not per file: yt-dlp fetches the video and the
            # audio separately and reports each on its own. The stage beside it
            # says which one, so the numbers restarting is readable rather than
            # baffling.
            job.got = human_bytes(downloaded)

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
