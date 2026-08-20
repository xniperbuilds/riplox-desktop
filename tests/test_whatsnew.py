"""
What's new, built from the commits instead of remembered.

The panel sat four features out of date while the app shipped twice, because
nothing makes a hand-written list wrong until somebody looks at it. These cases
are about the two ways the replacement could be worse than the thing it
replaced: filling with noise, or taking the app down with it.

A throwaway git repository is built here rather than reading the real one, so
the cases are exact and the suite does not change meaning as history moves.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-whatsnew-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + str(detail) if detail else ""))


# --------------------------------------------------------------------------
# A repository of our own, so the cases are exact
# --------------------------------------------------------------------------

def git(where, *args, **kw):
    return subprocess.run(("git",) + args, cwd=str(where), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", **kw)


def build_repo(commits, tag_first=True):
    """A tiny repo: one commit per message, optionally tagged at the start."""
    work = SANDBOX / ("repo%d" % len(list(SANDBOX.glob("repo*"))))
    (work / "src").mkdir(parents=True)
    (work / "build").mkdir()
    shutil.copy(REPO / "build" / "make_whatsnew.py", work / "build")
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@t.t")
    git(work, "config", "user.name", "t")

    (work / "seed.txt").write_text("seed", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "seed")
    if tag_first:
        git(work, "tag", "v1.0.0")

    for i, message in enumerate(commits):
        (work / ("f%d.txt" % i)).write_text("x", encoding="utf-8")
        git(work, "add", "-A")
        git(work, "commit", "-q", "-F", "-", input=message)
    return work


def generate(work):
    done = subprocess.run([sys.executable, "build/make_whatsnew.py"], cwd=str(work),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    out = work / "src" / "whatsnew.json"
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return done, data


print("\n-- only commits that opted in are listed ---------------------------")
work = build_repo([
    "Send text as well as links\n\nBody text here.\n\nWhats-new: Send a key from your phone, sealed until copied.",
    "Fix flaky test in the queue suite",              # no trailer - must not appear
    "Tidy imports",                                    # no trailer - must not appear
])
done, data = generate(work)
items = data["items"]
check("the trailer line is taken", "Send a key from your phone, sealed until copied." in items)
check("a commit with no trailer is NOT taken", len(items) == 1, items)
check("so internal work stays out of the panel",
      not any("flaky" in i or "imports" in i for i in items), items)
check("the subject is never used as a fallback",
      not any(i.startswith("Send text as well as links") for i in items), items)

print("\n-- an explicit skip is honoured -----------------------------------")
work = build_repo(["Something internal\n\nWhats-new: skip"])
done, data = generate(work)
check("'skip' contributes nothing", data["items"] == [], data["items"])

print("\n-- one commit can ship more than one thing ------------------------")
work = build_repo([
    "Two things\n\nWhats-new: The first thing.\nWhats-new: The second thing."])
done, data = generate(work)
check("both lines are taken", data["items"] == ["The first thing.", "The second thing."],
      data["items"])

print("\n-- only since the last tag ----------------------------------------")
work = build_repo(["Old\n\nWhats-new: Before the tag."], tag_first=False)
git(work, "tag", "v2.0.0")
(work / "after.txt").write_text("x", encoding="utf-8")
git(work, "add", "-A")
git(work, "commit", "-q", "-F", "-", input="New\n\nWhats-new: After the tag.")
done, data = generate(work)
check("the tagged range is used", data["items"] == ["After the tag."], data["items"])
check("and the tag is recorded", data["since"] == "v2.0.0", data["since"])

print("\n-- no tag at all does not explode ---------------------------------")
work = build_repo(["Only\n\nWhats-new: The only line."], tag_first=False)
done, data = generate(work)
check("it still produces the list", data["items"] == ["The only line."], data["items"])
check("and says the range is the whole history", data["since"] == "", repr(data["since"]))

print("\n-- duplicates and whitespace --------------------------------------")
work = build_repo([
    "A\n\nWhats-new:    Spaced   out    line.",
    "B\n\nWhats-new: Spaced out line."])
done, data = generate(work)
check("whitespace is collapsed", data["items"] == ["Spaced out line."], data["items"])
check("and the same line is not repeated", len(data["items"]) == 1)

print("\n-- outside a git checkout it writes nothing, quietly ---------------")
bare = SANDBOX / "notgit"
(bare / "build").mkdir(parents=True)
(bare / "src").mkdir()
shutil.copy(REPO / "build" / "make_whatsnew.py", bare / "build")
done = subprocess.run([sys.executable, "build/make_whatsnew.py"], cwd=str(bare),
                      capture_output=True, text=True, encoding="utf-8", errors="replace")
check("it exits cleanly", done.returncode == 0, done.returncode)
check("and leaves no file behind", not (bare / "src" / "whatsnew.json").exists())

print("\n-- the app never falls over on this file --------------------------")
# The least important thing on the page must not be able to stop the window
# opening, so every bad shape has to answer with an empty list.
import app                                                    # noqa: E402

res = app.resource_dir() / "whatsnew.json"
saved = res.read_text(encoding="utf-8") if res.exists() else None
try:
    for label, body in (
            ("missing", None),
            ("not json", "{{{ broken"),
            ("json but not an object", "[1, 2, 3]"),
            ("object without items", '{"since": "v1"}'),
            ("items is not a list", '{"items": "nope"}'),
            ("items holds non-strings", '{"items": [1, null, {"a": 1}]}'),
            ("items holds blanks", '{"items": ["   ", ""]}')):
        if body is None:
            res.unlink(missing_ok=True)
        else:
            res.write_text(body, encoding="utf-8")
        try:
            got = app.whats_new()
            check("survives %s" % label, got == [], got)
        except Exception as exc:                              # noqa: BLE001
            check("survives %s" % label, False, "RAISED " + type(exc).__name__)

    res.write_text('{"items": ["A real line."]}', encoding="utf-8")
    check("and reads a good file", app.whats_new() == ["A real line."])
finally:
    if saved is None:
        res.unlink(missing_ok=True)
    else:
        res.write_text(saved, encoding="utf-8")

print("\n" + "=" * 68)
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
print("=" * 68)
shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
