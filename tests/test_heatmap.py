"""
The most-replayed curve, and the day it stops arriving.

⚠️ `heatmap` is not in `yt-dlp --help` at all. It is an undocumented field, so
no deprecation process protects it: the day YouTube changes the shape of its
answer it simply stops being there, with no error anywhere. That is the same
silent shape this project keeps getting bitten by, so the missing case is
tested FIRST here and treated as the ordinary one - the audit asked for that
in those words, and it is the reason this file leads with absence rather than
with a pretty curve.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-heatmap-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


print("\n-- FIRST: the day it stops arriving -------------------------------")
check("a video with no heatmap field at all", engine._heatmap_rows({}) == [])
check("a video whose heatmap is null", engine._heatmap_rows({"heatmap": None}) == [])
check("a video whose heatmap is empty", engine._heatmap_rows({"heatmap": []}) == [])
# yt-dlp prints the word NA when a field is missing. If that ever reaches the
# info dict as a string rather than as nothing, it must still mean "none".
check("the literal string NA is not a curve",
      engine._heatmap_rows({"heatmap": "NA"}) == [])
check("...and nor is a number", engine._heatmap_rows({"heatmap": 1}) == [])
check("no curve means no peaks, not five invented ones",
      engine.heatmap_peaks([]) == [])
check("and peaks of nothing is not an error",
      engine.heatmap_peaks(engine._heatmap_rows({"heatmap": None})) == [])

# The shape can also arrive half-changed: the field is there, the points are
# not what they were. Every one of these has to come back as "no curve"
# rather than as a graph drawn from rubbish.
for junk in ([{"start_time": 0}], [{"value": 0.5}], ["not a point"], [None],
             [{"start_time": "0", "end_time": "2", "value": "1"}],
             [{"start_time": 5, "end_time": 5, "value": 1}],
             [{"start_time": 5, "end_time": 1, "value": 1}]):
    check(f"a malformed point is dropped: {str(junk)[:44]}",
          engine._heatmap_rows({"heatmap": junk}) == [])

# A curve that is flat at zero is not five moments.
flat = [{"start_time": i * 2, "end_time": i * 2 + 2, "value": 0} for i in range(100)]
check("a curve flat at zero has no moments in it",
      engine.heatmap_peaks(engine._heatmap_rows({"heatmap": flat})) == [],
      str(engine.heatmap_peaks(engine._heatmap_rows({"heatmap": flat}))))


print("\n-- a real curve comes through unchanged ---------------------------")
# Read off youtube.com/watch?v=q3AuP01daL4. YouTube always sends exactly 100
# buckets covering the whole video, so a bucket is a hundredth of the
# duration - 73.53s on this two-hour one. Nothing may assume a fixed window.
REAL = [{"start_time": i * 73.53, "end_time": (i + 1) * 73.53,
         "value": v} for i, v in enumerate(
    [0.0, 0.02, 0.05, 0.30, 0.627, 0.40, 1.0, 0.649, 0.617, 0.20]
    + [0.05] * 12 + [0.626, 0.30] + [0.04] * 11 + [0.66, 0.30] + [0.03] * 40
    + [0.701, 0.30] + [0.02] * 7 + [0.64, 0.20] + [0.01] * 11 + [0.141])]
check("the fixture really is a hundred buckets", len(REAL) == 100, str(len(REAL)))

rows = engine._heatmap_rows({"heatmap": REAL})
check("every bucket survives", len(rows) == 100, str(len(rows)))
check("the busiest one is 1.0", max(r["value"] for r in rows) == 1.0)
check("a bucket is a hundredth of the video, not a fixed window",
      round(rows[0]["end"] - rows[0]["start"], 2) == 73.53,
      str(round(rows[0]["end"] - rows[0]["start"], 2)))
check("a value over one would be pulled back to one",
      engine._heatmap_rows({"heatmap": [{"start_time": 0, "end_time": 2,
                                         "value": 4.2}]})[0]["value"] == 1.0)
check("...and a negative one up to zero",
      engine._heatmap_rows({"heatmap": [{"start_time": 0, "end_time": 2,
                                         "value": -1}]})[0]["value"] == 0.0)


print("\n-- five moments, not one moment counted five times ----------------")
peaks = engine.heatmap_peaks(rows)
check("five of them", len(peaks) == 5, str(len(peaks)))
check("the busiest is first", peaks[0]["value"] == 1.0, str(peaks[0]["value"]))
check("they are ranked from one",
      [p["rank"] for p in peaks] == [1, 2, 3, 4, 5], str([p["rank"] for p in peaks]))
# The bug this guards: index 7 and 8 are the shoulders of the 1.0 hump at
# index 6, and both outrank most of the video. Taken naively, "the five most
# replayed moments" came back as one moment and the buckets leaning on it.
starts = sorted(p["start"] for p in peaks)
gaps = [round((b - a) / 73.53) for a, b in zip(starts, starts[1:])]
check("no two of them lean against each other", all(g >= 3 for g in gaps), str(gaps))
check("asking for fewer gives fewer", len(engine.heatmap_peaks(rows, want=2)) == 2)
check("asking for more than there is room for does not loop forever",
      len(engine.heatmap_peaks(rows, want=999)) <= len(rows),
      str(len(engine.heatmap_peaks(rows, want=999))))

short = engine._heatmap_rows({"heatmap": [
    {"start_time": 0, "end_time": 2, "value": 1.0},
    {"start_time": 2, "end_time": 4, "value": 0.5},
]})
check("a two-bucket curve gives one moment, not two touching ones",
      len(engine.heatmap_peaks(short)) == 1, str(len(engine.heatmap_peaks(short))))


print("\n-- and analyze() hands it to the screen ---------------------------")
import json                                                 # noqa: E402


class FakeRun:
    def __init__(self, payload):
        self.returncode = 0
        self.stdout = json.dumps(payload)
        self.stderr = ""


real_run = engine._run
try:
    payload = {"_type": "video", "title": "A video", "webpage_url": "https://x/y",
               "duration": 7352, "formats": [], "heatmap": REAL}
    engine._run = lambda *a, **k: FakeRun(payload)
    info = engine.analyze("https://www.youtube.com/watch?v=q3AuP01daL4", {})
    check("the curve reaches the screen", len(info.get("heatmap") or []) == 100,
          str(len(info.get("heatmap") or [])))
    check("with its moments already worked out",
          len(info.get("peaks") or []) == 5, str(len(info.get("peaks") or [])))

    payload.pop("heatmap")
    info = engine.analyze("https://www.youtube.com/watch?v=q3AuP01daL4", {})
    check("a video without one still has both keys, empty",
          info.get("heatmap") == [] and info.get("peaks") == [],
          repr(info.get("heatmap")) + " " + repr(info.get("peaks")))
except engine.EngineMissing:
    print("  --    yt-dlp binary not present, skipping")
finally:
    engine._run = real_run


print("\n-- the screen says when there is nothing to show ------------------")
html = (SRC / "templates" / "index.html").read_text(encoding="utf-8")
js = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
css = (SRC / "static" / "css" / "app.css").read_text(encoding="utf-8")

for element in ("heatBox", "heatNote", "heatGraph", "heatPeaks"):
    check(f"#{element} exists in the page and is used by the script",
          element in html and element in js)
check("an absent curve is said out loud, not left blank",
      "no most-replayed data" in html.lower())
# Saying "YouTube has no data for this" under a TikTok link would be noise
# about a feature that was never on offer there.
check("...and only where the feature exists at all", 'youtube' in js.lower())
check("the graph is drawn from the numbers, not from a picture",
      "svg" in js.lower() and ".heat-graph" in css)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
