"""
Saving the description, and the flag that was dropped instead of added.

v1.5.0's last two items were `--write-description` and `--break-on-existing`.
Only one of them is here. The other was measured against the code it was meant
to speed up and turned out to have nothing to do - the reasoning is written
down at the bottom of this file, because a feature that was deliberately not
built is worth as much as one that was, and it is the kind of thing that gets
proposed again in six months.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-desc-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


man = engine.DownloadManager()
DOWNLOADS = SANDBOX / "Downloads"
DOWNLOADS.mkdir(parents=True, exist_ok=True)
SETTINGS = dict(engine.DEFAULT_SETTINGS, download_dir=str(DOWNLOADS))


def command(opts=None, settings=None):
    job = engine.Job(url="https://www.youtube.com/watch?v=abc", quality="1080",
                     opts=opts or {})
    return man.build_args(job, settings or SETTINGS, "", None)


print("\n-- the description, when it is asked for -------------------------")
check("the flag reaches the command",
      "--write-description" in command({"write_desc": True}))
check("and stays out of it when it was not asked for",
      "--write-description" not in command({}))
check("a yes is the only thing that turns it on",
      "write_desc" not in engine.clean_opts({"write_desc": False}))
check("...and it arrives as a yes, not as whatever was sent",
      engine.clean_opts({"write_desc": "sure"}).get("write_desc") is True)
check("it does not disturb the rest of the command",
      len(command({"write_desc": True})) == len(command({})) + 1)

html = (SRC / "templates" / "index.html").read_text(encoding="utf-8")
js = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
check("there is a box for it", "optWriteDesc" in html and "optWriteDesc" in js)
check("it belongs to one download, so it is cleared for the next link",
      '$("optWriteDesc").checked = false;' in js)
# yt-dlp writes nothing at all when a site has no description, and says
# nothing about that either. The label therefore promises the description
# rather than a file that may not appear.
check("the label does not promise a file that may not arrive",
      "description as a text file" in html)


print("\n-- --break-on-existing: measured, then dropped --------------------")
# It was in the plan to stop a re-check walking a whole channel. Riplox does
# not walk one. There are exactly two places a listing is read at all, and
# neither of them can be shortened by that flag:
#
#   1. peek(), which Watch uses - already capped with --playlist-end, and it
#      keeps its own set of seen ids rather than a download archive.
#   2. build_args, which carries --no-playlist: every download is one video,
#      so there is no list to stop walking.
engine_src = (SRC / "engine.py").read_text(encoding="utf-8")
watch_src = (SRC / "watch.py").read_text(encoding="utf-8")

check("a download reads one video, never a list",
      '"--no-playlist"' in engine_src)
check("a watch check is already capped rather than walked",
      "--playlist-end" in engine_src)
check("...and it remembers what it has seen itself, not through an archive",
      '"known"' in watch_src and "--download-archive" not in watch_src)
# The one that would have been the real damage. Riplox reads the path of an
# already-downloaded file out of yt-dlp's own "has already been downloaded"
# line - that is what leaves the Play button something to open. --break-on-
# existing stops before printing it.
check("the already-downloaded line is still what recovers the path",
      "has already been downloaded" in engine_src)
check("and the flag nobody needed is nowhere in the code",
      "--break-on-existing" not in engine_src)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
