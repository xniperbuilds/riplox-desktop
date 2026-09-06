"""The site-health panel, and what it is allowed to blame a site for.

The panel is headed "How sites are behaving" and offers to tell somebody
"whether it is the site or you". It answered that wrongly: every failure that
was not a cancel went down as the SITE being down, including faults that never
involved the site at all.

Measured on a real install: Github sat there marked down because a Generic
download had built a filename Windows would not open, and Xniperbuilds was
marked down for "This site is not supported" - which is Riplox declining, not
a site failing.

    python tests/test_health_blames_the_right_thing.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-health-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          (" | " + str(detail)) if detail else ""))


print("-- faults that belong to this PC, not to a site " + "-" * 21)

MINE = [
    ("a filename Windows refused",
     "unable to open for writing: [Errno 22] Invalid argument: "
     "'C:\\Users\\x\\Videos\\Riplox\\Generic\\9b7c006a-068c-484e-b5b1.mp4'"),
    ("a full disk", "OSError: [Errno 28] No space left on device"),
    ("a locked file", "The process cannot access the file because it is being "
                      "used by another process"),
    ("no permission", "PermissionError: Access is denied"),
    ("ffmpeg missing", "ERROR: Postprocessing: ffmpeg not found"),
    ("Riplox declining the site", "This site is not supported."),
]
for label, text in MINE:
    check("%s is not the site's fault" % label, engine._is_local_trouble(text),
          text[:52])

print("\n-- and faults that really are about the site " + "-" * 24)

THEIRS = [
    ("a bot check", "ERROR: [youtube] abc: Sign in to confirm you're not a bot"),
    ("a removed video", "ERROR: [youtube] abc: Video unavailable"),
    ("a refusal", "ERROR: unable to download webpage: HTTP Error 403: Forbidden"),
    ("a login wall", "ERROR: [Instagram] xyz: login_required"),
]
for label, text in THEIRS:
    check("%s still counts against the site" % label,
          not engine._is_local_trouble(text), text[:52])

print("\n-- the reason is kept long enough to be readable " + "-" * 20)

long_reason = ("unable to open for writing: [Errno 22] Invalid argument: "
               "'C:\\Users\\somebody\\Videos\\Riplox\\Generic\\"
               "9b7c006a-068c-484e-b5b1-1efac427670a "
               "[9b7c006a-068c-484e-b5b1-1efac427670a] NAp Riplox.mp4'")
engine.note_health("https://www.youtube.com/watch?v=x", engine.HEALTH_DOWN,
                   long_reason)
rows = [r for r in engine.health() if r.get("site") == "YouTube"]
check("the row was recorded", bool(rows), len(rows))
if rows:
    kept = rows[0].get("why", "")
    check("the filename is not cut off mid-path",
          kept.endswith(".mp4'"), "kept %d of %d chars"
          % (len(kept), len(long_reason)))

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
