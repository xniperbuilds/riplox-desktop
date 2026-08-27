"""
Cutting the most replayed moments out of a video.

The moments come from YouTube; the clips do not. YouTube always sends exactly
100 buckets, so a bucket is a hundredth of the video - two and a half seconds
on a four-minute upload, seventy-three on a two-hour one. Neither of those is
a clip, so a length is chosen and the moment sits in the middle of it. That is
this app's decision rather than YouTube's, and everything below is about not
lying about the difference.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-clips-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


man = engine.DownloadManager()
DOWNLOADS = SANDBOX / "Downloads"
DOWNLOADS.mkdir(parents=True, exist_ok=True)
SETTINGS = dict(engine.DEFAULT_SETTINGS, download_dir=str(DOWNLOADS))


def peak(start, end):
    return {"start": start, "end": end, "value": 1.0}


def command(opts=None, quality="1080", start="", end=""):
    job = engine.Job(url="https://www.youtube.com/watch?v=abc", quality=quality,
                     opts=opts or {})
    job.start, job.end = start, end
    return man.build_args(job, SETTINGS, "", None)


def sections(args):
    return [args[i + 1] for i, a in enumerate(args) if a == "--download-sections"]


print("\n-- nothing to cut ------------------------------------------------")
check("no moments, no clips", engine.peak_clips([], 30) == [])
check("no length, no clips", engine.peak_clips([peak(100, 102)], 0) == [])
check("a length that is not a number is not a length",
      engine.peak_clips([peak(100, 102)], "half a minute") == [])
check("a moment missing its times is skipped, not guessed",
      engine.peak_clips([{"value": 1}], 30) == [])
check("and nothing at all is not an error", engine.clip_args([]) == [])


print("\n-- the moment sits in the middle of the clip ----------------------")
one = engine.peak_clips([peak(440, 450)], 30, duration=7352)
check("one moment, one clip", len(one) == 1, str(one))
check("centred on it", one[0]["start"] == 430 and one[0]["end"] == 460, str(one[0]))
check("and exactly the length asked for",
      one[0]["end"] - one[0]["start"] == 30, str(one[0]))
check("a different length is a different clip",
      engine.peak_clips([peak(440, 450)], 60, 7352)[0] == {"start": 415, "end": 475},
      str(engine.peak_clips([peak(440, 450)], 60, 7352)[0]))


print("\n-- a clip cannot run off either end of the video ------------------")
# Pulled back inside rather than shortened: a moment near the start is still
# worth the full length, it just cannot begin before the video does.
early = engine.peak_clips([peak(0, 4)], 30, duration=600)
check("a moment at the very beginning starts at zero",
      early[0]["start"] == 0, str(early[0]))
check("...and still gets its full length",
      early[0]["end"] - early[0]["start"] == 30, str(early[0]))
late = engine.peak_clips([peak(596, 600)], 30, duration=600)
check("a moment at the very end stops at the end",
      late[0]["end"] == 600, str(late[0]))
check("...and still gets its full length",
      late[0]["end"] - late[0]["start"] == 30, str(late[0]))
tiny = engine.peak_clips([peak(4, 8)], 30, duration=10)
check("a video shorter than the clip is not overrun",
      tiny[0]["end"] <= 10, str(tiny[0]))
check("an unknown duration does not stop it working",
      engine.peak_clips([peak(440, 450)], 30, duration=0) == [{"start": 430, "end": 460}],
      str(engine.peak_clips([peak(440, 450)], 30, 0)))


print("\n-- two moments close together are one clip, not two of the same ---")
# The peaks are only required to be three buckets apart, and on a short video
# three buckets is under ten seconds. Cutting both would hand back two clips
# of nearly the same footage and call them different moments.
near = engine.peak_clips([peak(100, 106), peak(112, 118)], 30, duration=600)
check("they run together into one clip", len(near) == 1, str(near))
check("which covers both", near[0]["start"] <= 88 and near[0]["end"] >= 130, str(near[0]))
far = engine.peak_clips([peak(100, 106), peak(400, 406)], 30, duration=600)
check("moments far apart stay two clips", len(far) == 2, str(far))
check("in the order they happen", far[0]["start"] < far[1]["start"], str(far))
# The list arrives ranked by how replayed each moment is, not by time.
unordered = engine.peak_clips([peak(400, 406), peak(100, 106)], 30, duration=600)
check("a list ranked by replays still comes back in time order",
      [c["start"] for c in unordered] == sorted(c["start"] for c in unordered),
      str(unordered))
check("the same length is asked for either way", len(unordered) == 2, str(unordered))


print("\n-- and they reach the command as time ranges ----------------------")
args = command({"clips": [{"start": 430, "end": 460}, {"start": 1600, "end": 1630}]})
check("one section per clip", len(sections(args)) == 2, str(sections(args)))
check("written as a time range, with the star that means one",
      sections(args) == ["*430-460", "*1600-1630"], str(sections(args)))
check("ffmpeg can still report progress", "--no-quiet" in args)
check("a backwards range is dropped rather than sent",
      engine.clip_args([{"start": 50, "end": 20}]) == [])


print("\n-- one kind of cut at a time --------------------------------------")
# All three of these speak through --download-sections, and yt-dlp adds
# everything it is given rather than choosing between them.
with_trim = command({"clips": [{"start": 10, "end": 40}]}, start="1:00", end="2:00")
check("clips replace a trim", sections(with_trim) == ["*10-40"], str(sections(with_trim)))
both = command({"clips": [{"start": 10, "end": 40}], "chapters": ["Intro"]})
check("chapters win over clips, rather than joining them",
      sections(both) == [engine.chapter_regex("Intro")], str(sections(both)))
check("part of a video stays out of the download archive",
      "--download-archive" not in command({"clips": [{"start": 10, "end": 40}]},
                                          quality="best"))
# The third pairing, checked here as well as with the chapters: adding a clip
# branch beside the chapter one rather than after it left the trim reaching
# the command whenever chapters were picked and clips were not - a bug in one
# combination introduced by fixing another.
chapters_and_trim = command({"chapters": ["Intro"]}, start="1:00", end="2:00")
check("adding clips did not let a trim back in beside chapters",
      sections(chapters_and_trim) == [engine.chapter_regex("Intro")],
      str(sections(chapters_and_trim)))


print("\n-- where the clips go --------------------------------------------")
tmpl = man._outtmpl(SETTINGS, engine.Job(url="u", quality="1080",
                                         opts={"clips": [{"start": 10, "end": 40}]}))
check("under a Clips folder, not the Chapters one",
      "Clips" in tmpl and "Chapters" not in tmpl, tmpl)
check("one folder per video, with its id and quality",
      "%(title).100B [%(id)s]" in tmpl and "%(height)sp" in tmpl, tmpl)
# Measured on the bundled binary: a section picked by time comes back with
# section_title AND section_number both NA. Only a section picked by chapter
# name has them, so a clip cannot be named after the moment.
check("named by the second it starts at, since a time range has no title",
      "%(section_start)05d" in tmpl and "%(section_title)s" not in tmpl, tmpl)
check("zero-padded, so the folder sorts in the order things happen",
      "05d" in tmpl, tmpl)


print("\n-- what arrives from the browser is not trusted -------------------")
ok = engine.clean_opts({"clips": [{"start": 10, "end": 40}]})
check("a good pair comes through", ok.get("clips") == [{"start": 10, "end": 40}])
check("a backwards pair is dropped",
      "clips" not in engine.clean_opts({"clips": [{"start": 40, "end": 10}]}))
check("a negative start is dropped",
      "clips" not in engine.clean_opts({"clips": [{"start": -5, "end": 10}]}))
check("text where a number should be is dropped",
      "clips" not in engine.clean_opts({"clips": [{"start": "a", "end": "b"}]}))
check("something that is not a list is not a selection",
      "clips" not in engine.clean_opts({"clips": "10-40"}))
check("a pair that is not a pair is dropped",
      "clips" not in engine.clean_opts({"clips": ["10-40"]}))
check("fifty is as many as go on one command line",
      len(engine.clean_opts({"clips": [{"start": i * 100, "end": i * 100 + 30}
                                       for i in range(80)]})["clips"]) == 50)


print("\n-- and analyze() offers a set of ranges per length ----------------")
import json                                                 # noqa: E402


class FakeRun:
    def __init__(self, payload):
        self.returncode = 0
        self.stdout = json.dumps(payload)
        self.stderr = ""


REAL = [{"start_time": i * 73.53, "end_time": (i + 1) * 73.53,
         "value": 1.0 if i == 6 else (0.7 if i == 60 else 0.01)}
        for i in range(100)]

real_run = engine._run
try:
    payload = {"_type": "video", "title": "A video", "webpage_url": "https://x/y",
               "duration": 7352, "formats": [], "heatmap": REAL}
    engine._run = lambda *a, **k: FakeRun(payload)
    info = engine.analyze("https://www.youtube.com/watch?v=abc", {})
    clips = info.get("clips") or {}
    check("one set per offered length",
          sorted(clips.keys()) == sorted(str(n) for n in engine.CLIP_LENGTHS),
          str(sorted(clips.keys())))
    check("a longer length gives longer clips",
          clips["60"][0]["end"] - clips["60"][0]["start"] == 60
          and clips["15"][0]["end"] - clips["15"][0]["start"] == 15,
          str(clips["15"][0]) + " " + str(clips["60"][0]))
    check("the screen is handed the ranges, not asked to work them out",
          all(isinstance(c["start"], int) for c in clips["30"]), str(clips["30"][:2]))

    payload.pop("heatmap")
    info = engine.analyze("https://www.youtube.com/watch?v=abc", {})
    check("a video with no curve offers no clips",
          all(not v for v in (info.get("clips") or {}).values()),
          str(info.get("clips")))
except engine.EngineMissing:
    print("  --    yt-dlp binary not present, skipping")
finally:
    engine._run = real_run


print("\n-- the screen ----------------------------------------------------")
html = (SRC / "templates" / "index.html").read_text(encoding="utf-8")
js = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
for element in ("clipOn", "clipLens", "clipNote"):
    check(f"#{element} exists in the page and is used by the script",
          element in html and element in js)
check("the screen sends the ranges the engine worked out",
      "opts.clips" in js or "clips =" in js)
check("it says only these parts are downloaded",
      "not the whole video" in js)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
