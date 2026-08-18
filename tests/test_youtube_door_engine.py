"""
The YouTube door through the engine - the part the door test cannot reach.

Runs _second_door against a real link, so it covers the two-stream download,
the ffmpeg join, the file that lands on disk, and what the log says about it.
Downloads into a temporary folder and deletes it afterwards.

Nothing is written to the real download folder, the history file, or settings.
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import engine                                                      # noqa: E402

PASS = FAIL = 0
LINK = "https://www.youtube.com/watch?v=jNQXAC9IVRw"      # 19s, the oldest one


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def run(quality, folder, note=""):
    """One trip through the door, with history writing stubbed out."""
    job = engine.Job(url=LINK, quality=quality)
    job.error = "yt-dlp said no (pretend)"
    manager = engine.DownloadManager.__new__(engine.DownloadManager)
    settings = {"download_dir": str(folder), "subfolder_per_site": False,
                "second_door": True, "prefer_h264": True}

    kept, engine.add_history = engine.add_history, lambda entry: None
    try:
        manager._second_door(job, settings)
    finally:
        engine.add_history = kept
    return job


print(f"ffmpeg present: {engine.has_ffmpeg()}")
print(f"\nbest quality through the door  ({LINK})")
work = Path(tempfile.mkdtemp(prefix="riplox_door_"))
try:
    job = run("best", work)
    check("finished", job.status == "done", job.status + " " + (job.error or ""))
    if job.status == "done":
        landed = Path(job.filepath)
        check("file is on disk", landed.exists(), landed.name)
        check("file has real bytes", landed.stat().st_size > 100_000,
              engine.human_bytes(landed.stat().st_size))
        check("title came from YouTube", job.title != LINK, job.title)
        check("uploader came from YouTube", bool(job.uploader), job.uploader)
        check("log says the door was used",
              "own route to YouTube" in job.log)
        check("nothing left behind",
              not list(landed.parent.glob("*.part")),
              ", ".join(p.name for p in landed.parent.glob("*.part")) or "clean")
        if engine.has_ffmpeg():
            check("the two streams were joined",
                  "joined" in job.log, "joined")
            probe = engine.ffmpeg_path()
            check("ffmpeg is available to inspect with", probe is not None)

        # The real proof that the join worked: both streams in one file.
        import subprocess
        ffprobe = (engine.ffmpeg_path().parent / "ffprobe.exe"
                   if engine.ffmpeg_path() else None)
        if ffprobe and ffprobe.exists():
            out = subprocess.run(
                [str(ffprobe), "-v", "error", "-show_entries",
                 "stream=codec_type", "-of", "csv=p=0", str(landed)],
                capture_output=True, text=True, creationflags=engine._NO_WINDOW)
            kinds = [line.strip() for line in out.stdout.splitlines() if line.strip()]
            check("the file really holds video and audio",
                  "video" in kinds and "audio" in kinds, ",".join(kinds))
        else:
            print("  --    ffprobe not found, skipping the stream check")
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\na height cap goes through the door too")
work = Path(tempfile.mkdtemp(prefix="riplox_door_"))
try:
    job = run("360", work)
    check("finished", job.status == "done", job.status + " " + (job.error or ""))
    if job.status == "done":
        check("file is on disk", Path(job.filepath).exists())
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\na link that is not one video keeps yt-dlp's own error")
work = Path(tempfile.mkdtemp(prefix="riplox_door_"))
try:
    job = engine.Job(url="https://www.youtube.com/@someone", quality="best")
    job.error = "yt-dlp said no (pretend)"
    manager = engine.DownloadManager.__new__(engine.DownloadManager)
    manager._second_door(job, {"download_dir": str(work),
                               "subfolder_per_site": False,
                               "second_door": True, "prefer_h264": True})
    check("refused", job.status == "error", job.status)
    check("said something readable", bool(job.error), job.error[:80])
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\nthe door can be turned off")
work = Path(tempfile.mkdtemp(prefix="riplox_door_"))
try:
    job = engine.Job(url=LINK, quality="best")
    job.error = "yt-dlp said no (pretend)"
    job.status = "error"
    manager = engine.DownloadManager.__new__(engine.DownloadManager)
    manager._second_door(job, {"download_dir": str(work),
                               "subfolder_per_site": False,
                               "second_door": False, "prefer_h264": True})
    check("left alone when switched off", job.status == "error"
          and job.error == "yt-dlp said no (pretend)", job.error[:50])
finally:
    shutil.rmtree(work, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
