"""
What the screen is told about a video's chapters.

Read-only, and the honest cases are the point. Most videos have no chapters
at all, and a site can say so in two different ways - a missing field or a
null one - which both have to arrive as "none" rather than as an error or a
blank row. The screen then says "This video has no chapters" out loud,
because a feature that silently shows nothing cannot be told apart from a
feature that is broken.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-chapterlist-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


# Read off youtube.com/watch?v=q3AuP01daL4 on 2026-08-27 with the same
# -J --flat-playlist that analyze() itself runs, so this is the shape the
# real code is handed, not a shape invented for the test.
REAL = [
    {"start_time": 0, "title": "Intro", "end_time": 64},
    {"start_time": 64, "title": "Setup & Installation", "end_time": 596},
    {"start_time": 596, "title": "Printing & Variables", "end_time": 1113},
    {"start_time": 1113, "title": "Data Types", "end_time": 1294},
    {"start_time": 7058, "title": "Mini Project: Guess the Number", "end_time": 7352},
]


print("\n-- a real video's chapters come through unchanged -----------------")
rows = engine._chapter_rows({"chapters": REAL})
check("every chapter is there", len(rows) == len(REAL), str(len(rows)))
check("in the order the video has them",
      [r["title"] for r in rows] == [c["title"] for c in REAL])
check("the first one starts at zero, and zero survives",
      rows[0]["start"] == 0, repr(rows[0]["start"]))
check("ampersands and colons are left alone, not stripped",
      rows[1]["title"] == "Setup & Installation"
      and rows[4]["title"] == "Mini Project: Guess the Number")
check("the end time comes too", rows[0]["end"] == 64, repr(rows[0]["end"]))


print("\n-- the ordinary case: no chapters ---------------------------------")
check("a video with no chapters field at all", engine._chapter_rows({}) == [])
check("a video whose chapters are null", engine._chapter_rows({"chapters": None}) == [])
check("a video with an empty chapter list", engine._chapter_rows({"chapters": []}) == [])


print("\n-- rubbish in the list is dropped, not shown ----------------------")
junk = engine._chapter_rows({"chapters": [
    {"start_time": 0, "title": "Real", "end_time": 10},
    {"start_time": 10, "title": "", "end_time": 20},
    {"start_time": 20, "title": "   ", "end_time": 30},
    {"start_time": 30, "end_time": 40},
    {"start_time": 40, "title": None, "end_time": 50},
    "not a chapter at all",
    None,
]})
check("only the one real chapter survives", len(junk) == 1, str(junk))
check("and it is the right one", junk and junk[0]["title"] == "Real")

odd = engine._chapter_rows({"chapters": [
    {"start_time": "0:00", "title": "String time", "end_time": None},
]})
check("a time that is not a number becomes no time, not a crash",
      odd[0]["start"] is None and odd[0]["end"] is None, str(odd))
check("...and the chapter itself is still listed", len(odd) == 1)


print("\n-- two chapters can carry the same title, and both are real -------")
# Not invented: youtube.com/watch?v=linlz7-Pnvw has 63 chapters and says "An
# aerial view of the Rocky Mountains in Switzerland." twice, at 2:30 and at
# 17:15. Collapsing them on screen would be the app deciding one of the two
# does not exist.
twins = engine._chapter_rows({"chapters": [
    {"start_time": 150, "title": "An aerial view of the Rocky Mountains.", "end_time": 161},
    {"start_time": 1035, "title": "An aerial view of the Rocky Mountains.", "end_time": 1061},
]})
check("both are listed, not folded into one", len(twins) == 2, str(len(twins)))
check("and they keep their own times",
      twins[0]["start"] == 150 and twins[1]["start"] == 1035)


print("\n-- nothing is quietly left out ------------------------------------")
many = engine._chapter_rows({"chapters": [
    {"start_time": i * 10, "title": f"Chapter {i}", "end_time": i * 10 + 10}
    for i in range(200)
]})
check("two hundred chapters means two hundred rows", len(many) == 200, str(len(many)))
check("the last one is the last one", many[-1]["title"] == "Chapter 199")


print("\n-- and analyze() actually puts them on the video ------------------")
# The wiring, without a network. analyze() is handed a recorded answer of the
# shape yt-dlp really returns; a helper that works while nothing calls it is
# the failure this catches.
import json                                                 # noqa: E402


class FakeRun:
    def __init__(self, payload):
        self.returncode = 0
        self.stdout = json.dumps(payload)
        self.stderr = ""


real_run = engine._run
try:
    payload = {"_type": "video", "title": "A video", "webpage_url": "https://x/y",
               "duration": 7352, "formats": [], "chapters": REAL}
    engine._run = lambda *a, **k: FakeRun(payload)
    info = engine.analyze("https://www.youtube.com/watch?v=q3AuP01daL4", {})
    check("the video carries its chapters", len(info.get("chapters") or []) == 5,
          str(len(info.get("chapters") or [])))
    check("titles intact through the whole path",
          (info.get("chapters") or [{}])[0].get("title") == "Intro")

    payload["chapters"] = None
    info = engine.analyze("https://www.youtube.com/watch?v=q3AuP01daL4", {})
    check("a video without them still has the key, empty",
          info.get("chapters") == [], repr(info.get("chapters")))
except engine.EngineMissing:
    print("  --    yt-dlp binary not present, skipping")
finally:
    engine._run = real_run


print("\n-- the screen has somewhere to put them ---------------------------")
# The helper and the markup are in different files and nothing else ties them
# together, so a rename on one side would otherwise be found by a person.
html = (SRC / "templates" / "index.html").read_text(encoding="utf-8")
js = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
css = (SRC / "static" / "css" / "app.css").read_text(encoding="utf-8")

for element in ("chapterBox", "chapterCount", "chapterList", "chapterNote"):
    check(f"#{element} exists in the page and is used by the script",
          element in html and element in js)
check("the no-chapters line is written down, not built in the script",
      "This video has no chapters." in html)
check("the list starts hidden, so a page with no video shows no empty box",
      'id="chapterBox" hidden' in html)
check("a playlist is told apart from a video with none",
      "isList ? null : info.chapters" in js)
check("the list can scroll rather than being cut short",
      ".chapter-list" in css and "overflow-y: auto" in css)
check("chapters sharing a title are marked on screen",
      "ch-twin" in js and "ch-twin" in css)
check("and the mark says why, before anything is pressed",
      "arrive together" in js)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
