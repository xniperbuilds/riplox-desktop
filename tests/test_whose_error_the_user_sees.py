"""When both routes fail, whose sentence reaches the user?

_second_door keeps the engine's error, tries its own way in, and on failure
hands both to _door_verdict. That used to return the DOOR's sentence in every
case but one narrow Instagram shape - so whatever the engine said was replaced.

Two things ride on that sentence, and both were found by running the
situations rather than by reading the code:

  1. The health panel decides "the site or you" by reading job.error. After
     the door had overwritten it, a filename Windows will not open read as a
     site refusal and the SITE was marked down for it. That is exactly the
     fault F-11 fixed on the engine's own path - the door left a second way
     to reach it.

  2. The give-up guard stops a download that is getting nowhere and names the
     setting to change. If the door then failed too, that advice was thrown
     away and the user was told the video was unavailable instead.

    python tests/test_whose_error_the_user_sees.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-verdict-"))
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


DOOR = "YouTube says: this video is unavailable."
LOCAL = ("ERROR: unable to open for writing: [Errno 22] Invalid argument: "
         r"'C:\Users\x\Downloads\a|b?.mp4'")
FULL = "ERROR: [Errno 28] No space left on device"
STALL = ("Stopped after fetching about 70.0 MB three times "
         + engine._RIPLOX_GAVE_UP + ". Retry to try again, or lower "
         "\u201cPieces per file\u201d in Settings.")
REFUSED = "ERROR: Sign in to confirm you're not a bot"

print("-- a fault on THIS PC is not explained by the site " + "-" * 18)

for name, said in (("a filename Windows will not open", LOCAL),
                   ("a full disk", FULL)):
    kept = engine._door_verdict(said, DOOR)
    check("%-34s is kept" % name, kept == said, kept[:46])
    check("%-34s still reads as local" % name,
          engine._is_local_trouble(kept), True)

print("\n-- and neither is Riplox stopping the job itself " + "-" * 20)

kept = engine._door_verdict(STALL, DOOR)
check("the give-up sentence survives", kept == STALL, kept[:52])
check("so the advice reaches the person it was written for",
      "Pieces per file" in kept)
check("the phrase is one constant, not two copies",
      engine._RIPLOX_GAVE_UP in STALL and engine._RIPLOX_GAVE_UP in kept,
      "reworded in one place only and the verdict stops recognising it")

print("\n-- the door still speaks for everything it is better at " + "-" * 13)

check("a site refusal hands over to the door",
      engine._door_verdict(REFUSED, DOOR) == DOOR)
check("so does an ordinary engine failure",
      engine._door_verdict("ERROR: unable to extract player response", DOOR)
      == DOOR)
check("and an empty engine error", engine._door_verdict("", DOOR) == DOOR)

print("\n-- what the health panel is left with " + "-" * 31)


def marked(engine_error):
    """Whether the SITE gets a black mark, the way _run_job decides it."""
    engine._health.clear()
    # The dict is a cache over a file; clearing one without the other reloads
    # the previous case's mark and reads as a failure that never happened.
    engine._health_file().unlink(missing_ok=True)
    said = engine._door_verdict(engine_error, DOOR)
    if not engine._is_local_trouble(said):
        engine.note_health("https://www.youtube.com/watch?v=x",
                           engine.HEALTH_DOWN)
    return {r["site"]: r["state"] for r in engine.health()}


check("a filename this PC will not open marks nothing", marked(LOCAL) == {},
      marked(LOCAL))
check("a full disk marks nothing", marked(FULL) == {}, marked(FULL))
check("a real site refusal DOES mark the site",
      marked(REFUSED) == {"YouTube": engine.HEALTH_DOWN},
      "the panel is still useful - it just stopped blaming the wrong thing")

print("\n-- and audio has no height to be short of " + "-" * 27)

# Reachable only if a door ever reported a height beside an audio pick. It
# does not today - an mp3 picks an audio stream and those carry no height - so
# this is the guard standing one layer earlier than the bug could appear.
check("mp3 is never called short",
      engine.quality_shortfall("mp3", 360, 2160) == "")
check("even at a height that would be short for a video",
      engine._quality_suspect("mp3", 360) is False)
check("while max at 360 still is",
      engine._quality_suspect("max", 360) is True)

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
