"""
Riplox Desktop - download engine.

Wraps the yt-dlp executable in a subprocess instead of importing the library.
That is deliberate: the bundled binary lives in a user-writable folder, so the
app can update its own extractors ("Update engine") when a site changes its
layout. A frozen library copy would go stale within weeks.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

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


def default_download_dir() -> Path:
    return Path(os.path.expanduser("~")) / "Downloads" / "Riplox"


def _shipped(name: str) -> Path | None:
    """Find a binary that came with the app, wherever this build put it."""
    for root in bundle_roots():
        candidate = root / "bin" / name
        if candidate.exists():
            return candidate
    return None


def ytdlp_path() -> Path | None:
    """
    yt-dlp runs from a writable copy in LOCALAPPDATA so that "Update engine"
    works without admin rights. Program Files is read-only for normal users.
    """
    name = "yt-dlp.exe" if os.name == "nt" else "yt-dlp"
    live = bin_dir() / name
    shipped = _shipped(name)

    if shipped is not None:
        if not live.exists() or shipped.stat().st_mtime > live.stat().st_mtime:
            try:
                shutil.copy2(shipped, live)
            except OSError:
                # A running yt-dlp can hold a lock; the existing copy is fine.
                pass

    if live.exists():
        return live
    if shipped is not None:
        return shipped

    found = shutil.which("yt-dlp")
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

DEFAULT_SETTINGS = {
    "download_dir": str(default_download_dir()),
    "default_quality": "best",
    "max_parallel": 2,
    "cookies_browser": "none",       # none | firefox | chrome | edge | brave
    "cookies_file": "",              # path to an exported cookies.txt
    "prefer_h264": True,             # play-anywhere codec over raw quality
    "subfolder_per_site": False,
    "auto_paste": True,              # watch clipboard for links
    "write_thumbnail": False,
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
    return s


def save_settings(patch: dict) -> dict:
    with _settings_lock:
        s = load_settings()
        for key, value in patch.items():
            if key in DEFAULT_SETTINGS:
                s[key] = value
        tmp = settings_file().with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2)
        tmp.replace(settings_file())
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


def _base_args(settings: dict) -> list:
    exe = ytdlp_path()
    if exe is None:
        raise EngineMissing("yt-dlp binary not found")

    args = [str(exe), "--no-warnings", "--ignore-config", "--no-colors"]

    ff = ffmpeg_path()
    if ff is not None:
        args += ["--ffmpeg-location", str(ff.parent)]

    # A cookies.txt file wins over the browser reader. Since Chrome 127 bound
    # its cookie store to its own process, reading Chromium browsers directly
    # fails on Windows no matter what - an exported file is the way through.
    cookie_file = (settings or {}).get("cookies_file", "")
    if cookie_file and Path(cookie_file).exists():
        args += ["--cookies", str(cookie_file)]
    else:
        browser = (settings or {}).get("cookies_browser", "none")
        if browser and browser != "none":
            args += ["--cookies-from-browser", browser]

    return args


# Chromium-based browsers encrypt cookies so only the browser itself can read
# them (Chrome 127+, July 2024). Firefox does not.
LOCKED_BROWSERS = {"chrome", "edge", "brave", "opera", "vivaldi", "chromium"}


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


def engine_version() -> str:
    exe = ytdlp_path()
    if exe is None:
        return "missing"
    try:
        out = _run([str(exe), "--version"], timeout=30)
        return (out.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def update_engine() -> dict:
    """Ask yt-dlp to update itself. Works because it lives in LOCALAPPDATA."""
    exe = ytdlp_path()
    if exe is None:
        return {"ok": False, "message": "Engine not installed."}
    try:
        out = _run([str(exe), "-U"], timeout=300)
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


def format_args(quality: str, settings: dict) -> list:
    """Translate a UI quality choice into yt-dlp format flags."""
    ff = has_ffmpeg()
    safe = bool((settings or {}).get("prefer_h264", True))

    if quality == "mp3":
        if not ff:
            # No encoder available - grab the best standalone audio track as-is.
            return ["-f", "bestaudio/best"]
        return ["-f", "bestaudio/best", "-x", "--audio-format", "mp3",
                "--audio-quality", "0"]

    if not ff:
        # Without ffmpeg we can only take streams that are already muxed.
        if quality == "best":
            return ["-f", "best[ext=mp4]/best"]
        return ["-f", f"best[height<=?{quality}][ext=mp4]/best[height<=?{quality}]/best"]

    cap = "" if quality == "best" else f"[height<=?{quality}]"
    plain = f"bv*{cap}+ba/b{cap}/bv*+ba/b"

    if safe:
        selector = f"bv*{cap}{H264}+ba{AAC}/bv*{cap}{H264}+ba/{plain}"
    else:
        selector = plain

    return ["-f", selector, "-S", "res", "--merge-output-format", "mp4"]


def analyze(url: str, settings: dict) -> dict:
    """
    Inspect a URL without downloading.
    Returns a single video dict, or a playlist dict with entries.
    """
    args = _base_args(settings) + [
        "-J", "--flat-playlist", "--no-progress", url,
    ]
    try:
        out = _run(args, timeout=120)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Timed out while reading that link.")

    if out.returncode != 0 or not (out.stdout or "").strip():
        raise RuntimeError(_clean_error(out.stderr))

    try:
        info = json.loads(out.stdout)
    except ValueError:
        raise RuntimeError("Could not read that link.")

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
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
                }
                for e in entries
            ],
        }

    return {
        "kind": "video",
        "url": info.get("webpage_url") or url,
        "title": info.get("title") or "Untitled",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "thumbnail": _pick_thumb(info),
        "extractor": (info.get("extractor_key") or info.get("extractor") or "").lower(),
        "qualities": _available_qualities(info),
    }


def _pick_thumb(info: dict) -> str:
    if not isinstance(info, dict):
        return ""
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = info.get("thumbnails") or []
    if thumbs:
        return thumbs[-1].get("url", "")
    return ""


def _available_qualities(info: dict) -> list:
    """Which of our fixed quality rungs this video can actually deliver."""
    heights = set()
    for f in info.get("formats") or []:
        h = f.get("height")
        if isinstance(h, int):
            heights.add(h)

    rungs = []
    for key in ("2160", "1440", "1080", "720", "480", "360"):
        target = int(key)
        if any(h >= target for h in heights):
            rungs.append(key)

    out = ["best"] + rungs
    if has_ffmpeg() or True:  # audio-only is always offered
        out.append("mp3")
    return out


def _clean_error(stderr: str) -> str:
    """Turn a yt-dlp stack of ERROR lines into one human sentence."""
    text = (stderr or "").strip()
    if not text:
        return "That link could not be opened."

    low_all = text.lower()

    # Chrome-family cookie stores cannot be decrypted by anything but the
    # browser itself. Closing it does not help, so do not tell them to.
    if ("dpapi" in low_all
            or "app-bound" in low_all
            or "object has no attribute 'decode'" in low_all
            or ("cookie" in low_all and "decrypt" in low_all)):
        return ("Chrome-based browsers lock their cookies so no other program "
                "can read them. Use Firefox instead, or export a cookies.txt "
                "file and pick it in Settings.")

    if "cookie" in low_all and ("permission" in low_all or "could not copy"
                                in low_all or "database" in low_all):
        return ("Close your browser completely and try again - its cookies "
                "cannot be read while it is running.")

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ERROR:"):
            msg = line[6:].strip()
            msg = msg.split(";")[0].strip()
            low = msg.lower()
            if "unsupported url" in low:
                return "This site is not supported."
            if "not a bot" in low or "login_required" in low:
                # YouTube throttles IPs it does not trust. Cookies from a real
                # browser session are the documented way through.
                return ("YouTube wants proof you are a real viewer. Open "
                        "Settings and set 'Use cookies from browser' to the "
                        "browser you watch YouTube in, then try again.")
            if "private" in low or "login" in low or "sign in" in low:
                return "This video is private - try enabling browser cookies in Settings."
            if "unavailable" in low or "removed" in low:
                return "This video is unavailable or was removed."
            if "geo" in low and "restrict" in low:
                return "This video is blocked in your region."
            if "age" in low and ("restrict" in low or "confirm" in low):
                return "Age-restricted - enable browser cookies in Settings."
            return msg[:200]

    return text.splitlines()[-1][:200]


# --------------------------------------------------------------------------
# Download jobs
# --------------------------------------------------------------------------

PROGRESS_TAG = "@@RPX@@"
POST_TAG = "@@RPXPP@@"
PATH_TAG = "@@RPXFILE@@"


class Job:
    __slots__ = ("id", "url", "title", "thumbnail", "quality", "status", "percent",
                 "speed", "eta", "size", "filepath", "error", "created", "proc",
                 "cancelled", "uploader")

    def __init__(self, url, title="", thumbnail="", quality="best", uploader=""):
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

    def add(self, url, title="", thumbnail="", quality="best", uploader="") -> Job:
        job = Job(url, title, thumbnail, quality, uploader)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
        self._wake.set()
        return job

    def snapshot(self) -> list:
        with self._lock:
            return [self._jobs[i].to_dict() for i in self._order if i in self._jobs]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job or job.status in ("done", "error", "cancelled"):
            return False

        job.cancelled = True
        proc = job.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        else:
            job.status = "cancelled"
        return True

    def retry(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status not in ("error", "cancelled"):
                return False
            job.status = "queued"
            job.error = ""
            job.percent = 0.0
            job.cancelled = False
        self._wake.set()
        return True

    def remove(self, job_id: str) -> bool:
        self.cancel(job_id)
        with self._lock:
            self._jobs.pop(job_id, None)
            if job_id in self._order:
                self._order.remove(job_id)
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

    # -- worker plumbing -------------------------------------------------

    def _sync_workers(self) -> None:
        """Grow the pool to match the configured parallelism."""
        want = max(1, min(5, int(load_settings().get("max_parallel", 2))))
        while len(self._workers) < want:
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()
            self._workers.append(t)

    def _next_job(self):
        want = max(1, min(5, int(load_settings().get("max_parallel", 2))))
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
            self._wake.set()

    def _outtmpl(self, settings: dict, job: Job) -> str:
        root = Path(settings["download_dir"])
        if settings.get("subfolder_per_site"):
            root = root / "%(extractor_key)s"

        if job.quality == "mp3":
            # Audio lands as .mp3, so it can never collide with a video file.
            return str(root / "%(title).110B [%(id)s].%(ext)s")

        # Height belongs in the name: without it, grabbing the same video at
        # 720p and then at 1080p silently overwrote the first file.
        return str(root / "%(title).100B [%(id)s] %(height)sp.%(ext)s")

    def _run_job(self, job: Job) -> None:
        settings = load_settings()
        args = _base_args(settings)
        args += format_args(job.quality, settings)
        args += [
            "--newline",
            # --print implies --quiet, which would swallow every progress line.
            "--progress",
            "--no-playlist",
            "--windows-filenames",
            "--retries", "5",
            "--fragment-retries", "10",
            "--concurrent-fragments", "4",
            "-o", self._outtmpl(settings, job),
            "--progress-template",
            (PROGRESS_TAG + "%(progress.status)s|%(progress.downloaded_bytes)s|"
             "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
             "%(progress.speed)s|%(progress.eta)s"),
            "--progress-template",
            "postprocess:" + POST_TAG + "%(progress.status)s|%(progress.postprocessor)s",
            "--print", "after_move:" + PATH_TAG + "%(filepath)s",
            "--no-simulate",
        ]

        if settings.get("write_thumbnail"):
            args.append("--write-thumbnail")

        args.append(job.url)

        job.status = "downloading"
        job.error = ""

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

        stderr_lines = []
        err_thread = threading.Thread(
            target=lambda: stderr_lines.extend(proc.stderr.read().splitlines()),
            daemon=True,
        )
        err_thread.start()

        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if line.startswith(PROGRESS_TAG):
                self._apply_progress(job, line[len(PROGRESS_TAG):])
            elif line.startswith(POST_TAG):
                job.status = "converting"
            elif line.startswith(PATH_TAG):
                job.filepath = line[len(PATH_TAG):].strip()
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

        if job.cancelled:
            job.status = "cancelled"
            job.speed = job.eta = ""
            return

        if proc.returncode == 0:
            job.status = "done"
            job.percent = 100.0
            job.speed = job.eta = ""
            if job.filepath and job.title in (job.url, ""):
                job.title = Path(job.filepath).stem

            # Progress reports one stream at a time, so the running total is
            # only the last stream. The finished file on disk is the truth.
            try:
                job.size = _human_bytes(Path(job.filepath).stat().st_size)
            except (OSError, ValueError):
                pass
            add_history({
                "title": job.title,
                "url": job.url,
                "filepath": job.filepath,
                "quality": job.quality,
                "thumbnail": job.thumbnail,
                "size": job.size,
                "when": datetime.now().isoformat(timespec="seconds"),
            })
        else:
            job.status = "error"
            job.error = _clean_error("\n".join(stderr_lines))

    def _apply_progress(self, job: Job, payload: str) -> None:
        parts = payload.split("|")
        if len(parts) < 6:
            return
        status, done, total, total_est, speed, eta = parts[:6]

        downloaded = _num(done)
        size = _num(total) or _num(total_est)

        if size:
            pct = downloaded / size * 100.0
            # A merged download reports progress per stream, so the raw number
            # drops back to zero when the audio track starts. Only ever move
            # forward, and save 100% for the moment the job actually finishes.
            job.percent = max(job.percent, min(99.0, pct))
            job.size = _human_bytes(size)
        job.speed = f"{_human_bytes(_num(speed))}/s" if _num(speed) else ""
        job.eta = _human_time(_num(eta))

        if status == "finished":
            job.speed = job.eta = ""
        elif job.status not in ("converting", "cancelled"):
            job.status = "downloading"


def _num(value: str):
    try:
        f = float(value)
        return f if f == f and f not in (float("inf"), float("-inf")) else 0
    except (TypeError, ValueError):
        return 0


def _human_bytes(n) -> str:
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
