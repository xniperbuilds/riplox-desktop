"""The quality a person picked is the quality that gets downloaded.

Reported 1 Sep 2026: "max pe kar raha hun download, par library mein Best
available show ho raha". It was not the Library - that renders what the history
says, and the history said "best" because the download really did run as best.

The swap happened in the browser. When a link is analysed the chips are rebuilt
from the rungs the video offers, and any current choice missing from that list
is replaced with the first one. The fallback list used when a link offers no
rungs was written before the "max" rung existed and never gained it, so "max"
was always missing from it and was always replaced by its first entry, "best".

⭐ WHY ONLY YOUTUBE, which is how it was reported. A watch link copied from a
browser usually carries a playlist on it - "&list=...&start_radio=1" - and
Riplox analyses that as a playlist, which is exactly when the fallback is used.
Instagram links never carry one. From the history of 1 Sep 2026, 14 of 14:

    17:29  best   &list= present        14:56  max   no list=
    16:58  best   &list= present        11:53  max   no list=
    16:53  best   &list= present        11:51  max   no list=
    16:48  best   &list= present        10:59  max   no list=
    16:41  best   &list= present        10:52  max   no list=

Every link with a list recorded as best; every link without one kept max.
⚠️ And it sticks: the chosen quality is not reset per link, so the two
downloads after a swapped one were also best despite carrying no list.

Two faults, and the second is the one that matters: the list was wrong, AND
the replacement was silent.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:92]) if detail else ""))


APP = (ROOT / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8")

print("\n-- the fallback list, where the choice went to die ----------------")
found = re.search(r'\?\s*info\.qualities\s*\n?\s*:\s*(\[[^\]]*\])', APP)
check("the fallback list is where it always was", bool(found))
fallback = found.group(1) if found else ""
check("⭐ it now offers max, so a chosen max survives a link with no rungs",
      '"max"' in fallback, fallback)
check("...and still offers best, which everything can do",
      '"best"' in fallback, fallback)

print("\n-- and every rung the engine offers is in it ----------------------")
# The engine is the authority on what a person can pick. Anything it offers
# and this list omits is another silent swap waiting to happen.
offered = set(engine.QUALITY_LABELS) - {"1440", "2160", "360"}
missing = sorted(q for q in offered if '"%s"' % q not in fallback)
check("⭐ nothing the engine offers is missing from the fallback",
      not missing, "missing: %s" % missing if missing else fallback)

print("\n-- ⭐⭐ and a swap that does happen is not silent -----------------")
swap = re.search(r'if \(options\.indexOf\(quality\) === -1\) \{(.{0,400}?)\n    \}',
                 APP, re.S)
check("the replacement is no longer a bare one-liner", bool(swap))
body = swap.group(1) if swap else ""
check("⭐ it tells the user", "toast(" in body, body.strip()[:80])
check("...naming what they asked for", "asked" in body, body.strip()[:80])

print("\n-- ⚠ and the engine still offers max at all -----------------------")
# If this ever stops being true the fallback above is wrong in the other
# direction - offering a rung nothing can deliver.
check("⭐ max is a real rung the engine knows",
      "max" in engine.QUALITY_LABELS, sorted(engine.QUALITY_LABELS))
check("...and its label is not 'Best available'",
      engine.QUALITY_LABELS["max"] != engine.QUALITY_LABELS["best"],
      "%s vs %s" % (engine.QUALITY_LABELS["max"], engine.QUALITY_LABELS["best"]))

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
