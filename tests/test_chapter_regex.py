"""
Chapter titles reach yt-dlp as a REGEX, not as text.

--download-sections takes a pattern, and two things follow from that. Both
were measured on the bundled 2026.07.04 binary rather than read anywhere:

  * "C++ (part 1)" is not a pattern meaning "C++ (part 1)". It is not a valid
    pattern at all - yt-dlp refuses to start: `invalid --download-sections
    regex "C++ (part 1)" - multiple repeat at position 2`.
  * A pattern is searched INSIDE the title, not matched against it. Asking a
    real 2-hour video for "Data Types" came back with two sections - "Data
    Types" and "Data Types (List, Tuple, Set, Dictionary)" - the second one
    twenty minutes long and never ticked.

So a title has to be escaped and anchored before it goes near the command.
The titles that fail loudly are the lucky ones; the ones that compile are
what quietly download the wrong chapter.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-chapters-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


# Read off a real video on 2026-08-27 (youtube.com/watch?v=q3AuP01daL4), not
# invented. Two pairs in here are the whole reason anchoring is not optional:
# "Data Types" is a prefix of chapter 13, and "Mini Project: ..." starts two
# different chapters.
CHAPTERS = [
    "Intro",
    "Setup & Installation",
    "Printing & Variables",
    "Data Types",
    "Input & Comments",
    "Type Conversion",
    "String Operations",
    "Logical Operators",
    "Conditional Statements (If-Else)",
    "Mini Project: Calculator",
    "Loops (Range, For, While)",
    "Loop Control (Break/Continue)",
    "Data Types (List, Tuple, Set, Dictionary)",
    "Functions",
    "Mini Project: Guess the Number",
]

# The three the audit's premortem named, plus the ones that are dangerous
# because they compile: a leading * is how yt-dlp spells "time range", and a
# bare . matches any character at all.
AWKWARD = ["C++", "(part 1)", "50% off", "*Intro", "a.b", "what? [live]"]


def selects(pattern):
    """Every chapter yt-dlp would pick for this pattern. It searches, not matches."""
    return [c for c in CHAPTERS if re.search(pattern, c)]


print("\n-- the punctuation the premortem named ----------------------------")
for title in AWKWARD:
    pattern = engine.chapter_regex(title)
    try:
        re.compile(pattern)
        compiled = True
    except re.error as exc:
        compiled, pattern = False, f"{pattern} ({exc})"
    check(f"{title!r} compiles at all", compiled, pattern)
    if compiled:
        check(f"{title!r} matches itself", bool(re.fullmatch(pattern, title)))


print("\n-- and what the naive version would have done ---------------------")
# If chapter_regex is ever reduced to `return title`, these are the checks
# that go red. A test that cannot fail teaches nobody anything.
#
# "C++" is the one worth reading twice. yt-dlp's own frozen Python rejects it
# outright - measured: `multiple repeat at position 2`. This host runs 3.11,
# where ++ became a possessive quantifier, so the same raw title compiles and
# quietly means "one or more C" instead. Two Pythons, two failure modes, and
# the newer one is the worse: it does not complain, it just picks the wrong
# chapter. So the check is on the meaning, not on the exception.
try:
    wrong = bool(re.compile("C++").search("CSS Basics"))
    why = "compiles as a quantifier and matches 'CSS Basics'"
except re.error as exc:
    wrong, why = True, str(exc)
check("passing 'C++' through raw never means the title 'C++'", wrong, why)
check("passing 'Data Types' through raw takes two chapters, not one",
      len(selects("Data Types")) == 2, str(selects("Data Types")))
check("passing 'a.b' through raw would also take 'axb'",
      bool(re.search("a.b", "axb")))


print("\n-- one ticked chapter, one chapter selected -----------------------")
for title in CHAPTERS:
    hit = selects(engine.chapter_regex(title))
    check(f"{title!r} selects exactly itself", hit == [title], str(hit))


print("\n-- a title starting with * is a title, not a time range -----------")
star = engine.chapter_regex("*Intro")
check("the pattern does not begin with *", not star.startswith("*"), star)
check("it still matches the chapter", bool(re.fullmatch(star, "*Intro")))


print("\n-- chapter_args ---------------------------------------------------")
check("nothing asked for, nothing added", engine.chapter_args([]) == [])
check("None is the same as nothing", engine.chapter_args(None) == [])
check("blank titles are dropped, not passed as an empty pattern",
      engine.chapter_args(["", "   ", None]) == [])

args = engine.chapter_args(["Intro", "Functions"])
check("one --download-sections per chapter",
      args.count("--download-sections") == 2, " ".join(args))
check("ffmpeg is left able to report progress", "--no-quiet" in args)
check("the patterns are the escaped ones",
      args[args.index("--download-sections") + 1] == engine.chapter_regex("Intro"))

twice = engine.chapter_args(["Intro", "Intro", " Intro "])
check("the same chapter ticked twice is fetched once",
      twice.count("--download-sections") == 1, " ".join(twice))


print("\n-- the real binary accepts every pattern we build -----------------")
# Run with no URL at all: yt-dlp validates --download-sections while parsing
# options, so a bad pattern dies there, and a good one gets as far as "You
# must provide at least one URL". Nothing is fetched and nothing is written.
exe = engine.ytdlp_path()
if not exe:
    print("  --    yt-dlp binary not present, skipping")
else:
    for title in CHAPTERS + AWKWARD:
        pattern = engine.chapter_regex(title)
        out = subprocess.run([str(exe), "--download-sections", pattern],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stderr or ""
        check(f"yt-dlp takes the pattern for {title!r}",
              "invalid --download-sections" not in out,
              out.strip().splitlines()[-1][:80] if out.strip() else "")

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
