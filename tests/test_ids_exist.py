"""
Everything the script reaches into the page for by name.

app.js calls $("someId") 513 times, and index.html is where those ids live.
Nothing checks that the two agree. A renamed id, or an element moved during a
redesign and dropped on the way, produces `null` at runtime - and the failure
lands wherever that null is first used, which is usually somewhere else and
usually much later.

Ids were once taken to be the whole join between the interface and the
behaviour. They are not - the redesign audit counted 269 other coupling
points - but they are the largest, and one more is checked here.

The script reads eight data attributes off the page that it never writes:
data-dir, data-export, data-info, data-short, data-sub, data-theme, data-view
and data-what. index.html carries all eight. The redesign deck carries none of
them, so rebuilding the page from the deck would drop every one - and
`el.dataset.view` on an element without the attribute is `undefined`, not an
error. Same silent shape as a missing id, so it is guarded the same way.

An element may move to any screen, change its tag and be restyled completely.
If it loses its id, or a data attribute the script reads, this goes red on the
spot.
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

print("\n-- every data attribute the script reads has somewhere to come from -")


def kebab(name):
    """dataset.copyText is the data-copy-text attribute."""
    return "data-" + re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), name)


# Written as markup rather than assigned: index.html holds some, and app.js
# writes others into the rows it builds. Both count as a source.
in_markup = set(re.findall(r"(data-[a-z0-9-]+)\s*=", HTML))
in_markup |= set(re.findall(r"(data-[a-z0-9-]+)\s*=", JS.replace("\\", "")))
assigned = set(re.findall(r"\.dataset\.([A-Za-z0-9_]+)\s*=", JS))
touched = set(re.findall(r"\.dataset\.([A-Za-z0-9_]+)", JS))

print(f"      {len(touched)} keys touched · {len(assigned)} the script assigns "
      f"· {len(in_markup)} written as attributes")

orphans = sorted(k for k in touched
                 if k not in assigned and kebab(k) not in in_markup)
check("no dataset key is read that nothing ever sets",
      not orphans, ", ".join(orphans) if orphans else "")

# The eight that come from index.html specifically. These are the ones a
# rebuilt page would drop, because the deck has none of them.
FROM_PAGE = ["data-dir", "data-export", "data-info", "data-short",
             "data-sub", "data-theme", "data-view", "data-what"]
page_attrs = set(re.findall(r"(data-[a-z0-9-]+)\s*=", HTML))
gone = [a for a in FROM_PAGE if a not in page_attrs]
check("the page still carries the data attributes the script reads from it",
      not gone, ", ".join(gone) if gone else "")

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
