"""The library, counted - and the counting has to be right.

Insights is only worth having if the numbers are true, because the whole point
of it is turning "it feels broken lately" into a figure somebody acts on. A
wrong failure rate is worse than no failure rate.

So this builds a library whose answers are known by construction and checks
every number against them.

⚠️ Writes nothing, but it reads through engine's own loaders, so the data
directory is pointed at a temporary one and the redirect is asserted before
anything is read.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import engine

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-insights-"))
engine.data_dir = lambda: SANDBOX

print("\n-- the sandbox is real before anything is read ----------------------")
check("history goes to the temporary directory",
      str(SANDBOX) in str(engine.history_file()), str(engine.history_file()))
check("so does the failed list",
      str(SANDBOX) in str(engine.failed_file()), str(engine.failed_file()))


def when(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S",
                         time.localtime(time.time() - days_ago * 86400))


# Known by construction:
#   YouTube   4 finished (100 + 200 + 300 + 400 MB), 1 failed  -> 20.0% failed
#   TikTok    1 finished (500 MB),                   3 failed  -> 75.0% failed
#   Instagram 1 finished (1.0 GB),                   0 failed  ->  0.0%
#   total     6 finished, 4 failed, 10 tried        -> 60.0% first try
#   this week 2 of them (0 and 3 days ago)
HISTORY = [
    {"url": "https://www.youtube.com/watch?v=a", "size": "100 MB", "when": when(0)},
    {"url": "https://www.youtube.com/watch?v=b", "size": "200 MB", "when": when(3)},
    {"url": "https://www.youtube.com/watch?v=c", "size": "300 MB", "when": when(20)},
    {"url": "https://www.youtube.com/watch?v=d", "size": "400 MB", "when": when(40)},
    {"url": "https://www.tiktok.com/@x/video/1", "size": "500 MB", "when": when(30)},
    {"url": "https://www.instagram.com/p/xyz/",  "size": "1.0 GB", "when": when(50)},
]
FAILED = (
    [{"url": "https://www.youtube.com/watch?v=e"}] +
    [{"url": "https://www.tiktok.com/@x/video/%d" % i} for i in (2, 3, 4)]
)

engine.history_file().write_text(json.dumps(HISTORY), encoding="utf-8")
engine.failed_file().write_text(json.dumps(FAILED), encoding="utf-8")

r = engine.insights()

print("\n-- the totals ------------------------------------------------------")
check("every finished download is counted once", r["files"] == 6, str(r["files"]))
# 100+200+300+400+500 MB is 1.46 GB, and the Instagram gigabyte takes it to
# 2.5. Binary units throughout, which is why 1000 MB is not a gigabyte.
check("the sizes add up", r["size"] == "2.5 GB", r["size"])
check("this week is the two from this week", r["week"] == 2, str(r["week"]))
check("and their sizes only", r["week_size"] == "300.0 MB", r["week_size"])
check("the oldest download sets the start date",
      r["since"] == when(50)[:10], r["since"])

print("\n-- worked first try ------------------------------------------------")
check("it is finished out of everything tried",
      r["first_try"] == 60.0, str(r["first_try"]) + "% of " + str(r["first_try_of"]))
check("and the denominator is finished plus failed",
      r["first_try_of"] == 10, str(r["first_try_of"]))
check("the failures are counted", r["failed"] == 4, str(r["failed"]))

print("\n-- per site, which is the part worth having --------------------------")
by = {s["site"]: s for s in r["sites"]}
check("the sites are the sites", set(by) == {"YouTube", "TikTok", "Instagram"},
      ", ".join(sorted(by)))
check("YouTube: 4 done, 1 failed, 20% failed",
      by["YouTube"]["done"] == 4 and by["YouTube"]["failed"] == 1
      and by["YouTube"]["rate"] == 20.0, str(by["YouTube"]))
check("TikTok: the one that is actually broken reads 75%",
      by["TikTok"]["rate"] == 75.0, str(by["TikTok"]["rate"]))
check("Instagram: nothing failed, so nothing is claimed",
      by["Instagram"]["rate"] == 0.0, str(by["Instagram"]["rate"]))
check("the busiest site is first", r["sites"][0]["site"] == "YouTube",
      r["sites"][0]["site"])
check("and it is named as the one most of the library came from",
      r["top"] == "YouTube" and r["top_share"] == 66.7,
      r["top"] + " " + str(r["top_share"]) + "%")

print("\n-- an empty library says nothing rather than dividing by zero --------")
engine.history_file().write_text("[]", encoding="utf-8")
engine.failed_file().write_text("[]", encoding="utf-8")
e = engine.insights()
check("no files, no crash", e["files"] == 0 and e["first_try"] == 0.0, str(e["first_try"]))
check("no site is claimed to be the top one", e["top"] == "", repr(e["top"]))
check("and there is nothing in the breakdown", e["sites"] == [], str(e["sites"]))

print("\n-- a size it cannot read is zero, not a guess ------------------------")
engine.history_file().write_text(json.dumps(
    [{"url": "https://www.youtube.com/watch?v=a", "size": "who knows", "when": when(1)}]),
    encoding="utf-8")
u = engine.insights()
check("an unreadable size does not become a number out of nowhere",
      u["size"] == "0 B", u["size"])
check("...and the file is still counted", u["files"] == 1, str(u["files"]))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
