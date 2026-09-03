"""
Five ticked, three files, and the job went green.

Reported by Nazim from real use. Every section of a cut is fetched on its own -
ffmpeg opens the media URL again for each one - so a site can refuse one and
hand over the rest. Nothing was counting, so the job finished, the folder was
short, and the only way to notice was to count the files by hand.

The screen says how many files it is asking for, because it is the only side
that knows a ticked row is a file: two chapters sharing a title are one pattern
and two files, and the engine sees only patterns.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-parts-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


print("\n-- the number survives the trip ----------------------------------")
check("the screen's count is accepted",
      engine.clean_opts({"parts_expected": 5}).get("parts_expected") == 5)
check("a number that is not one is dropped",
      "parts_expected" not in engine.clean_opts({"parts_expected": "five"}))
check("zero is not a count",
      "parts_expected" not in engine.clean_opts({"parts_expected": 0}))
check("and an absurd one is pulled back to what a command can hold",
      engine.clean_opts({"parts_expected": 9999}).get("parts_expected") == 500)


print("\n-- a job counts what it was handed -------------------------------")
job = engine.Job(url="u", quality="1080", opts={})
check("a fresh job has counted nothing", job.parts == 0, str(job.parts))
check("the slot exists, so counting cannot raise",
      "parts" in engine.Job.__slots__)


print("\n-- and a short folder is not 'done' ------------------------------")
# The finishing code is inside _spawn, so the shape of the decision is
# checked here rather than the whole download being run: exit code zero and
# fewer parts than asked for must not end as done.
src = (SRC / "engine.py").read_text(encoding="utf-8")
check("the count is compared with what was asked for",
      'wanted = int(job.opts.get("parts_expected") or 0)' in src)
check("fewer parts than asked for is an error, not a green row",
      'if wanted and getattr(job, "parts", 0) < wanted:' in src)
check("the message says how many of how many",
      "Only {job.parts} of the {wanted} parts" in src)
check("...and that what did arrive is still there",
      "What did arrive is in the folder" in src)
check("...and that a retry will not fetch them again",
      "without downloading these again" in src)

check("a part that was already on disk still counts as arrived",
      src.count('job.parts = getattr(job, "parts", 0) + 1') == 2)
# A retry re-lists every part, so a count carried over from the last attempt
# would make a short folder add up to a full one.
check("each attempt starts counting from zero again",
      "        job.parts = 0" in src)

js = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
check("the screen sends the number of rows it ticked",
      "body.opts.parts_expected = chapterPicks.length || currentClips().length" in js)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
