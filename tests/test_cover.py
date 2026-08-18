"""
Choosing the cover picture.

The interesting half is not the picking - it is that a chosen cover is an
address which arrives from the browser and is then fetched by the app itself.
An address pointing at 127.0.0.1 would have Riplox make a request to its own
API on somebody else's behalf, so that is what most of this checks.

Then it does the real thing once: download a video, attach a chosen cover,
and read the result back with ffprobe.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import doors                                                       # noqa: E402
import engine                                                      # noqa: E402

PASS = FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


print("addresses Riplox is willing to fetch")
for good in ["https://i.ytimg.com/vi/x/maxresdefault.jpg",
             "https://scontent.cdninstagram.com/v/t51/x.jpg"]:
    check(good[:52], engine.safe_image(good) == good)

print("\nand the ones it is not")
BAD = {
    "http://i.ytimg.com/x.jpg": "not https",
    "https://127.0.0.1/x.jpg": "a bare address",
    "https://127.0.0.1:8791/api/settings": "riplox's own api",
    "https://localhost/x.jpg": "localhost",
    "https://[::1]/x.jpg": "ipv6 loopback",
    "https://169.254.169.254/latest/meta-data/": "cloud metadata",
    "file:///C:/Windows/win.ini": "a local file",
    "javascript:alert(1)": "a script",
    "": "nothing",
}
for bad, why in BAD.items():
    check(why, engine.safe_image(bad) == "", bad[:44] or "(empty)")

check("an absurdly long one is refused",
      engine.safe_image("https://x.com/" + "a" * 2000) == "")

print("\nit only survives cleaning as a real address")
check("a good one is kept",
      engine.clean_opts({"thumb_url": "https://i.ytimg.com/x.jpg"})
      .get("thumb_url") == "https://i.ytimg.com/x.jpg")
check("riplox's own api is dropped",
      "thumb_url" not in engine.clean_opts(
          {"thumb_url": "https://127.0.0.1:8791/api/settings"}))

print("\nthe list offered to the user")
info = {"thumbnails": [
    {"url": "https://i.ytimg.com/a.jpg", "width": 120, "height": 90},
    {"url": "https://i.ytimg.com/b.jpg", "width": 640, "height": 480},
    {"url": "https://i.ytimg.com/c.jpg", "width": 1280, "height": 720},
    {"url": "https://i.ytimg.com/d.jpg", "width": 1280, "height": 720},
    {"url": "http://i.ytimg.com/insecure.jpg", "width": 99, "height": 99},
    {"url": "https://127.0.0.1/evil.jpg", "width": 98, "height": 98},
    {"url": "https://i.ytimg.com/e.jpg", "id": "0"},
]}
rows = engine._thumb_rows(info)
urls = [r["url"] for r in rows]
check("biggest first", rows[0]["width"] == 1280, rows[0]["label"])
check("the same size twice is listed once",
      len([u for u in urls if u.endswith(("c.jpg", "d.jpg"))]) == 1,
      ", ".join(Path(u).name for u in urls))
check("an insecure one never reaches the screen",
      not any("insecure" in u for u in urls))
check("nor does one pointing at this machine",
      not any("127.0.0.1" in u for u in urls))
check("one with no size still gets a label",
      any(r["label"] == "0" for r in rows), [r["label"] for r in rows])
check("a site with forty tiles does not become the screen",
      len(engine._thumb_rows({"thumbnails": [
          {"url": f"https://i.ytimg.com/{n}.jpg", "width": n, "height": n}
          for n in range(1, 41)]})) <= 8)
check("no thumbnails means no picker", engine._thumb_rows({}) == [])

print("\nlive: a real download with a chosen cover")
work = Path(tempfile.mkdtemp(prefix="riplox_cover_"))
try:
    got = doors.resolve("https://www.youtube.com/watch?v=jNQXAC9IVRw",
                        quality="best", can_merge=engine.has_ffmpeg())
    cover = engine.safe_image(got.get("thumbnail"))
    check("the door offered a cover address", bool(cover), (cover or "")[:52])

    job = engine.Job(url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
                     quality="best", opts={"thumb_url": cover})
    job.error = "yt-dlp said no (pretend)"
    manager = engine.DownloadManager.__new__(engine.DownloadManager)
    kept, engine.add_history = engine.add_history, lambda entry: None
    try:
        manager._second_door(job, {"download_dir": str(work),
                                   "subfolder_per_site": False,
                                   "second_door": True, "prefer_h264": True})
    finally:
        engine.add_history = kept
    check("the video downloaded", job.status == "done",
          job.status + " " + (job.error or ""))

    if job.status == "done":
        said = manager._cover(job, cover)
        check("the cover step reported what it did", bool(said), said)
        beside = Path(job.filepath).with_suffix(".jpg")
        check("the picture is on disk beside the video", beside.exists(),
              engine.human_bytes(beside.stat().st_size) if beside.exists() else "")

        ffprobe = engine.ffmpeg_path().parent / "ffprobe.exe"
        if ffprobe.exists():
            out = subprocess.run(
                [str(ffprobe), "-v", "error", "-show_entries",
                 "stream=codec_name:stream_disposition=attached_pic",
                 "-of", "csv=p=0", str(job.filepath)],
                capture_output=True, text=True,
                creationflags=engine._NO_WINDOW)
            check("the video still opens after being rewritten",
                  out.returncode == 0 and "h264" in out.stdout,
                  out.stdout.replace("\n", " ")[:60])
            check("and now carries an attached picture",
                  "1" in out.stdout.split("\n")[-2] if len(
                      out.stdout.strip().split("\n")) > 1 else False,
                  out.stdout.replace("\n", " | ")[:70])
        check("nothing was left half-written",
              not list(Path(job.filepath).parent.glob("*.cover*")),
              "clean")
except doors.DoorError as exc:
    check("live run", False, str(exc)[:90])
finally:
    shutil.rmtree(work, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
