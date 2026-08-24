"""What a finished download is called, and what the row says it is.

Two small things, both asked for after watching the app in use.

**The name.** Every file carries "Riplox" now. At the END of the name, not the
front: a folder of downloads still sorts by title, which is how anybody
actually looks for them - and the mark rides along when the file is shared or
uploaded again, which is the point of it.

**The label.** "Best available" is the app's word, not an answer. It never said
whether 4K or 720p came back, and that is the one thing somebody looking at a
finished row wants to know - so once the file exists the word is replaced by
the height it really came out at. Only for "best": "Highest" was chosen
deliberately and already says what it means, and a numbered rung is its own
answer.
"""
import ntpath
import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-name-test-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import engine                                                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name
          + ((" | " + str(detail)[:88]) if detail else ""))


class FakeJob:
    def __init__(self, quality="best", opts=None, start="", end=""):
        self.quality, self.opts, self.start, self.end = quality, opts or {}, start, end


MANAGER = engine.DownloadManager.__new__(engine.DownloadManager)
SETTINGS = {"download_dir": r"C:\Videos", "subfolder_per_site": False}


def name_for(**kw):
    return ntpath.basename(MANAGER._outtmpl(SETTINGS, FakeJob(**kw)))


print("\n-- every download carries the name --------------------------------")
for quality in ("best", "max", "2160", "1080", "mp3"):
    got = name_for(quality=quality)
    check("%-5s -> %s" % (quality, got), " Riplox." in got, got)

check("⭐ it sits at the END, so a folder still sorts by title",
      name_for().index("Riplox") > name_for().index("%(title)"))
check("...and before the extension, not after it",
      name_for().endswith(" Riplox.%(ext)s"), name_for())

print("\n-- except where somebody typed their own -------------------------")
typed = name_for(opts={"outtmpl": "my own name.%(ext)s"})
check("⭐ a name given by hand is used exactly as given",
      typed == "my own name.%(ext)s", typed)

print("\n-- and it does not shove aside what was already in the name -------")
clipped = name_for(start="00:10", end="00:20")
check("a trimmed clip still says so", "clip" in clipped, clipped)
check("...and still carries the mark", clipped.endswith(" Riplox.%(ext)s"), clipped)
check("the id is still there", "%(id)s" in name_for())
check("the height is still there for video", "%(height)sp" in name_for())
check("...and still absent for audio, which has none",
      "%(height)s" not in name_for(quality="mp3"), name_for(quality="mp3"))


print("\n-- the row says what it actually got -------------------------------")
job = engine.Job(url="https://example.com/v", quality="best")
check("before it lands, the word it was asked with",
      job.to_dict()["qualityLabel"] == engine.QUALITY_LABELS["best"],
      job.to_dict()["qualityLabel"])

job.height = 2160
check("⭐ once the file exists, the height it really came out at",
      job.to_dict()["qualityLabel"] == "2160p", job.to_dict()["qualityLabel"])

job.height = 720
check("...whatever that height is", job.to_dict()["qualityLabel"] == "720p")

for quality, expect in (("max", engine.QUALITY_LABELS["max"]),
                        ("1080", engine.QUALITY_LABELS["1080"]),
                        ("mp3", engine.QUALITY_LABELS["mp3"])):
    other = engine.Job(url="https://example.com/v", quality=quality)
    other.height = 2160
    check("%-5s keeps its own name - it already says what it means" % quality,
          other.to_dict()["qualityLabel"] == expect,
          other.to_dict()["qualityLabel"])

check("a height of zero is not mistaken for an answer",
      engine.Job(url="https://x/y", quality="best").height == 0)

shutil.rmtree(SANDBOX, ignore_errors=True)

print("\n" + "=" * 68)
print("  " + str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68 + "\n")
raise SystemExit(1 if FAIL else 0)
