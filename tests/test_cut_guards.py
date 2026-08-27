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
