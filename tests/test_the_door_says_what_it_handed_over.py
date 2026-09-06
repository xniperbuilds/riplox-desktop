"""Riplox's own route is held to the same standard as yt-dlp's.

When yt-dlp's fallback rungs hand back a small file, the row says so and Retry
means something. The second door set status="done" and said nothing - and it
is the route taken precisely when things have already gone wrong.

It can genuinely return less. Without ffmpeg there is nothing to merge, and
the only combined stream YouTube offers is itag 18 at 360p. A request for max
then landed as a 360p file marked done, with no note, while the identical
result through yt-dlp would have been flagged.

    python tests/test_the_door_says_what_it_handed_over.py
"""
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-door-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


# -- the judging, on its own ------------------------------------------------

print("-- what counts as short " + "-" * 45)

short = engine.quality_shortfall
check("max answered at 360 when 2160 existed", bool(short("max", 360, 2160)))
check("best answered at 360 when 2160 existed", bool(short("best", 360, 2160)))
check("2160 asked, 360 given", bool(short("2160", 360, 2160)))
check("1080 asked, 360 given", bool(short("1080", 360, 2160)))

check("a 360 video really only being 360 is not short",
      short("max", 360, 360) == "", short("max", 360, 360))
check("nothing taller on offer is not short", short("2160", 720, 720) == "")
check("the rung just below the request is near enough",
      short("1080", 1080, 2160) == "" and short("1080", 720, 2160) != "",
      "1080 for 1080 passes; 720 for 1080 does not")
check("asked small, got small", short("360", 360, 2160) == "")
check("max answered above the fallback ceiling is not questioned",
      short("max", 720, 2160) == "",
      "only a suspiciously small answer is worth the words")
check("no height means no verdict", short("max", 0, 2160) == "",
      "a warning built on no measurement would be worse")
check("audio-only asks nothing of height", short("mp3", 0, 2160) == "")

message = short("max", 360, 2160)
check("the message names both numbers",
      "360p" in message and "2160p" in message, message[:60])
check("and says the file was kept", "is saved" in message)
check("and that Retry means something", "Retry" in message)

# -- and the door, end to end ----------------------------------------------


class FakeDoor(object):
    """doors.py, reduced to what _second_door asks of it."""

    DoorError = type("DoorError", (Exception,), {})

    def __init__(self, info):
        self.info = info

    def handles(self, url):
        return True

    def configure(self, proxy):
        pass

    def resolve(self, url, quality="", prefer_h264=True, can_merge=True):
        return dict(self.info)


class Manager(engine.DownloadManager):
    def __init__(self):
        self._jobs, self._order = {}, []
        self._lock = engine.threading.RLock()
        self.pulled = []

    def _door_path(self, settings, job, info):
        return SANDBOX / ("door_%s.%s" % (job.id, info.get("ext") or "mp4"))

    def _door_pull(self, address, where, headers, deadline, progress, job,
                   proxy=None):
        self.pulled.append(address)
        where.write_bytes(b"x" * 4096)


def door_run(info, quality="max"):
    sys.modules["doors"] = FakeDoor(info)
    engine.clear_history()
    job = engine.Job("https://www.youtube.com/watch?v=x", quality=quality)
    man = Manager()
    man._second_door(job, dict(engine.DEFAULT_SETTINGS))
    return job, engine.load_history()


YT = {"url": "https://cdn/v.mp4", "id": "x", "ext": "mp4", "headers": {},
      "site": "YouTube", "note": "", "title": "A video"}

print("\n-- a request for max answered with the 360p muxed stream " + "-" * 12)

job, history = door_run(dict(YT, height=360, best_height=2160))
check("the row does NOT say done", job.status != "done", job.status)
check("it says what happened instead",
      "360p" in job.error and "2160p" in job.error, job.error[:60])
check("the file is still on disk", Path(job.filepath).exists())
check("and it is not filed away as a finished download", history == [],
      len(history))

print("\n-- the same route answering in full " + "-" * 33)

job, history = door_run(dict(YT, height=2160, best_height=2160))
check("done", job.status == "done", job.status)
check("nothing is said", job.error == "", job.error)
check("and it reaches the library", len(history) == 1, len(history))

print("\n-- a door that is handed one file and takes it " + "-" * 22)

# TikTok, Instagram, Facebook: no choice of streams, no height reported.
job, history = door_run(dict(YT, site="TikTok", title="A clip"))
check("silence, not a guess", job.status == "done" and job.error == "",
      job.status)
check("it reaches the library", len(history) == 1, len(history))

print("\n-- and the height is recorded on the job either way " + "-" * 17)

job, _ = door_run(dict(YT, height=1080, best_height=2160), quality="1080")
check("what arrived is written down", job.height == 1080, job.height)
check("1080 asked and 1080 given is not short", job.status == "done",
      job.error or job.status)

print("\n-- and the site is not blamed for a stream the site served " + "-" * 9)


class Refused(Manager):
    """yt-dlp had every attempt and got nowhere. Then the door runs."""

    def _run_engine(self, job, settings):
        job.status = "error"
        job.error = "ERROR: Sign in to confirm you're not a bot"
        return False

    def _signed_out_retry(self, job, settings):
        return False


def whole_job(info, quality="max"):
    sys.modules["doors"] = FakeDoor(info)
    engine._health.clear()
    job = engine.Job("https://www.youtube.com/watch?v=x", quality=quality)
    man = Refused()
    man._run_job(job)
    return job, {row["site"]: row["state"] for row in engine.health()}


job, marks = whole_job(dict(YT, height=360, best_height=2160))
check("the row still says the file came back small",
      job.status == "error" and "360p" in job.error, job.status)
check("but YouTube is recorded as REACHED, not down",
      marks.get("YouTube") == engine.HEALTH_DOOR, marks)

job, marks = whole_job(dict(YT, height=2160, best_height=2160))
check("a full answer through the door is recorded the same way",
      marks.get("YouTube") == engine.HEALTH_DOOR, marks)

print("\n-- the engine path still judges by the same rule " + "-" * 20)

check("_quality_short uses it", "quality_shortfall" in (
    engine.inspect.getsource(engine.DownloadManager._quality_short)
    if hasattr(engine, "inspect") else
    __import__("inspect").getsource(engine.DownloadManager._quality_short)),
    "one rule, two routes - that is the whole fix")

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
