"""The window when the engine does not answer, and before it destroys anything.

Two faults this holds shut, both found by reading rather than by anything
going red:

  * api() put its only .catch on r.json(), so a REJECTED fetch - a busy,
    restarting or stopped engine - had no handler at all. 104 call sites, 91
    of which already check res.ok, and none of those branches could run
    because res never arrived.
  * pollJobs rescheduled itself as the last statement of its success path,
    with an early return above it and a catch beside it. One failed poll and
    the queue's heartbeat was dead until the user changed view - the exact
    symptom the comment above it says was fixed: "a link arriving from a phone
    sat there unseen until you switched tabs".

And one thing that had no guard at all: the two buttons that destroy the most
were the two that did not ask, while removing a single sign-in did.

Read as text on purpose. These are structural promises about a file the Python
tests cannot execute, and a structural check is what catches them coming back.

    python tests/test_window_survives_a_dead_engine.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JS = (HERE.parent / "src" / "static" / "js" / "app.js").read_text(
    encoding="utf-8", errors="replace")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


def body(name):
    """The source of one top-level function, by brace counting."""
    start = JS.index("function %s(" % name)
    depth, i = 0, JS.index("{", start)
    for j in range(i, len(JS)):
        depth += (JS[j] == "{") - (JS[j] == "}")
        if depth == 0:
            return JS[start:j + 1]
    raise AssertionError("unterminated " + name)


print("-- a request that never comes back " + "-" * 34)

api = body("api")
check("api() answers a rejected fetch instead of rejecting",
      "lost contact with its engine" in api)
# The two-argument then is what separates a rejected FETCH from an error
# thrown inside the success handler; a trailing .catch would swallow both.
check("it uses then(onOk, onFail), not a blanket catch",
      re.search(r"\.then\(\s*\n?\s*function\s*\(r\)", api) is not None
      and api.count("function ()") >= 1)
check("a reply that is not JSON is still handled", "Bad response." in api)

print("\n-- the heartbeat " + "-" * 52)

poll = body("pollJobs")
sched = poll.index("setTimeout(pollJobs")
guard = poll.index(".catch(")
check("pollJobs reschedules AFTER the catch, not inside the success path",
      sched > guard,
      "reschedule at %d, catch at %d" % (sched, guard))
check("the early return cannot skip the reschedule",
      poll.index("if (!res.ok) return") < guard)
check("only one place reschedules it", poll.count("setTimeout(pollJobs") == 1)

print("\n-- what may not be destroyed without being asked " + "-" * 21)

CALLS = [("the library", 'api("/api/history/clear"'),
         ("every sign-in", 'api("/api/cookies/forget", {})'),
         ("one site's sign-in", 'api("/api/cookies/forget", { site: site })')]
for label, call in CALLS:
    where = JS.index(call)
    before = JS[max(0, where - 800):where]
    check("clearing %s asks first" % label, "ask(" in before,
          "no ask() in the 800 characters before it")

# The singular toast on an action that deletes all of them was its own tell.
# ⚠️ Comment lines are stripped first: this file explains the old wording, and
# checking the raw text made the fix look like the bug.
code = "\n".join(l for l in JS.splitlines()
                 if not l.lstrip().startswith(("//", "*", "/*")))
check("the all-sign-ins toast is not written in the singular",
      '"Sign-in deleted"' not in code)

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
