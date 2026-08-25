"""An engine that stops saying anything gets stopped.

Found on a real machine: one TikTok video yt-dlp cannot extract left FOUR
yt-dlp processes spinning at a full core each - one for five hours - while the
row sat on "downloading" for ever. Other videos finished, so nothing looked
broken; the PC was just slow and that one never arrived.

⚠️ The dangerous half of this feature is the false positive. Killing a healthy
download throws away everything it had, so most of this file is about the times
it must NOT fire: while paused, while cancelled, while the merge is talking on
stderr, and while a very slow download is still printing progress.

⚠️ LOCALAPPDATA is redirected before engine is imported.
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-watchdog-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine                                               # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:88]) if detail else ""))


src = (ROOT / "src" / "engine.py").read_text(encoding="utf-8")


print("\n-- the shape of it --------------------------------------------------")

check("there is a silence limit at all", "_SILENCE_LIMIT" in src)
check("⭐ it is generous - minutes, not seconds",
      engine._SILENCE_LIMIT >= 600, "%ds" % engine._SILENCE_LIMIT)
check("a job can remember when it was last heard from",
      "heard" in engine.Job.__slots__ and "went_quiet" in engine.Job.__slots__)

j = engine.Job(url="https://example.com/x")
check("and starts out not having given up", j.went_quiet is False)

# Both pipes must feed it, or a long merge - which only talks on stderr -
# would look like silence and get killed halfway through writing the file.
run_fn = src.split("def _run_engine(", 1)[1]
stdout_feeds = "for line in proc.stdout:\n            job.heard" in run_fn
stderr_feeds = "for raw in proc.stderr:\n                job.heard" in run_fn
check("⭐ stdout keeps it alive", stdout_feeds)
check("⭐ stderr keeps it alive too - the merge only talks there", stderr_feeds)

check("the watchdog kills the whole tree, not just the one process",
      "_kill_tree(proc)" in run_fn.split("def watchdog", 1)[1][:700])
check("...and it is a daemon, so it can never hold the app open",
      "daemon=True" in run_fn.split("def watchdog", 1)[1][:600])


print("\n-- when it must NOT fire --------------------------------------------")

body = run_fn.split("def watchdog", 1)[1][:700]
check("⭐ a PAUSED job is left alone - it is silent on purpose",
      "job.paused" in body)
check("⭐ a CANCELLED job is left alone", "job.cancelled" in body)
check("...and both refresh the clock rather than just skipping, so the "
      "silence does not accumulate while paused",
      "job.heard = time.monotonic()" in body)

# The real guard against false positives: it waits, it does not sample once.
check("it re-checks on a timer instead of deciding once",
      "while proc.poll() is None" in body and "time.sleep" in body)


print("\n-- what the user is told --------------------------------------------")

tail = run_fn.split("if job.went_quiet:", 1)
check("the outcome is an error, not a silent cancel", len(tail) > 1)
if len(tail) > 1:
    after = tail[1][:900]
    check('⭐ it does not look like the user pressed Cancel',
          'job.status = "error"' in after)
    check("the message says what happened in plain words",
          "stopped responding" in after)
    check("...and says how long it waited, from the constant itself",
          "_SILENCE_LIMIT" in after)
    check("...and suggests something the user can actually do",
          "Update engine" in after)
    check("⭐ it does NOT go round the retry ladder again",
          "return True" in after)


print("\n-- it actually fires, on a real process ------------------------------")

# A real child that goes quiet, with the limit turned down so the test is quick.
real_limit = engine._SILENCE_LIMIT
try:
    engine._SILENCE_LIMIT = 3.0
    quiet = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time\n"
         "sys.stdout.write('[download]   1.0% of 10.00MiB\\n'); sys.stdout.flush()\n"
         "time.sleep(120)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    job = engine.Job(url="https://example.com/quiet")
    job.heard = time.monotonic()
    job.went_quiet = False

    def watchdog():
        while quiet.poll() is None:
            time.sleep(0.25)
            if job.cancelled or job.paused:
                job.heard = time.monotonic()
                continue
            if time.monotonic() - job.heard < engine._SILENCE_LIMIT:
                continue
            job.went_quiet = True
            engine._kill_tree(quiet)
            return

    threading.Thread(target=watchdog, daemon=True).start()
    began = time.monotonic()
    quiet.wait(timeout=40)
    took = time.monotonic() - began

    check("⭐ a process that goes quiet is stopped", job.went_quiet is True)
    check("...promptly, once the limit passes",
          took < 20, "%.1fs" % took)
    check("...and it really is dead", quiet.poll() is not None)

    # And the opposite: one that keeps talking is left alone.
    chatty = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time\n"
         "for i in range(60):\n"
         "    sys.stdout.write('[download] %d%%\\n' % i); sys.stdout.flush()\n"
         "    time.sleep(0.3)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    job2 = engine.Job(url="https://example.com/chatty")
    job2.heard = time.monotonic()
    job2.went_quiet = False

    def feed():
        for _ in chatty.stdout:
            job2.heard = time.monotonic()

    threading.Thread(target=feed, daemon=True).start()

    def watchdog2():
        while chatty.poll() is None:
            time.sleep(0.25)
            if time.monotonic() - job2.heard < engine._SILENCE_LIMIT:
                continue
            job2.went_quiet = True
            engine._kill_tree(chatty)
            return

    threading.Thread(target=watchdog2, daemon=True).start()
    time.sleep(8)
    check("⭐⭐ a slow but talking download is NOT killed",
          job2.went_quiet is False and chatty.poll() is None)
    engine._kill_tree(chatty)
finally:
    engine._SILENCE_LIMIT = real_limit

check("the limit was put back", engine._SILENCE_LIMIT == real_limit)


print("")
print(str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("  FAILED: " + name)
import shutil                                               # noqa: E402
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
