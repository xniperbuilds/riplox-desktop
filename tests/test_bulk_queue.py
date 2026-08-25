"""
Pause all / Resume all / Retry all, at the manager.

The point of the split is that each button only touches what belongs to it:
Retry all must not restart something the user paused on purpose, and Resume
all must not revive something that failed.
"""

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-bulk-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


def bare_manager():
    """
    A DownloadManager with no worker threads at all.

    ⚠️ This is the whole reason this file was flaky. DownloadManager() starts
    real workers in __init__, and every job below is pushed straight into
    _jobs as "queued" - exactly what a worker is hunting for. Whether the
    assertion or the worker got there first depended on how busy the machine
    was, so it passed alone and failed inside the full suite. A worker that won
    would also have gone off and downloaded https://example.com/queued for real.

    ⚠️ Setting _running = False afterwards does NOT fix it, and that was
    measured rather than assumed: a worker already past the `while self._running`
    check still takes one more job, and _wake.set() actively hurries it along.
    Measured 12 of 12 either way. The only reliable answer is to never start
    them - so build the object and fill in the four fields the bulk methods
    touch (_lock, _jobs, _order, _wake) plus _running for anything that reads it.

    pause_all / retry_all / resume_all need no worker: they are state changes
    under that same lock.
    """
    man = engine.DownloadManager.__new__(engine.DownloadManager)
    man._jobs = {}
    man._order = []
    man._lock = threading.Lock()
    man._wake = threading.Event()
    man._workers = []
    man._running = False
    return man


def loaded():
    """One job in each state that matters."""
    man = bare_manager()
    states = ["queued", "downloading", "paused", "error", "cancelled", "done"]
    made = {}
    for state in states:
        job = engine.Job(url=f"https://example.com/{state}")
        job.status = state
        job.paused = state == "paused"
        job.cancelled = state in ("paused", "cancelled")
        with man._lock:
            man._jobs[job.id] = job
            man._order.append(job.id)
        made[state] = job
    return man, made


def states(made):
    return {name: job.status for name, job in made.items()}


print("\n-- Pause all takes the running ones, and only those ----------------")
man, made = loaded()
stopped = man.pause_all()
check("it paused the two live ones", stopped == 2, str(stopped))
check("queued is now paused", made["queued"].status == "paused")
check("downloading is now paused", made["downloading"].status == "paused")
check("a finished download is untouched", made["done"].status == "done")
check("a failed one is untouched", made["error"].status == "error")
check("a cancelled one is untouched", made["cancelled"].status == "cancelled")
check("part-files are kept (paused, not cancelled)",
      made["downloading"].paused is True)

print("\n-- Retry all takes the failures, and leaves paused alone -----------")
man, made = loaded()
retried = man.retry_all()
check("it queued the failed and cancelled ones", retried == 2, str(retried))
check("the failed one is queued again", made["error"].status == "queued")
check("the cancelled one is queued again", made["cancelled"].status == "queued")
check("⭐ a deliberately paused download is NOT restarted",
      made["paused"].status == "paused")
check("a finished download is not queued again", made["done"].status == "done")
check("the error text was cleared", made["error"].error == "")

print("\n-- Resume all takes the paused ones only ---------------------------")
man, made = loaded()
resumed = man.resume_all()
check("it resumed the paused one", resumed == 1, str(resumed))
check("paused is queued again", made["paused"].status == "queued")
check("a failed one is not resumed", made["error"].status == "error")

print("\n-- pressing them on an empty queue is harmless ---------------------")
empty = bare_manager()           # same reason as loaded(), above
check("pause all on nothing", empty.pause_all() == 0)
check("retry all on nothing", empty.retry_all() == 0)
check("resume all on nothing", empty.resume_all() == 0)

print("\n-- pressing twice does not double-count ----------------------------")
man, made = loaded()
first = man.retry_all()
second = man.retry_all()
check("the second press finds nothing left", first == 2 and second == 0,
      f"{first} then {second}")

print("\n-- pause all then resume all is a round trip -----------------------")
man, made = loaded()
man.pause_all()
man.resume_all()
check("the two live ones are queued again",
      made["queued"].status == "queued" and made["downloading"].status == "queued",
      str(states(made)))
check("...and the one that was already paused came back too",
      made["paused"].status == "queued")

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
