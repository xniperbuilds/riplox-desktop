"""
Holding a failed link must not become an endless loop.

A message that cannot be handled is kept and redelivered - that is the fix.
But one that can NEVER be handled would then come back forever, silently,
which trades a silent loss for a silent loop. This checks it stops, and says
so when it does.

Updated 2026-08-15 after an audit. The rule used to be "three failures and it
goes", and this file locked that in - but the poll loop does not wait between
successful polls, so those three happened back to back: a link that failed
three times and would have worked on the fourth was given up on in 31
milliseconds. There is now a time condition as well, and the cases below check
BOTH halves - a transient failure keeps the link, a permanent one still lets
it go once the window has actually passed.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-giveup-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import sharing                                              # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


def deliver(nonce, blows_up):
    """
    The real sharing.deliver(), with only the app's own handling stubbed.

    Deliberately not a copy of its logic: a test that restates the code it is
    testing passes on a defect. Only `handle` is replaced - the rule under
    test runs exactly as it ships.
    """
    real_handle = sharing.handle
    sharing.handle = (lambda n, c, via="": (_ for _ in ()).throw(RuntimeError("nope"))) \
        if blows_up else (lambda n, c, via="": None)
    try:
        settled = sharing.deliver([{"n": nonce, "c": "payload"}])
    finally:
        sharing.handle = real_handle
    record = sharing._failures.get(nonce)
    tries = record[0] if record else 0
    return settled, tries, nonce in settled and blows_up


def age(nonce, seconds):
    """Move a nonce's first-failure time back, instead of sleeping for it."""
    if nonce in sharing._failures:
        sharing._failures[nonce][1] -= seconds


print("\n-- a link that works is confirmed at once ------------------------")
sharing._failures.clear()
settled, tries, gave_up = deliver("aaaaaaaa1", blows_up=False)
check("confirmed on the first pass", settled == ["aaaaaaaa1"])
check("nothing is remembered against it", not sharing._failures)

print("\n-- a link that fails is kept, not confirmed ----------------------")
sharing._failures.clear()
settled, tries, gave_up = deliver("bbbbbbbb2", blows_up=True)
check("⭐ NOT confirmed, so the relay keeps it", settled == [], str(settled))
check("the failure is counted", tries == 1, str(tries))

settled, tries, gave_up = deliver("bbbbbbbb2", blows_up=True)
check("still kept on the second try", settled == [] and tries == 2, str(tries))

print("\n-- ⭐ three quick failures do NOT lose it ------------------------")
settled, tries, gave_up = deliver("bbbbbbbb2", blows_up=True)
check("the third failure in the same instant still keeps it",
      settled == [] and tries == 3,
      "back-to-back polls are not three chances, they are one")
settled, tries, gave_up = deliver("bbbbbbbb2", blows_up=True)
check("and so does the fourth", settled == [], f"tries={tries}")

print("\n-- but once the window has passed, it stops circling -------------")
age("bbbbbbbb2", sharing.GIVE_UP_NOT_BEFORE + 1)
settled, tries, gave_up = deliver("bbbbbbbb2", blows_up=True)
check("⭐ it is let go", gave_up is True and settled == ["bbbbbbbb2"],
      f"after {sharing.GIVE_UP_NOT_BEFORE}s and {sharing.GIVE_UP_AFTER}+ tries")
check("the count is cleared with it", "bbbbbbbb2" not in sharing._failures)

print("\n-- and the reason is written down, not swallowed -----------------")
log = sharing.load().get("log") or []
gave = [e for e in log if "gave up" in (e.get("why") or "")]
tried = [e for e in log if "could not be handled" in (e.get("why") or "")]
check("the earlier tries are in the log", len(tried) >= 2, str(len(tried)))
check("⭐ giving up is in the log too", len(gave) == 1, str(len(gave)))
check("...and it says how many tries and over how long",
      bool(gave) and "tries over" in gave[0]["why"],
      gave[0]["why"][:70] if gave else "")

print("\n-- one bad link does not take a good one with it -----------------")
sharing._failures.clear()
deliver("cccccccc3", blows_up=True)
settled, _, _ = deliver("dddddddd4", blows_up=False)
check("the good one is confirmed", settled == ["dddddddd4"])
held = sharing._failures.get("cccccccc3")
check("the bad one is still being held", bool(held) and held[0] == 1, str(held))

print("\n-- a restart forgets the count, which is the point ---------------")
sharing._failures.clear()
check("nothing carried over a restart", not sharing._failures,
      "whatever was wrong may well be fixed by then")

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
