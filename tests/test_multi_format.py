"""
One press, two files: the video and the mp3 queued together.

The rules worth holding: the extras never replace the main choice, a picked
format id never travels onto the mp3 (it names a video stream and would
produce the wrong file), and nothing an unknown quality name says gets
through.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-multi-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import app as riplox_app                                    # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


URL = "https://www.youtube.com/watch?v=abcdefghijk"


def queued(body):
    """Run /api/add with this body and report the jobs it created."""
    riplox_app.manager._jobs.clear()
    riplox_app.manager._order.clear()
    with riplox_app.app.test_request_context(json=body):
        res = riplox_app.api_add()
    payload = res[0].get_json() if isinstance(res, tuple) else res.get_json()
    jobs = list(riplox_app.manager._jobs.values())
    return payload, jobs


print("\n-- without the extra, nothing changes ----------------------------")
body, jobs = queued({"items": [{"url": URL}], "quality": "1080"})
check("one job", len(jobs) == 1, str(len(jobs)))
check("at the quality asked for", jobs[0].quality == "1080")
check("the count is honest", body.get("added") == 1)

print("\n-- with it, both are queued from one press -----------------------")
body, jobs = queued({"items": [{"url": URL}], "quality": "1080", "also": ["mp3"]})
qualities = sorted(j.quality for j in jobs)
check("⭐ two jobs from one press", len(jobs) == 2, str(qualities))
check("the video is still the quality asked for", "1080" in qualities)
check("...and the mp3 came with it", "mp3" in qualities)
check("both counted", body.get("added") == 2, str(body.get("added")))
check("same link on both", len({j.url for j in jobs}) == 1)

print("\n-- a picked format never travels onto the mp3 --------------------")
body, jobs = queued({"items": [{"url": URL}], "quality": "1080",
                     "also": ["mp3"], "opts": {"format_id": "137"}})
main = [j for j in jobs if j.quality == "1080"][0]
audio = [j for j in jobs if j.quality == "mp3"][0]
check("the video keeps the picked format", main.opts.get("format_id") == "137",
      str(main.opts))
check("⭐ the mp3 does NOT (it names a video stream)",
      "format_id" not in audio.opts, str(audio.opts))

print("\n-- the two files cannot collide ----------------------------------")
man = engine.DownloadManager()
settings = dict(engine.DEFAULT_SETTINGS, download_dir=str(SANDBOX))
names = {j.quality: man._outtmpl(settings, j) for j in jobs}
check("they are written to different names",
      names["1080"] != names["mp3"], "")
check("the audio one ends up an mp3", "%(ext)s" in names["mp3"]
      and "height" not in names["mp3"], names["mp3"].rsplit("\\", 1)[-1])

print("\n-- rubbish in `also` is refused ----------------------------------")
body, jobs = queued({"items": [{"url": URL}], "quality": "1080",
                     "also": ["mp3", "nonsense", "1080", "mp3", "wav"]})
qualities = sorted(j.quality for j in jobs)
check("unknown names dropped", "nonsense" not in qualities and "wav" not in qualities,
      str(qualities))
check("a repeat of the main choice is not queued twice",
      qualities.count("1080") == 1, str(qualities))
check("a repeat inside the extras is not either",
      qualities.count("mp3") == 1, str(qualities))

body, jobs = queued({"items": [{"url": URL}], "quality": "1080",
                     "also": ["mp3"] * 40})
check("a long list cannot blow up the queue", len(jobs) == 2, str(len(jobs)))

print("\n-- a playlist gets both for every entry --------------------------")
items = [{"url": f"https://www.youtube.com/watch?v=vid{i:08d}"} for i in range(5)]
body, jobs = queued({"items": items, "quality": "720", "also": ["mp3"]})
check("five links became ten jobs", len(jobs) == 10, str(len(jobs)))
check("five of each", sorted(j.quality for j in jobs).count("mp3") == 5)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
