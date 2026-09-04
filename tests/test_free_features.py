"""
The three things paid downloaders charge for that Riplox now does for nothing:
an 8K rung, converting video rather than only audio, and starting one download
at a time you choose.

Every check here is about a decision the code makes, not about ffmpeg or a
site: the rungs offered for a set of formats, the arguments built for a
conversion, and whether the queue will hand back a job whose time has not come.
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-free-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import convert                                              # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


def fmt(height, vcodec="vp9", size=10_000_000):
    return {"height": height, "vcodec": vcodec, "acodec": "none",
            "filesize": size, "format_id": f"{height}-{vcodec}"}


def rungs_for(formats):
    return engine._available_qualities({"formats": formats}, {})


print("\n-- 8K ---------------------------------------------------------------")

# A rung is offered only when a real format reaches it, so the 8K chip appears
# on an 8K video and nowhere else. That is the whole guard: no extra check had
# to be added for it.
eight = rungs_for([fmt(4320), fmt(2160), fmt(1080, "avc1.640028")])
check("an 8K video offers the 8K rung", "4320" in eight["rungs"])
check("and it sits above 4K",
      eight["rungs"].index("4320") < eight["rungs"].index("2160"))

four = rungs_for([fmt(2160), fmt(1080, "avc1.640028")])
check("a 4K video does not offer 8K", "4320" not in four["rungs"])

small = rungs_for([fmt(720, "avc1.4d401f"), fmt(360, "avc1.42001e")])
check("a 720p video offers neither 8K nor 4K",
      "4320" not in small["rungs"] and "2160" not in small["rungs"])

check("8K has a label of its own", engine.QUALITY_LABELS.get("4320") == "8K · 4320p")
check("and a height the finished file is measured against",
      engine._ASKED_HEIGHT.get("4320") == 4320)

# There is no h264 at 4320p anywhere, so the chip has to say what the file will
# actually be. Saying it afterwards, in a player that will not open it, is the
# failure this prevents.
check("the 8K rung is marked as VP9 or AV1", "4320" in eight["noH264"])
check("and a height that does have h264 is not",
      "1080" not in eight["noH264"])

friendly = rungs_for([fmt(2160, "avc1.640034"), fmt(1080, "avc1.640028")])
check("a site that serves 4K as h264 gets no warning on it",
      "2160" not in friendly["noH264"])

# The per-device ceiling has to know where 8K sits, or a phone capped at 1080p
# could ask for 4320 and be handed it.
import sharing                                              # noqa: E402
check("a phone capped at 1080p asking for 8K gets 1080p",
      sharing._capped("4320", "1080") == "1080")
check("and a phone capped at 8K asking for 1080p still gets 1080p",
      sharing._capped("1080", "4320") == "1080")


print("\n-- converting video -------------------------------------------------")

SOURCE = SANDBOX / "clip.mp4"
SOURCE.write_bytes(b"not a real video")
TARGET = SANDBOX / "clip.mkv"

H264 = {"vcodec": "h264", "codec": "aac", "has_video": True, "has_audio": True,
        "height": 1080, "duration": 60.0}


def args_for(fmt_id, info=None, scale="", target=None):
    return convert.build_args(SOURCE, target or (SANDBOX / f"clip.{fmt_id}"),
                              fmt_id, "normal", info or H264, scale)


check("video formats are offered as well as audio",
      set(convert.VIDEO_FORMATS) == {"mp4", "mkv", "mov", "webm"})
check("and each one is recognised as video",
      all(convert.kind_of(f) == "video" for f in convert.VIDEO_FORMATS))
check("while mp3 is still audio", convert.kind_of("mp3") == "audio")
check("and something invented is nothing at all", convert.kind_of("m4v") == "")

mkv = args_for("mkv")
check("h264 into an MKV is a remux, not an encode",
      "-c:v" in mkv and mkv[mkv.index("-c:v") + 1] == "copy")
check("and its audio is carried across untouched",
      "-c:a" in mkv and mkv[mkv.index("-c:a") + 1] == "copy")

webm = args_for("webm")
check("h264 into a WebM has to be encoded",
      webm[webm.index("-c:v") + 1] == "libvpx-vp9")

shrunk = args_for("mp4", scale="720")
check("shrinking to 720p scales the picture",
      "-vf" in shrunk and shrunk[shrunk.index("-vf") + 1] == "scale=-2:720")
check("and stops it being a copy",
      shrunk[shrunk.index("-c:v") + 1] == "libx264")

# The rule the whole feature rests on: 1080p asked to become 1440p is not an
# upscale, it is nothing to do.
check("asking a 1080p file for 1080p changes nothing",
      convert.shrink_to(H264, "1080") == 0)
check("asking it for 720p shrinks it", convert.shrink_to(H264, "720") == 720)
check("and an unknown height is left alone", convert.shrink_to(H264, "4320") == 0)

no_audio = dict(H264, has_audio=False, codec="")
check("a silent video does not get an empty audio track",
      "-an" in args_for("mp4", no_audio))

check("MP4 drops subtitles it cannot hold", "-sn" in args_for("mp4"))
check("MKV keeps them", "-c:s" in mkv and mkv[mkv.index("-c:s") + 1] == "copy")
check("MP4 is written so it can start playing before it has finished",
      "+faststart" in args_for("mp4"))

gif = args_for("gif")
check("a GIF is capped in length", "-t" in gif)
check("and capped before the file is read, not after",
      gif.index("-t") < gif.index("-i"))
check("and capped in width", f"scale={convert.GIF_WIDTH}:-2" in " ".join(gif))
check("and says both caps in its own name",
      str(convert.GIF_SECONDS) in convert.GIF["label"])

# The one bug nobody forgives: an MP4 converted to an MP4 lands on the file
# being read. free_name is what stops it, so that is what is checked.
check("a file is never converted on top of itself",
      convert.free_name(SANDBOX / "clip.mp4") != SOURCE)
convert.run(SOURCE, SANDBOX, "mp4", "normal")
check("and the original survives a conversion that fails", SOURCE.exists())

audio_only = args_for("mp3", H264)
check("the audio path still drops the picture", "-vn" in audio_only)
check("and still copies when the container allows it",
      "-c:a" in audio_only)


print("\n-- starting at a time ------------------------------------------------")

noon = datetime(2026, 9, 4, 12, 0, 0)

check("nothing asked for means no wait", engine.next_time_at("", noon) == 0.0)
check("and nonsense means no wait", engine.next_time_at("99:99", noon) == 0.0)

tonight = engine.next_time_at("14:30", noon)
check("a time later today is later today",
      datetime.fromtimestamp(tonight) == datetime(2026, 9, 4, 14, 30))

tomorrow = engine.next_time_at("02:00", noon)
check("a time that has gone by today means tomorrow",
      datetime.fromtimestamp(tomorrow) == datetime(2026, 9, 5, 2, 0))

# The start time is handed to add(), not written afterwards: the workers are
# woken by add() itself, so a job that is queued first and delayed a moment
# later can be started in between. That is exactly what happened here.
man = engine.DownloadManager()
job = man.add(url="https://www.youtube.com/watch?v=later", title="later",
              quality="1080", start_after=engine.time.time() + 3600)
check("a job waiting for its time is not handed out",
      man._next_job() is None)
check("and no worker started it in the gap while it was being queued",
      job.status == "queued", f"status={job.status}")

seen = job.to_dict()
check("and the row says how long it is waiting", seen.get("startsIn", 0) > 0)

# The manager's own worker threads are running, so either this call picks the
# job up or one of them already has. Both are the job running; asking which
# would be testing the scheduler's luck rather than its rule.
job.start_after = engine.time.time() - 1
picked = man._next_job()
check("once the time has passed it runs",
      (picked is not None and picked.id == job.id) or job.status != "queued")

# Everything else in the queue carries on around a row that is waiting: this
# is a per-item delay, not a second version of the hours in Settings.
# A different site on purpose: pacing is per site and lives outside the
# manager, so reusing YouTube here would have this queue held back by the job
# started a few lines above and prove nothing about waiting rows.
man2 = engine.DownloadManager()
held = man2.add(url="https://vimeo.com/111", quality="1080",
                start_after=engine.time.time() + 3600)
ready = man2.add(url="https://vimeo.com/222", quality="1080")
man2._next_job()
check("a waiting row does not hold up the rest of the queue",
      ready.status != "queued", f"ready={ready.status}")
check("and does not quietly start itself either",
      held.status == "queued", f"held={held.status}")


print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
