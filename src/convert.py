"""
Riplox Desktop - turning a file that is already on disk into audio.

Nothing here downloads anything. The bundled ffmpeg already carries every
encoder this needs (libmp3lame, aac, flac, libopus, libvorbis, alac), so the
whole feature costs no extra megabytes.

Two rules shape the code below:

* Never touch the original. A conversion that eats its own input is a bug
  nobody forgives.
* Do not re-encode when a copy will do. Pulling the audio out of an MP4 into
  an M4A is a stream copy: instant, and not a single bit of quality lost.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import engine

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# What each container can hold as-is, so we know when copying is allowed.
FORMATS = {
    "mp3":  {"label": "MP3",  "codec": "libmp3lame", "copyable": ("mp3",)},
    "m4a":  {"label": "M4A",  "codec": "aac",        "copyable": ("aac", "alac")},
    "opus": {"label": "OPUS", "codec": "libopus",    "copyable": ("opus",)},
    "flac": {"label": "FLAC", "codec": "flac",       "copyable": ("flac",)},
    "wav":  {"label": "WAV",  "codec": "pcm_s16le",  "copyable": ()},
}

# Bitrates offered for the lossy formats. FLAC and WAV ignore this entirely.
QUALITY = {
    "high":   {"label": "High (320 kbps)",   "bitrate": "320k"},
    "normal": {"label": "Normal (192 kbps)", "bitrate": "192k"},
    "small":  {"label": "Small (128 kbps)",  "bitrate": "128k"},
}

MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv",
                  ".ts", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac",
                  ".aac", ".wma"}


def ffprobe_path():
    ff = engine.ffmpeg_path()
    if ff is None:
        return None
    probe = ff.parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return probe if probe.exists() else None


def inspect(path) -> dict:
    """Duration, audio codec, and whether the file carries cover art."""
    probe = ffprobe_path()
    if probe is None:
        return {}
    try:
        out = subprocess.run(
            [str(probe), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, creationflags=_NO_WINDOW)
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}

    info = {"duration": 0.0, "codec": "", "cover": False, "has_audio": False}
    try:
        info["duration"] = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        pass

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio" and not info["codec"]:
            info["codec"] = stream.get("codec_name") or ""
            info["has_audio"] = True
        if stream.get("disposition", {}).get("attached_pic"):
            info["cover"] = True
    return info


def free_name(target: Path) -> Path:
    """A name nothing is using, so an existing file is never overwritten."""
    if not target.exists():
        return target
    for n in range(2, 100):
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        if not candidate.exists():
            return candidate
    return target.with_name(f"{target.stem} (new){target.suffix}")


def build_args(source: Path, target: Path, fmt: str, quality: str,
               info: dict) -> list:
    ffmpeg = engine.ffmpeg_path()
    spec = FORMATS[fmt]

    args = [str(ffmpeg), "-hide_banner", "-nostdin", "-y",
            "-i", str(source), "-vn"]

    # Straight copy when the container can already hold what is in there.
    copying = info.get("codec") in spec["copyable"]
    if copying:
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", spec["codec"]]
        if fmt in ("mp3", "m4a", "opus"):
            args += ["-b:a", QUALITY.get(quality, QUALITY["high"])["bitrate"]]

    # Titles, artists and dates come along; they are what make a file findable.
    args += ["-map_metadata", "0"]
    if fmt == "mp3":
        args += ["-id3v2_version", "3", "-write_id3v1", "1"]

    args += ["-progress", "pipe:1", "-nostats", str(target)]
    return args


_OUT_TIME = re.compile(r"out_time_ms=(\d+)")


def run(source, target_dir, fmt: str, quality: str, job=None) -> dict:
    """
    Convert one file. `job` is optional; when given, its percent and status
    are updated as ffmpeg reports progress, and cancelling it stops the work.
    """
    source = Path(source)
    if not source.exists():
        return {"ok": False, "error": "That file is not there any more."}
    if engine.ffmpeg_path() is None:
        return {"ok": False, "error": "The audio tools are missing. Reinstall Riplox."}
    if fmt not in FORMATS:
        return {"ok": False, "error": "Unknown format."}

    info = inspect(source)
    if not info.get("has_audio"):
        return {"ok": False, "error": "There is no audio in that file."}

    target_dir = Path(target_dir or source.parent)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"Cannot write there: {exc}"}

    target = free_name(target_dir / f"{source.stem}.{fmt}")
    args = build_args(source, target, fmt, quality, info)

    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=_NO_WINDOW)
    if job is not None:
        job.proc = proc

    duration = info.get("duration") or 0
    for line in proc.stdout:
        if job is not None and job.cancelled:
            continue                       # drain, but stop believing it
        found = _OUT_TIME.search(line)
        if found and duration > 0 and job is not None:
            done = int(found.group(1)) / 1_000_000.0
            job.percent = max(job.percent, min(99.0, done / duration * 100))

    tail = (proc.stderr.read() or "")
    proc.wait()
    if job is not None:
        job.proc = None

    if job is not None and job.cancelled:
        target.unlink(missing_ok=True)     # a half-written file helps nobody
        return {"ok": False, "cancelled": True}

    if proc.returncode != 0 or not target.exists():
        target.unlink(missing_ok=True)
        last = [l for l in tail.splitlines() if l.strip()]
        return {"ok": False,
                "error": last[-1][:200] if last else "The conversion failed."}

    return {"ok": True, "path": str(target), "copied": info.get("codec") in
            FORMATS[fmt]["copyable"], "size": target.stat().st_size}
