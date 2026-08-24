"""Best available stops losing resolution to codec, and a re-upload rung appears.

Measured on YouTube before this changed: "Best available" returned **1080p
h264** on a video that had **2160p** sitting there in AV1 and VP9. h264 was a
FILTER, and on YouTube h264 stops at 1080p - so asking for the best threw the
4K away. It is a tie-break in the sort now: h264 still wins wherever h264 can
reach the same height, and nothing is lost where it cannot.

"max" is the separate promise - the fattest stream, chosen to survive being
uploaded again rather than to play everywhere.

⚠️ Two traps this file exists to keep shut:

  * every quality that is not "best" or "max" is a NUMBER that goes straight
    into [height<=?N]. A name added without the guard builds "[height<=?max]"
    and breaks the download outright.

  * --download-archive remembers video IDS, not files. Without a carve-out, a
    video already saved at 1080p is silently skipped when asked for again at
    the highest quality - the user gets nothing, with no reason shown.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-quality-test-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:88]) if detail else ""))


def args_for(quality, **settings):
    settings.setdefault("prefer_h264", True)
    return engine.format_args(quality, settings)


def sort_of(args):
    return args[args.index("-S") + 1] if "-S" in args else ""


def selector_of(args):
    return args[args.index("-f") + 1] if "-f" in args else ""


print("\n-- what each quality asks the engine for ---------------------------")
check("⭐ best no longer filters for h264 - it would cost the resolution",
      "vcodec~=" not in selector_of(args_for("best")),
      selector_of(args_for("best"))[:70])
check("...it prefers h264 in the sort instead",
      sort_of(args_for("best")) == "res,vcodec:h264,acodec:aac",
      sort_of(args_for("best")))
check("turning compatibility off leaves plain highest-first",
      sort_of(args_for("best", prefer_h264=False)) == "res",
      sort_of(args_for("best", prefer_h264=False)))
check("⭐ max takes the fattest stream, for re-uploading",
      sort_of(args_for("max")) == "res,vbr,abr", sort_of(args_for("max")))
check("...and asks for no particular codec",
      "vcodec~=" not in selector_of(args_for("max")))


print("\n-- the height cap belongs only to numbers ---------------------------")
for quality in ("best", "max"):
    check("%-5s carries no height cap" % quality,
          "height<=?" not in selector_of(args_for(quality)),
          selector_of(args_for(quality))[:60])
check("⭐ nothing ever builds [height<=?max]",
      "height<=?max" not in selector_of(args_for("max")))
for quality in ("2160", "1080", "720", "480"):
    check("%-5s still caps at %sp" % (quality, quality),
          ("height<=?" + quality) in selector_of(args_for(quality)))
check("1080 still comes back as h264 where h264 reaches it",
      sort_of(args_for("1080")) == "res,vcodec:h264,acodec:aac")

print("\n-- mp3 is untouched -------------------------------------------------")
mp3 = args_for("mp3")
check("mp3 asks for audio only", "bestaudio" in selector_of(mp3), selector_of(mp3))
check("...and never gets a video sort", "-S" not in mp3)


print("\n-- the archive must not swallow a higher quality --------------------")
keep = engine.extra_args({"skip_existing": True}, "best")
drop = engine.extra_args({"skip_existing": True}, "max")
check("best still skips what you already have",
      "--download-archive" in keep)
check("⭐ max ignores it - a bigger file is not the copy you already saved",
      "--download-archive" not in drop)
check("with the setting off, neither uses it",
      "--download-archive" not in engine.extra_args({}, "best")
      and "--download-archive" not in engine.extra_args({}, "max"))
check("trimming keeps its own carve-out",
      "--download-archive" not in engine.extra_args({"skip_existing": True},
                                                    "best", trimmed=True))


print("\n-- it is offered, and it is named honestly --------------------------")
check("max has a label", "max" in engine.QUALITY_LABELS)
label = engine.QUALITY_LABELS.get("max", "")
check("⭐ the label does not call it 'best' - it is not better for watching",
      "best" not in label.lower(), label)
check("...it says what it is for", "re-upload" in label.lower(), label)

rungs = engine._available_qualities(
    {"formats": [{"height": 1080}, {"height": 2160}]}, {})["rungs"]
check("max is offered beside best", "max" in rungs and "best" in rungs, rungs)
check("...and mp3 is still last", rungs[-1] == "mp3", rungs)

shutil.rmtree(SANDBOX, ignore_errors=True)

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
