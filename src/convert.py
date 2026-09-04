"""
Riplox Desktop - turning a file that is already on disk into something else.

Nothing here downloads anything. The bundled ffmpeg already carries every
encoder this needs (libmp3lame, aac, flac, libopus, libvorbis, alac, libx264,
libvpx-vp9), so the whole feature costs no extra megabytes.

Three rules shape the code below:

* Never touch the original. A conversion that eats its own input is a bug
  nobody forgives.
* Do not re-encode when a copy will do. Pulling the audio out of an MP4 into
  an M4A is a stream copy: instant, and not a single bit of quality lost. The
  same is true of an MP4 becoming an MKV - the picture never has to be touched.
* Never scale up. "720p" written over a 480p video is the app lying about the
  file, exactly as it would be on a download.
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

# The same idea for video: `vcopy` and `acopy` are the codecs this container
# already accepts, so the stream is carried across untouched instead of being
# encoded again. MKV takes nearly everything, which is why an MP4 to MKV is
# instant; MP4 to WebM is not, and cannot be.
VIDEO_FORMATS = {
    "mp4":  {"label": "MP4",  "vcodec": "libx264",     "acodec": "aac",
             "vcopy": ("h264",),
             "acopy": ("aac",)},
    "mkv":  {"label": "MKV",  "vcodec": "libx264",     "acodec": "aac",
             "vcopy": ("h264", "hevc", "vp8", "vp9", "av1", "mpeg4"),
             "acopy": ("aac", "opus", "vorbis", "mp3", "flac", "ac3")},
    "mov":  {"label": "MOV",  "vcodec": "libx264",     "acodec": "aac",
             "vcopy": ("h264", "hevc"),
             "acopy": ("aac",)},
    "webm": {"label": "WebM", "vcodec": "libvpx-vp9",  "acodec": "libopus",
             "vcopy": ("vp8", "vp9", "av1"),
             "acopy": ("opus", "vorbis")},
}

# Bitrates offered for the lossy audio formats. FLAC and WAV ignore this.
QUALITY = {
    "high":   {"label": "High (320 kbps)",   "bitrate": "320k"},
    "normal": {"label": "Normal (192 kbps)", "bitrate": "192k"},
    "small":  {"label": "Small (128 kbps)",  "bitrate": "128k"},
}

# The same three words, meaning picture quality when the video is re-encoded.
# Lower CRF is better and bigger; these are the usual x264 landmarks.
CRF = {"high": "18", "normal": "23", "small": "28"}

# Heights a video may be shrunk to. Nothing here grows a video: a target at or
# above what the file already is means no scaling happens at all.
SCALES = {
    "1080": {"label": "1080p", "height": 1080},
    "720":  {"label": "720p",  "height": 720},
    "480":  {"label": "480p",  "height": 480},
}

# A GIF of a whole video is never what anyone wanted, and would be enormous.
# Both caps are in the label, so nobody has to find out from the file.
GIF_SECONDS = 15
GIF_WIDTH = 480
GIF = {"label": f"GIF (first {GIF_SECONDS}s)"}

MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv",
                  ".ts", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".flac",
                  ".aac", ".wma"}


def kind_of(fmt: str) -> str:
    """Which of the three paths a format takes: audio, video, gif, or none."""
    if fmt in FORMATS:
        return "audio"
    if fmt in VIDEO_FORMATS:
        return "video"
    if fmt == "gif":
        return "gif"
    return ""


def label_of(fmt: str) -> str:
    if fmt in FORMATS:
        return FORMATS[fmt]["label"]
    if fmt in VIDEO_FORMATS:
        return VIDEO_FORMATS[fmt]["label"]
    return GIF["label"] if fmt == "gif" else fmt


def ffprobe_path():
    ff = engine.ffmpeg_path()
    if ff is None:
        return None
    probe = ff.parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return probe if probe.exists() else None


def inspect(path) -> dict:
    """Duration, codecs, size, and whether the file carries cover art."""
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

    info = {"duration": 0.0, "codec": "", "cover": False, "has_audio": False,
            "vcodec": "", "width": 0, "height": 0, "has_video": False}
    try:
        info["duration"] = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        pass

    for stream in data.get("streams", []):
        attached = stream.get("disposition", {}).get("attached_pic")
        if stream.get("codec_type") == "audio" and not info["codec"]:
            info["codec"] = stream.get("codec_name") or ""
            info["has_audio"] = True
        # A cover picture is a video stream by the file format's reckoning, so
        # taking it for one would offer to convert an MP3 into an MP4 of a
        # single still frame.
        if stream.get("codec_type") == "video" and not attached and not info["vcodec"]:
            info["vcodec"] = stream.get("codec_name") or ""
            info["width"] = int(stream.get("width") or 0)
            info["height"] = int(stream.get("height") or 0)
            info["has_video"] = True
        if attached:
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


def shrink_to(info: dict, scale: str) -> int:
    """
    The height to scale down to, or 0 for "leave it alone".

    Asking for 1080p on a 720p file is not an error and not an upscale - it is
    simply nothing to do, and saying so here keeps the decision in one place.
    """
    want = SCALES.get(str(scale or ""), {}).get("height", 0)
    height = info.get("height") or 0
    return want if want and height and want < height else 0


def build_args(source: Path, target: Path, fmt: str, quality: str,
               info: dict, scale: str = "") -> list:
    ffmpeg = engine.ffmpeg_path()
    kind = kind_of(fmt)
    if kind == "video":
        return _video_args(ffmpeg, source, target, fmt, quality, info, scale)
    if kind == "gif":
        return _gif_args(ffmpeg, source, target)

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


def _video_args(ffmpeg, source: Path, target: Path, fmt: str, quality: str,
                info: dict, scale: str) -> list:
    spec = VIDEO_FORMATS[fmt]
    args = [str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-i", str(source)]

    height = shrink_to(info, scale)

    # Copying the picture is the whole point of a remux: an MP4 becoming an
    # MKV finishes in a second. Shrinking rules it out, because a scaled
    # picture has to be drawn again.
    if not height and info.get("vcodec") in spec["vcopy"]:
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", spec["vcodec"], "-crf", CRF.get(quality, CRF["normal"])]
        if spec["vcodec"] == "libx264":
            # yuv420p because a 10-bit or 4:4:4 source encoded as-is produces a
            # file most players refuse, which is the opposite of the point.
            args += ["-preset", "medium", "-pix_fmt", "yuv420p"]
        if height:
            args += ["-vf", f"scale=-2:{height}"]

    if info.get("has_audio"):
        if info.get("codec") in spec["acopy"]:
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", spec["acodec"],
                     "-b:a", QUALITY.get(quality, QUALITY["high"])["bitrate"]]
    else:
        args += ["-an"]

    # MKV holds nearly any subtitle; MP4 and WebM refuse most of them, and a
    # refused subtitle stream fails the whole conversion. Dropping it there is
    # the difference between a file and an error.
    args += ["-c:s", "copy"] if fmt == "mkv" else ["-sn"]

    if fmt in ("mp4", "mov"):
        args += ["-movflags", "+faststart"]

    args += ["-map_metadata", "0"]
    args += ["-progress", "pipe:1", "-nostats", str(target)]
    return args


def _gif_args(ffmpeg, source: Path, target: Path) -> list:
    # -t before -i stops the reading rather than the writing, so a two-hour
    # film costs fifteen seconds of work instead of two hours of it.
    return [str(ffmpeg), "-hide_banner", "-nostdin", "-y",
            "-t", str(GIF_SECONDS), "-i", str(source),
            "-vf", (f"fps=12,scale={GIF_WIDTH}:-2:flags=lanczos,"
                    "split[a][b];[a]palettegen=stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=bayer:bayer_scale=5"),
            "-loop", "0", "-an",
            "-progress", "pipe:1", "-nostats", str(target)]


_OUT_TIME = re.compile(r"out_time_ms=(\d+)")


def run(source, target_dir, fmt: str, quality: str, job=None,
        scale: str = "") -> dict:
    """
    Convert one file. `job` is optional; when given, its percent and status
    are updated as ffmpeg reports progress, and cancelling it stops the work.
    """
    source = Path(source)
    if not source.exists():
        return {"ok": False, "error": "That file is not there any more."}
    if engine.ffmpeg_path() is None:
        return {"ok": False, "error": "The audio tools are missing. Reinstall Riplox."}

    kind = kind_of(fmt)
    if not kind:
        return {"ok": False, "error": "Unknown format."}

    info = inspect(source)
    if kind == "audio" and not info.get("has_audio"):
        return {"ok": False, "error": "There is no audio in that file."}
    if kind in ("video", "gif") and not info.get("has_video"):
        return {"ok": False, "error": "There is no video in that file."}

    target_dir = Path(target_dir or source.parent)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": f"Cannot write there: {exc}"}

    # The original is never the target. Converting an MP4 into an MP4 - a
    # remux, or a shrink - would land on the file being read, and free_name is
    # what steps aside: the name is already taken, so the answer is a new one.
    target = free_name(target_dir / f"{source.stem}.{fmt}")

    args = build_args(source, target, fmt, quality, info, scale)

    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=_NO_WINDOW)
    if job is not None:
        job.proc = proc

    duration = info.get("duration") or 0
    if kind == "gif":
        duration = min(duration, GIF_SECONDS) if duration else GIF_SECONDS
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

    if kind == "audio":
        copied = info.get("codec") in FORMATS[fmt]["copyable"]
    elif kind == "video":
        copied = (not shrink_to(info, scale)
                  and info.get("vcodec") in VIDEO_FORMATS[fmt]["vcopy"])
    else:
        copied = False

    return {"ok": True, "path": str(target), "copied": copied,
            "size": target.stat().st_size}
