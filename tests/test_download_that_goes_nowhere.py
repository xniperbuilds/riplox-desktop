"""A download that is busy and getting nowhere.

_SILENCE_LIMIT catches an engine that has stopped saying anything. It cannot
catch the opposite, and the opposite is what an interrupted 4K download does:

Measured while auditing this - an attempt fetched 70 MB and committed 8 of
them. With sixteen fragments running at once the rest sit in .part-Frag files,
and the next attempt deletes them and fetches them again. The bar moves the
whole time, so nothing is ever silent, and one job can spend all nine of its
attempts re-fetching the same bytes. Reported as a download that "kept going
and never finished".

So an attempt that peaks no higher than the best any attempt reached is an
attempt that got nowhere. Two in a row and the job stops with a sentence that
says what happened and what to change.

    python tests/test_download_that_goes_nowhere.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-nowhere-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []
MB = 1024 * 1024


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


class NoWait(object):
    """The pause between attempts is real seconds; this test counts attempts.

    Only the clock is faked. Every branch under test still runs, and the waits
    still happen - they just take no time, so nine attempts cost nothing.
    """

    def __init__(self, real):
        self._real = real
        self._now = 0.0

    def monotonic(self):
        self._now += 1.0
        return self._now

    def sleep(self, seconds):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


import time as _real_time                                    # noqa: E402
engine.time = NoWait(_real_time)


class FakeManager(engine.DownloadManager):
    """Runs the real _run_engine, with the attempts replaced by a script."""

    def __init__(self, peaks):
        self.peaks = list(peaks)
        self.tried = 0
        self._jobs, self._order = {}, []
        self._lock = engine.threading.RLock()
        self._wake = engine.threading.Event()

    def _save(self):
        pass

    def _attempt(self, job, settings, client, with_cookies=True):
        job.attempt_peak = (self.peaks[self.tried]
                            if self.tried < len(self.peaks) else 0.0)
        self.tried += 1
        job.log = "ERROR: unable to download webpage: read timed out"
        job.status = "downloading"
        return False

    def _network_went(self, job):
        return False

    def _signed_out_retry(self, job, settings):
        return False

    def _second_door(self, job, settings):
        return None


def run(peaks):
    job = engine.Job("https://www.youtube.com/watch?v=x", quality="max")
    man = FakeManager(peaks)
    man._jobs[job.id] = job
    man._order.append(job.id)
    man._run_engine(job, dict(engine.DEFAULT_SETTINGS))
    return job, man


print("-- the same bytes, over and over " + "-" * 36)

job, man = run([70 * MB] * 9)
check("it stops instead of spending every attempt", man.tried < 9, man.tried)
check("three attempts, not nine", man.tried == 3, man.tried)
check("and it says so", "without getting any further" in (job.error or ""),
      (job.error or "")[:64])
check("the message names how much was fetched",
      "70" in (job.error or ""), (job.error or "")[:40])
check("and offers the setting that changes it",
      "Pieces per file" in (job.error or ""))

print("\n-- a retry that genuinely resumes is not stopped " + "-" * 20)

job, man = run([20 * MB, 45 * MB, 70 * MB, 95 * MB, 120 * MB,
                150 * MB, 180 * MB, 210 * MB, 240 * MB])
check("every attempt is allowed", man.tried == 9, man.tried)
check("no stall was recorded", job.stalled == 0, job.stalled)
check("and nothing was blamed on it", "without getting any further"
      not in (job.error or ""))

print("\n-- and one that crawls forward is given its chances " + "-" * 17)

# Just over the floor each time: slow, but genuinely further on.
job, man = run([(10 + 5 * n) * MB for n in range(9)])
check("small but real progress keeps going", man.tried == 9, man.tried)

print("\n-- one bad attempt between two good ones is forgiven " + "-" * 16)

# A flaky line: every other attempt gets nowhere, but the ones in between
# genuinely resume. The count has to be given BACK on progress, not merely
# stop rising - two stalls a download apart are not two in a row.
job, man = run([70 * MB, 70 * MB, 120 * MB, 120 * MB, 200 * MB,
                200 * MB, 300 * MB, 300 * MB, 400 * MB])
check("it is not stopped for stalling twice with progress between",
      man.tried == 9, man.tried)
check("the count was given back, not just held", job.stalled < 2, job.stalled)

print("\n-- an attempt that fetched nothing is not counted as a stall " + "-" * 8)

# 0 peak means it never got as far as downloading - a refusal, not a stall.
job, man = run([0, 0, 0, 0, 0, 0, 0, 0, 0])
check("zero-byte attempts do not trigger it", job.stalled == 0, job.stalled)
check("they are left to the rungs", "without getting any further"
      not in (job.error or ""))

print("\n-- and the peak is read off the real progress lines " + "-" * 16)

# The rest of this file scripts the peak. This part does not: it feeds the
# engine's own progress payloads through _apply_progress, which is the only
# thing that sets attempt_peak in a real download.
live = engine.Job("https://www.youtube.com/watch?v=x", quality="max")
man = FakeManager([])
for done in (5 * MB, 40 * MB, 70 * MB):
    man._apply_progress(live, "downloading|%d|%d|%d|1200000|30|4|32"
                        % (done, 200 * MB, 200 * MB))
check("it follows the bytes downloaded", live.attempt_peak == 70 * MB,
      live.attempt_peak)

# A restarted attempt reports small numbers again. The PEAK must not follow
# them down - that is the whole measurement.
man._apply_progress(live, "downloading|%d|%d|%d|1200000|30|1|32"
                    % (2 * MB, 200 * MB, 200 * MB))
check("and does not fall back when an attempt restarts",
      live.attempt_peak == 70 * MB, live.attempt_peak)

print("\n-- Retry gives back the goes this guard took " + "-" * 23)

job, man = run([70 * MB] * 9)
check("it had stopped", job.status == "error", job.status)
man.retry(job.id)
check("the stall count is given back", job.stalled == 0, job.stalled)
check("and so is the peak it was measured against", job.best_peak == 0.0,
      job.best_peak)
check("the job is queued again", job.status == "queued", job.status)

print("\n-- the constants " + "-" * 52)
check("the limit is two", engine._NO_PROGRESS_LIMIT == 2)
check("the floor is a few MB, not zero", 0 < engine._PROGRESS_FLOOR <= 16 * MB,
      engine._PROGRESS_FLOOR)

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
