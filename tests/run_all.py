"""Run every test file, and refuse to lose one quietly.

The counting this replaces read each file's "N passed, M failed" line and added
it up. A file that crashed printed no such line, so it contributed nothing to
either column - and the total simply got smaller. That is exactly how a tilde
added to one string took twenty-five tests out of the suite while the run still
said "0 failed".

So a file that does not report is a failure here, not a gap. The only exception
is a file that says plainly it needs something absent - the relay tests want a
local wrangler - and those are listed as skipped rather than counted as passing.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUMMARY = re.compile(r"(\d+) passed, (\d+) failed")
# A file may end with this instead of a count; both are a real report.
ALL_PASS = "ALL PASS"
# Said by a test that cannot run without something this machine does not have.
UNAVAILABLE = ("not reachable", "start wrangler", "no device", "not installed")

passed = failed = 0
broken, skipped, quiet = [], [], []

for path in sorted(HERE.glob("test_*.py")):
    run = subprocess.run([sys.executable, str(path)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    text = run.stdout + run.stderr

    found = SUMMARY.search(text)
    if found:
        passed += int(found.group(1))
        got = int(found.group(2))
        failed += got
        if got:
            broken.append("%s (%d failed)" % (path.name, got))
        continue

    if ALL_PASS in text:
        passed += text.count("  ok ") + text.count("  PASS ")
        continue

    if any(word in text for word in UNAVAILABLE):
        skipped.append(path.name)
        continue

    # No count, no all-pass, no stated reason: it stopped early. The last line
    # is usually the exception that did it.
    tail = [line for line in text.strip().splitlines() if line.strip()][-1:]
    quiet.append("%s -> %s" % (path.name, tail[0].strip() if tail else "no output"))

print("=" * 70)
print("  %d passed, %d failed" % (passed, failed))
for item in broken:
    print("    FAILED  " + item)
for item in skipped:
    print("    skipped " + item + " (needs something not running here)")
for item in quiet:
    print("    BROKE   " + item)
print("=" * 70)

sys.exit(1 if (failed or quiet) else 0)
