"""
Downloading the ticked chapters.

One press, one folder, one file per chapter - the first time in this app that
a single job has produced more than one file. That is where the last two real
bugs came from, so the things this checks are not the flags but the
consequences: what the job ends up pointing at, what its size says, and what
happens when a trim and a chapter selection are both asked for at once.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-chapterdl-"))
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


def command(opts=None, quality="1080", start="", end=""):
    job = engine.Job(url="https://www.youtube.com/watch?v=abc", quality=quality,
                     opts=opts or {})
    job.start, job.end = start, end
    return man.build_args(job, SETTINGS, "", None)


def sections(args):
    return [args[i + 1] for i, a in enumerate(args) if a == "--download-sections"]


print("\n-- every chapter is one pattern, not two hundred ------------------")
every = engine.chapter_args(None, every=True)
check("all of them is a single --download-sections",
      every.count("--download-sections") == 1, " ".join(every))
check("and the pattern is the one that matches any title",
      ".*" in every, " ".join(every))
check("ffmpeg can still report progress", "--no-quiet" in every)
# 200 titles at 80 characters is 16000 before the flags; Windows takes 32767
# for the whole command. This is the selection that could not have fitted.
named = engine.chapter_args(["Chapter %d" % i for i in range(200)])
check("naming two hundred of them would have taken two hundred arguments",
      named.count("--download-sections") == 200, str(named.count("--download-sections")))


print("\n-- the ticked chapters reach the command --------------------------")
args = command({"chapters": ["Intro", "Functions"]})
check("one pattern per ticked chapter", len(sections(args)) == 2, str(sections(args)))
check("escaped and anchored, not raw",
      sections(args) == [engine.chapter_regex("Intro"), engine.chapter_regex("Functions")],
      str(sections(args)))
check("all of them is the flag, not the list",
      sections(command({"chapters_all": True})) == [".*"])


print("\n-- a trim and a chapter selection cannot both be asked for --------")
# yt-dlp adds every --download-sections it is given rather than choosing, so
# a trim left on beside a chapter selection would hand back the chapters AND
# the trimmed range - a file nobody asked for, with no error anywhere.
both = command({"chapters": ["Intro"]}, start="1:00", end="2:00")
check("the chapters win", sections(both) == [engine.chapter_regex("Intro")], str(sections(both)))
check("and the trimmed range is not in there too",
      not any(s.startswith("*") for s in sections(both)), str(sections(both)))
plain = command({}, start="1:00", end="2:00")
check("a trim on its own still works", sections(plain) == ["*1:00-2:00"], str(sections(plain)))


print("\n-- part of a video is not the video -------------------------------")
# Same reason a trim is kept out: the archive is what makes "skip files I
# already have" work, and three chapters recorded under the video's own id
# would make the whole video look already downloaded.
kept = dict(SETTINGS, skip_existing=True)


def archived(opts):
    job = engine.Job(url="https://www.youtube.com/watch?v=abc", quality="1080",
                     opts=opts)
    return "--download-archive" in man.build_args(job, kept, "", None)


check("a whole video is recorded in the archive", archived({}))
check("a chapter selection is not", not archived({"chapters": ["Intro"]}))
check("nor is all-chapters", not archived({"chapters_all": True}))


print("\n-- where the files go ---------------------------------------------")
tmpl = engine.DownloadManager()._outtmpl(SETTINGS, engine.Job(
    url="u", quality="1080", opts={"chapters": ["Intro"]}))
check("under a Chapters folder", "Chapters" in tmpl, tmpl)
check("then one folder per video, with its id",
      "%(title).100B [%(id)s]" in tmpl, tmpl)
check("the quality is in the folder name, so 720p does not land on 1080p",
      "%(height)sp" in tmpl, tmpl)
check("one file per chapter, named after the chapter",
      "%(section_title)s" in tmpl, tmpl)
# yt-dlp counts sections from zero. A folder starting at "00 - Intro" reads
# as a fault, and the template can do the arithmetic - measured on the
# bundled binary, not assumed.
check("numbered from one, not from zero", "%(section_number+1)02d" in tmpl, tmpl)

mp3 = engine.DownloadManager()._outtmpl(SETTINGS, engine.Job(
    url="u", quality="mp3", opts={"chapters_all": True}))
check("an audio split says so instead of claiming a height",
      "[mp3]" in mp3 and "%(height)s" not in mp3, mp3)

ordinary = engine.DownloadManager()._outtmpl(SETTINGS, engine.Job(
    url="u", quality="1080", opts={}))
check("an ordinary download is untouched by any of this",
      "Chapters" not in ordinary and "Riplox" in ordinary, ordinary)


print("\n-- what arrives from the browser is not trusted -------------------")
check("a list of titles comes through",
      engine.clean_opts({"chapters": ["A", "B"]}).get("chapters") == ["A", "B"])
check("blanks are dropped",
      engine.clean_opts({"chapters": ["A", "", "  ", None]}).get("chapters") == ["A"])
check("the same title twice is one",
      engine.clean_opts({"chapters": ["A", "A"]}).get("chapters") == ["A"])
check("something that is not a list is not a selection",
      "chapters" not in engine.clean_opts({"chapters": "Intro"}))
check("an empty list is not a selection",
      "chapters" not in engine.clean_opts({"chapters": []}))
check("all-chapters arrives as a yes",
      engine.clean_opts({"chapters_all": True}).get("chapters_all") is True)
check("...and never as a maybe",
      "chapters_all" not in engine.clean_opts({"chapters_all": False}))


print("\n-- the size of a folder is not the size of its directory entry ----")
folder = SANDBOX / "Chapters" / "A video [abc] 1080p"
folder.mkdir(parents=True, exist_ok=True)
for name, size in (("01 - Intro.mp4", 3000), ("02 - Middle.mp4", 5000),
                   ("03 - End.mp4", 2000)):
    (folder / name).write_bytes(b"x" * size)

# The failure this replaces did not raise. .stat() on a directory succeeds and
# answers with a few kilobytes, so the except clause around it never fired and
# a whole folder of chapters was recorded as about 4 KB.
raw = folder.stat().st_size
check("the naive answer really is wrong, not an error", raw != 10000, str(raw))
check("the folder adds up to what is in it",
      engine.written_bytes(folder) == 10000, str(engine.written_bytes(folder)))

one = SANDBOX / "single.mp4"
one.write_bytes(b"x" * 1234)
check("a single file still answers for itself",
      engine.written_bytes(one) == 1234, str(engine.written_bytes(one)))
check("something that is not there is still an error, not a zero",
      not Path(SANDBOX / "gone.mp4").exists())
try:
    engine.written_bytes(SANDBOX / "gone.mp4")
    check("a missing file raises rather than reporting nothing", False, "it returned")
except OSError:
    check("a missing file raises rather than reporting nothing", True)


print("\n-- and the guards that live in the server ------------------------")
app_py = (SRC / "app.py").read_text(encoding="utf-8")
check("a playlist cannot carry one video's chapters",
      'opts.pop("chapters", None)' in app_py)
check("too many titles is refused out loud, not silently dropped",
      "too many chapters" in app_py)
check("a folder is not called a file when it goes missing",
      "That file is no longer there." not in app_py)

js = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
check("all-chapters travels as a flag, not as every title",
      "chapters_all = true" in js)
check("chapters sharing a title tick together, as the mark promised",
      "other.checked = box.checked" in js)
check("the trim is put away when chapters are ticked",
      '$("trimOn").checked = false; resetTrim();' in js)

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
