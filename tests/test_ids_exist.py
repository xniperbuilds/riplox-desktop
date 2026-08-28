"""
Every id the script reaches for is in the page.

The interface and the behaviour are joined at 252 named ids and nothing else:
app.js calls $("someId") 513 times, and index.html is where those ids live.
Nothing checks that the two agree. A renamed id, or an element moved during a
redesign and dropped on the way, produces `null` at runtime - and the failure
lands wherever that null is first used, which is usually somewhere else and
usually much later.

This is the test the 1.5 redesign is built behind: an element may move to any
screen, change its tag and be restyled completely, but if it loses its id this
goes red on the spot.
"""

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
HTML = (SRC / "templates" / "index.html").read_text(encoding="utf-8")
JS = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


# Ids the page defines, and ids the script asks for by name.
defined = set(re.findall(r'\bid="([A-Za-z][\w-]*)"', HTML))
asked = set(re.findall(r'\$\(\s*"([A-Za-z][\w-]*)"\s*\)', JS))

# Some are created by the script itself rather than written in the page - they
# are looked up after being built, which is fine. Named here so the check stays
# a real check rather than a list of exceptions that quietly grows.
BUILT_AT_RUNTIME = set()

print("\n-- the page holds every id the script looks up ---------------------")
print(f"      page defines {len(defined)} · script asks for {len(asked)}")

missing = sorted(asked - defined - BUILT_AT_RUNTIME)
check("no id is asked for that the page does not have",
      not missing, ", ".join(missing[:8]) if missing else "")

# The other direction is information, not a failure: markup carries ids for CSS
# and for anchors too, so an unused one is ordinary.
unused = sorted(defined - asked)
print(f"      ids defined but never looked up by name: {len(unused)}"
      + (" (ordinary - CSS and anchors use ids too)" if unused else ""))

print("\n-- and the ids the 1.5 features depend on are all there ------------")
# These are the ones added in 1.5.0. A redesign that moves the chapter panel
# into a tab must carry every one of them across.
FEATURE_IDS = [
    "chapterBox", "chapterCount", "chapterList", "chapterNote", "chapterAll",
    "chapterAllWrap", "chapterPicked", "heatBox", "heatSummary", "heatGraph",
    "heatPeaks", "heatNote", "clipOn", "clipOnWrap", "clipRow", "clipLens",
    "clipNote", "cutExact", "cutExactWrap", "cutNoFf", "optWriteDesc",
]
for i in FEATURE_IDS:
    check(f"#{i}", i in defined)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
