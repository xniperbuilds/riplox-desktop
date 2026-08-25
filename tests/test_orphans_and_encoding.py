"""Three bugs found on Nazim's own running copy, 25 Aug.

He reported two things: one TikTok link stuck downloading for hours while other
videos finished, and Library's Play button opening a folder instead of playing.
They turned out to be three separate faults, and all three were silent.

  1. tie_to_app() - written to make Windows kill yt-dlp when Riplox stops -
     was never called. Three yt-dlp processes from dead Riploxes were still
     running, holding 3.7, 2.6 and 2.1 hours of CPU between them, two of them
     on the same URL the live copy was retrying.

  2. yt-dlp writes its output in the console codepage, not utf-8. Riplox reads
     the pipe as utf-8, so every title with a curly quote or an emoji came back
     mangled - and the "Destination:" line is where Riplox learns the path it
     just saved. The library recorded names that do not exist on disk.
     56 of 244 entries on his machine.

  3. When the recorded path is missing, the server opens the folder and returns
     a note explaining why. Neither caller ever showed the note, so Play opened
     a folder and said nothing.

⚠️ LOCALAPPDATA is redirected before engine is imported.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-orphan-test-"))
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


print("\n-- 1. every yt-dlp is tied to the app that started it ---------------")

check("tie_to_app still exists", "def tie_to_app(" in src)
# The bug was that it existed and nobody called it. Count real call sites.
calls = len(re.findall(r"^\s*tie_to_app\(", src, re.M))
check("⭐ it is actually CALLED - this is the whole bug", calls >= 1,
      "%d call site(s)" % calls)

# It has to be called on the process that is spawned, right where it is spawned.
after = src.split("job.proc = proc", 1)
check("⭐ ...on the download process, immediately after it starts",
      len(after) > 1 and "tie_to_app(proc)" in after[1][:900])

check("the job object still asks Windows to kill on close",
      "_JOB_KILL_ON_CLOSE" in src and "SetInformationJobObject" in src)


print("\n-- 2. yt-dlp is told to speak utf-8 ---------------------------------")

settings = {"download_dir": str(SANDBOX / "dl"), "engine_channel": "stable"}
try:
    args = engine._base_args(settings)
    joined = " ".join(str(a) for a in args)
    check("⭐ --encoding utf-8 is on every yt-dlp run",
          "--encoding" in args and args[args.index("--encoding") + 1] == "utf-8",
          joined[:88])
except engine.EngineMissing:
    # No binary in a bare checkout - check the source instead.
    check("⭐ --encoding utf-8 is on every yt-dlp run",
          '"--encoding", "utf-8"' in src, "checked in source")

check("the reason is written down, not just the flag",
      "cp1252" in src or "console codepage" in src)
check("...including that PYTHONIOENCODING does NOT work here",
      "PYTHONIOENCODING" in src)

# The decode side must stay forgiving: a mangled byte should never crash a
# download, even now that it should not arrive.
check("the pipe is still read as utf-8 with replacement, not strict",
      'encoding="utf-8"' in src and 'errors="replace"' in src)


print("\n-- 3. a folder opening instead of a video says so -------------------")

app_src = (ROOT / "src" / "app.py").read_text(encoding="utf-8-sig")
open_fn = app_src.split("def api_open(", 1)[1].split("\n@app.", 1)[0]
check("the server still opens the folder when the file has moved",
      "opened its folder" in open_fn or "folder is open" in open_fn)
check("...and still says ok, so the button is not reported as broken",
      '"ok": True, "note"' in open_fn)

js = (ROOT / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8")
notes = len(re.findall(r"else if \(r\.note\) toast\(r\.note", js))
check("⭐ BOTH callers now show that note - queue and library",
      notes >= 2, "%d of 2 call sites" % notes)

# Guard against the shape that hid it: a caller that only looks at r.ok.
opens = len(re.findall(r'api\("/api/open"', js))
check("every /api/open caller is accounted for", opens >= 2,
      "%d caller(s), %d showing the note" % (opens, notes))


print("")
print(str(len(PASS)) + " passed, " + str(len(FAIL)) + " failed")
for name in FAIL:
    print("  FAILED: " + name)
import shutil                                               # noqa: E402
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
