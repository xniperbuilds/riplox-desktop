"""
Run Riplox's tests.

    python tests/run.py            everything, including the ones that go online
    python tests/run.py --offline  only the ones that need no network
    python tests/run.py cover door just the files whose names contain these

Each test file is a script that prints its own checks and exits non-zero if
any failed. That is deliberate: they are readable on their own, they need no
test framework installed, and any one of them can be run by hand while the
thing it covers is being worked on.

    python tests/test_youtube_door.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Windows hands a piped child process the ANSI codepage, and several of these
# files print a ⭐ or a ⚠ in a heading. Run by hand in a terminal they are
# fine; collected by this runner they died on the first emoji with a
# UnicodeEncodeError, and every one of them was reported as a failing test
# while its own checks had all passed. Set once here rather than editing the
# headings, because the next test written will have the same problem.
CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

# The ones that talk to a real site. They are the most valuable tests here -
# a door that passes against a recorded answer proves nothing about the day
# the site changes - but they need a connection, so they can be left out.
ONLINE = {
    "test_youtube_door.py",
    "test_youtube_door_engine.py",
    "test_proxy.py",
    "test_cover.py",
    "test_door_facebook.py",
}

# Not "online" but "needs something started first". Left out of both runs by
# default and named when it is, because a test that cannot pass unless you
# already knew to start a server is a failure nobody can act on - and one
# permanent red line teaches everybody to ignore the summary.
NEEDS_A_SERVICE = {
    "test_relay_socket.py": "cd relay && npx wrangler dev --port 8799 --local",
}


def main() -> int:
    picks = [a for a in sys.argv[1:] if not a.startswith("-")]
    offline = "--offline" in sys.argv

    # .mjs as well as .py: the relay is JavaScript, and a test that only runs
    # when somebody remembers to type "node tests/..." is a test that does not
    # exist. Its runner is chosen from the suffix a few lines below.
    files = sorted(list(HERE.glob("test_*.py")) + list(HERE.glob("test_*.mjs")))
    held = [p for p in files if p.name in NEEDS_A_SERVICE and not picks]
    files = [p for p in files if p not in held]
    if offline:
        files = [p for p in files if p.name not in ONLINE]
    if picks:
        files = [p for p in files if any(word in p.name for word in picks)]

    if not files:
        print("no tests matched")
        return 1

    width = max(len(p.name) for p in files)
    failed = []
    began = time.monotonic()

    for path in files:
        started = time.monotonic()
        runner = "node" if path.suffix == ".mjs" else sys.executable
        done = subprocess.run([runner, str(path)],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              env=CHILD_ENV)
        took = time.monotonic() - started

        # The last line each file prints is its own tally.
        tail = [ln for ln in (done.stdout or "").splitlines() if ln.strip()]
        summary = tail[-1] if tail else "(no output)"
        mark = "ok  " if done.returncode == 0 else "FAIL"
        print(f"{mark}  {path.name:<{width}}  {summary:<24} {took:5.1f}s")

        if done.returncode != 0:
            failed.append(path.name)
            for line in tail:
                if line.strip().startswith("FAIL"):
                    print(f"        {line.strip()}")
            if done.stderr.strip():
                print(f"        {done.stderr.strip().splitlines()[-1]}")

    print(f"\n{len(files) - len(failed)}/{len(files)} files passed "
          f"in {time.monotonic() - began:.0f}s"
          + (f" - failed: {', '.join(failed)}" if failed else ""))

    # Said out loud rather than left out quietly. A run that skipped something
    # and did not mention it reads as "everything was covered".
    for path in held:
        print(f"held  {path.name} - start it first:  {NEEDS_A_SERVICE[path.name]}")
    if offline:
        left = sorted(ONLINE - {p.name for p in files})
        if left:
            print(f"held  {len(left)} online tests not run: {', '.join(left)}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
