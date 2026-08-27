"""
The two guards the pre-release audit turned up.

A t=2 grid over the eight things a download can be set to found two situations
the six new features had not accounted for. Both were the same shape: the
screen offered something the rest of the app could not deliver.

  1. Cutting a video into parts is ffmpeg's work, and yt-dlp refuses outright
     without it - measured: "You have requested downloading the video
     partially, but ffmpeg is not installed. Aborting", nothing written. The
     trim block has been hidden without an encoder since long before this
     release; chapters and clips were not.
  2. A name typed by hand names one file. yt-dlp resolves every section to
     that same name - measured: two chapters, one "my name.webm". Twelve
     ticked chapters would have produced one file and no explanation.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-cutguards-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

import app as riplox                                        # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


print("\n-- one name cannot cover a folder of parts ------------------------")
client = riplox.app.test_client()
HEAD = {"X-Riplox-Token": riplox.TOKEN, "Content-Type": "application/json"}
BASE = {"items": [{"url": "https://www.youtube.com/watch?v=abc"}], "quality": "1080"}

for label, opts in (("chapters", {"chapters": ["Intro"], "outtmpl": "my name.%(ext)s"}),
                    ("all chapters", {"chapters_all": True, "outtmpl": "my name.%(ext)s"}),
                    ("clips", {"clips": [{"start": 10, "end": 40}],
                               "outtmpl": "my name.%(ext)s"})):
    r = client.post("/api/add", headers=HEAD, json=dict(BASE, opts=opts))
    body = r.get_json() or {}
    check(f"a name beside {label} is refused, and says why",
          r.status_code == 400 and "one file" in (body.get("error") or ""),
          f"{r.status_code} {str(body.get('error'))[:60]}")

# Asked BEFORE anything allowed is sent, or the queue would be holding a job
# this check put there itself - which would be a bad test, not a defect.
jobs = (client.get("/api/jobs", headers=HEAD).get_json() or {}).get("jobs")
check("nothing was queued by a refused request", not jobs, str(len(jobs or [])))

r = client.post("/api/add", headers=HEAD,
                json=dict(BASE, opts={"outtmpl": "my name.%(ext)s"}))
check("a name on its own is still allowed", r.status_code == 200, str(r.status_code))


print("\n-- a piece of a video does not carry the whole video's marks ------")
# Found by Nazim in real use: a 17-second chapter reported twenty minutes.
# yt-dlp had written all 63 of the source's chapter marks into it, the last of
# them ending at 1214 seconds, and players read that track for the length.
man = engine.DownloadManager()
MARKED = dict(engine.DEFAULT_SETTINGS, download_dir=str(SANDBOX),
              embed_chapters=True)


def marks(opts, start="", end="", quality="1080"):
    job = engine.Job(url="https://www.youtube.com/watch?v=abc", quality=quality,
                     opts=opts)
    job.start, job.end = start, end
    return "--embed-chapters" in man.build_args(job, MARKED, "", None)


check("a whole video still gets its chapter marks", marks({}))
check("a chapter download does not", not marks({"chapters": ["Intro"]}))
check("nor does all-chapters", not marks({"chapters_all": True}))
check("nor do clips", not marks({"clips": [{"start": 10, "end": 40}]}))
check("nor does a trim", not marks({}, start="1:00", end="2:00"))
check("and the screen says so rather than dropping it quietly",
      "belong to the whole video" in
      (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8"))


print("\n-- cutting on the mark, when it is asked for ---------------------")
# Reported from real use: about half a second of the part before showed at the
# start of a chapter. Sections are cut on the video's own keyframes, so ffmpeg
# has to begin at the keyframe before the mark. The only way past it is to
# re-encode - measured at 218s against 94s for the same two-minute clip - so
# it is offered rather than assumed, exactly as the trim already offers it.
def exact_in(opts, exact, start="", end=""):
    job = engine.Job(url="https://www.youtube.com/watch?v=abc", quality="1080",
                     opts=opts)
    job.start, job.end, job.exact = start, end, exact
    return "--force-keyframes-at-cuts" in man.build_args(job, MARKED, "", None)


for label, opts in (("chapters", {"chapters": ["Intro"]}),
                    ("all chapters", {"chapters_all": True}),
                    ("clips", {"clips": [{"start": 10, "end": 40}]})):
    check(f"{label}: off by default, so the fast cut stays the default",
          not exact_in(opts, False))
    check(f"{label}: on when it is asked for", exact_in(opts, True))

check("a trim still has it too", exact_in({}, True, start="1:00", end="2:00"))
check("and a whole video never does", not exact_in({}, True))

js_now = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
html_now = (SRC / "templates" / "index.html").read_text(encoding="utf-8")
check("there is a box for it", "cutExact" in html_now and "cutExact" in js_now)
check("it is only shown while something is being cut into parts",
      '$("cutExactWrap").hidden = !cutting;' in js_now)
check("it is cleared for every new link, like the rest of this screen",
      '$("cutExact").checked = false;' in js_now)
check("the label says what it costs, not just what it does",
      "slower" in html_now and "re-encoded" in html_now)


check("the shown command and the real one are built in one place",
      "function applyCut(body)" in js_now
      and js_now.count("applyCut(body);") == 2)


print("\n-- what cannot be cut is not offered -----------------------------")
js = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
html = (SRC / "templates" / "index.html").read_text(encoding="utf-8")

check("the chapter ticks are drawn only when the encoder is there",
      "var canCut = !!S.hasFfmpeg;" in js and "canCut ?" in js)
check("the all-chapters tick goes with them",
      '$("chapterAllWrap").hidden = !canCut;' in js)
check("the clips tick goes with them", "!peaks.length || !S.hasFfmpeg" in js)
check("one line says why, rather than the panels just going quiet",
      "cutNoFf" in html and "cutNoFf" in js and "media tool" in html)
# Verified against a copy of the app whose has_ffmpeg() answered False: the
# fifteen chapters were still listed, no tick boxes were drawn, the clips box
# and the all-chapters box were hidden, the line was shown, and the graph -
# which needs no encoder - stayed.
check("the list itself is still shown, because it is worth reading",
      'chapterList").innerHTML' in js)
check("the name field says why it is out of reach, like it does for a playlist",
      "One name cannot cover a folder of parts" in js
      and "One name cannot cover a playlist" in js)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
