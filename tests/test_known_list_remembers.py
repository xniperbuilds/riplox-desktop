"""What a followed item remembers, and how far back it really goes.

KNOWN_CAP says 400 ids per item. The list was grown by plain concatenation, so
a check that found nothing new still wrote its whole page of ids to the front
again - and after a dozen checks the 400 slots held little but repeats of the
newest thirty, with anything older pushed off the end.

It never bit, and the reason is worth writing down: PEEK is 30 and a feed
returns 15, so a look never reaches back past what is still remembered. Two
constants agreeing by luck is not a guard. Raise either one and old videos
return as new - which, with auto-download on, is three re-downloads a check,
for ever.

    python tests/test_known_list_remembers.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-known-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import watch                                                # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


print("-- one id, once " + "-" * 52)

check("a repeat does not take a second slot",
      watch._first_seen(["a", "b", "a", "c", "b"]) == ["a", "b", "c"])
check("the FIRST occurrence keeps its place",
      watch._first_seen(["new", "old", "new"]) == ["new", "old"],
      "newest first is the order the caller relies on")
check("empty ids are dropped", watch._first_seen(["a", "", None, "b"])
      == ["a", "b"])
check("an empty list stays empty", watch._first_seen([]) == [])

print("\n-- and the cap counts DISTINCT ids now " + "-" * 30)

everything = ["id%04d" % n for n in range(watch.KNOWN_CAP + 200)]
kept = watch._first_seen(everything)
check("it stops at the cap", len(kept) == watch.KNOWN_CAP, len(kept))
check("and keeps the newest end of the list", kept[0] == "id0000")

# The shape that used to eat the list: the same page, over and over.
page = ["id%03d" % n for n in range(watch.PEEK)]
known = []
for _ in range(40):
    known = watch._first_seen(page + known)
check("forty checks that found nothing new remember %d ids, not %d repeats"
      % (watch.PEEK, watch.KNOWN_CAP),
      len(known) == watch.PEEK, len(known))

# Now something genuinely old must survive a busy channel.
old = ["ancient"]
known = watch._first_seen(page + old)
for _ in range(40):
    known = watch._first_seen(page + known)
check("and something old is still remembered afterwards", "ancient" in known,
      "before the fix it was pushed off by copies of the same page")

print("\n-- the margin this used to depend on " + "-" * 32)

check("a look never returns more than the cap",
      watch.PEEK <= watch.KNOWN_CAP and 15 <= watch.KNOWN_CAP,
      "PEEK=%d, feed=15, cap=%d" % (watch.PEEK, watch.KNOWN_CAP))

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
