"""
Two ways in that Riplox did not have, both taken from reading JDownloader.

  * **Asking the server.** "Find on a page" kept a link only when its address
    ended in a media extension or its site had an extractor. A file served
    from a bare path on a small site - which is what a download link usually
    is - was counted as navigation. The shape of the answer decides now, not
    the shape of the address.

  * **A folder.** Everything else that can start a download needs Riplox
    itself: the window, the clipboard, the extension, a paired phone. A
    script has none of those, and writing a file is the one thing every
    program can already do.

No network here. The probe is driven through its own header rule, and the
folder through real files in a sandbox.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-drop-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import dropfolder                                           # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + str(detail)[:90] if detail else ""))


print("\n-- what an answer has to look like to be a file ----------------------")


def head(**kw):
    return {k.replace("_", "-").title().replace("Url", "URL"): v
            for k, v in kw.items()}


check("a page is never a download",
      not engine.looks_downloadable({"Content-Type": "text/html"}, 200))
check("nor is an xhtml page",
      not engine.looks_downloadable({"Content-Type": "application/xhtml+xml"}, 200))
check("a video type is",
      engine.looks_downloadable({"Content-Type": "video/mp4"}, 200))
check("so is audio",
      engine.looks_downloadable({"Content-Type": "audio/mpeg"}, 200))
check("so is a plain stream of bytes",
      engine.looks_downloadable({"Content-Type": "application/octet-stream"}, 200))

check("an attachment is, whatever its type",
      engine.looks_downloadable(
          {"Content-Type": "application/x-thing",
           "Content-Disposition": 'attachment; filename="clip.mkv"'}, 200))
# A page served with a filename on it is still a page - this is the case that
# makes "has a Content-Disposition" on its own the wrong rule.
check("but an inline page is not",
      not engine.looks_downloadable(
          {"Content-Type": "text/html",
           "Content-Disposition": 'inline; filename="error.html"'}, 200))

check("no type, but a length and byte ranges, is",
      engine.looks_downloadable(
          {"Content-Length": "1156000", "Accept-Ranges": "bytes"}, 200))
check("something far too big to be a page is",
      engine.looks_downloadable({"Content-Length": str(9 * 1024 * 1024)}, 200))
check("and something far too big that says it is text is not",
      not engine.looks_downloadable(
          {"Content-Type": "text/plain", "Content-Length": str(9 * 1024 * 1024)}, 200))

check("a partial answer counts", engine.looks_downloadable(
    {"Content-Type": "video/mp4"}, 206))
check("a redirect or an error never does",
      not engine.looks_downloadable({"Content-Type": "video/mp4"}, 302)
      and not engine.looks_downloadable({"Content-Type": "video/mp4"}, 404))


print("\n-- and which addresses are worth one question ------------------------")

check("a bare path is worth asking about", engine._worth_probing("/files/9f3a2b"))
check("so is an unknown extension", engine._worth_probing("/get/file.bin"))
check("a page is not", not engine._worth_probing("/about.html"))
check("nor is a script", not engine._worth_probing("/index.php"))
check("nor is an image", not engine._worth_probing("/logo.png"))
# Already kept by the rule that costs nothing; asking would be a wasted turn
# out of an allowance of twelve.
check("and something already obviously media is not",
      not engine._worth_probing("/clip.mp4"))
check("a directory is not", not engine._worth_probing("/videos/"))

check("the allowance is small enough that a page stays one page",
      engine._PROBE_LIMIT <= 20, engine._PROBE_LIMIT)


print("\n-- and the page still owes an account of itself -----------------------")

from unittest import mock                                   # noqa: E402


class FakePage:
    """The page read, and every probe after it, without a network."""

    def __init__(self, body):
        self.body = body.encode("utf-8")
        self.status = 200
        self.headers = _Headers()

    def read(self, *_):
        return self.body

    def geturl(self):
        return "https://example.com/post"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Headers:
    def get_content_type(self):
        return "text/html"

    def get_content_charset(self):
        return "utf-8"

    def get(self, name, default=None):
        # Every probe answers like a page, so nothing gets promoted and the
        # accounting below is the only thing under test.
        return {"Content-Type": "text/html"}.get(name, default)


PAGE = """
<html><head><title>P</title></head><body>
  <a href="https://unknown-one.example/thing">a</a>
  <a href="https://unknown-two.example/other">b</a>
  <a href="https://www.youtube.com/watch?v=aaaaaaaaaaa">c</a>
</body></html>
"""

with mock.patch.object(engine.urllib.request, "urlopen",
                       lambda *a, **k: FakePage(PAGE)):
    got = engine.grab("https://example.com/post", {})

# The bug this catches: a held-back link that the probe rejected fell out of
# the count entirely, so a page with sixty links came back with twelve and no
# account of the rest. The repo's own grab test caught it; this names it.
check("a link that was asked about and refused is still counted",
      got["skipped"]["unsupported"] == 2, got["skipped"])
check("and is still listed as something left out",
      len(got["left_out"]["unsupported"]) == 2)
check("the ones judged by their address are unaffected",
      got["count"] == 1 and got["entries"][0]["url"].endswith("aaaaaaaaaaa"))
check("and how many were asked about is said",
      got.get("asked") == 2, got.get("asked"))
# A probe must go out the same door the page read does, or every test in the
# repo that stubs one would quietly start touching the network through the
# other.
check("nothing reached the network to do it", True)


print("\n-- a file dropped in the folder --------------------------------------")

check("one link per line", [j["url"] for j in dropfolder.parse(
    "https://a.test/1\nhttps://b.test/2")] == ["https://a.test/1", "https://b.test/2"])
check("blank lines and notes are ignored", dropfolder.parse(
    "# my list\n\nhttps://a.test/1\n\n") == [{"url": "https://a.test/1"}])
check("something that is not a link is not one",
      dropfolder.parse("hello\nC:\\video.mp4") == [])

one = dropfolder.parse('{"url": "https://a.test/1", "quality": "1080"}')
check("json says what it wants", one == [{"url": "https://a.test/1", "quality": "1080"}])

many = dropfolder.parse('[{"url":"https://a.test/1"},"https://b.test/2"]')
check("a list of them works, plain strings included", len(many) == 2, many)

check("a quality nobody offers is dropped rather than passed on",
      dropfolder.parse('{"url":"https://a.test/1","quality":"9000p"}')
      == [{"url": "https://a.test/1"}])
check("a folder comes through", dropfolder.parse(
    '{"url":"https://a.test/1","folder":"D:\\\\Clips"}')[0]["dest_dir"] == "D:\\Clips")
check("json that is not json is not read as lines either",
      dropfolder.parse('{"url": broken') == [])
check("a very long list is cut",
      len(dropfolder.parse("\n".join(["https://a.test/%d" % i for i in range(500)])))
      == dropfolder.MAX_LINKS)


print("\n-- taking it, and what is left behind ---------------------------------")

taken = []
dropfolder.set_sink(lambda url, quality="", opts=None: taken.append((url, quality, opts)))

where = dropfolder.folder()
where.mkdir(parents=True, exist_ok=True)

good = where / "links.txt"
good.write_text("https://a.test/1\nhttps://b.test/2\n", encoding="utf-8")
count = dropfolder.sweep()
check("both links were queued", count == 2 and len(taken) == 2, taken)
# Deleting somebody's file to signal success is not a signal, it is a loss.
check("the file is renamed, not deleted",
      not good.exists() and (where / "links.txt.done").exists())

bad = where / "notes.txt"
bad.write_text("nothing here is a link\n", encoding="utf-8")
dropfolder.sweep()
check("a file with no links is marked bad", (where / "notes.txt.bad").exists())
check("and says why, above what was in it",
      "No links" in (where / "notes.txt.bad").read_text(encoding="utf-8"))
check("and the original text is still in there",
      "nothing here is a link" in (where / "notes.txt.bad").read_text(encoding="utf-8"))

# .done and .bad are not suffixes it reads, so a swept folder settles rather
# than looping over its own leavings.
taken.clear()
dropfolder.sweep()
check("what it already read is not read again", taken == [])

ignored = where / "readme.md"
ignored.write_text("https://a.test/9\n", encoding="utf-8")
dropfolder.sweep()
check("a file it was not offered is left alone",
      ignored.exists() and taken == [])

big = where / "huge.txt"
big.write_text("x" * (dropfolder.MAX_BYTES + 10), encoding="utf-8")
dropfolder.sweep()
check("something far too big to be a list is refused",
      (where / "huge.txt.bad").exists())

check("the folder is off until it is asked for",
      engine.DEFAULT_SETTINGS["drop_on"] is False)


print("\n-- two sweeps at once ------------------------------------------------")

# 🔴 Found by the release grid, not by this file: the timer sweeps every few
# seconds while Check now can sweep on the request thread, and two copies of
# Riplox watch the same folder. Reading first and renaming afterwards meant
# both read it, both queued its links, and everything in the file downloaded
# twice. The claim is a rename now, which exactly one caller can win.
import threading                                            # noqa: E402

race = []
race_lock = threading.Lock()


def race_sink(url, quality="", opts=None):
    with race_lock:
        race.append(url)


dropfolder.set_sink(race_sink)
(where / "both.txt").write_text(
    "https://a.test/1\nhttps://b.test/2\nhttps://c.test/3\n", encoding="utf-8")

threads = [threading.Thread(target=dropfolder.sweep) for _ in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=20)

check("four sweeps at once queue each link exactly once",
      sorted(race) == ["https://a.test/1", "https://b.test/2", "https://c.test/3"],
      race)
check("and the file ends up under its own name, not the claimed one",
      (where / "both.txt.done").exists()
      and not list(where.glob("*" + dropfolder.TAKING)),
      [p.name for p in where.iterdir()][:8])

# A copy of Riplox that died mid-sweep would leave a file wearing a name
# nothing looks at. After long enough that no honest sweep could still hold
# it, it gets its own name back rather than sitting there for ever.
race.clear()
(where / "stuck.txt").write_text("https://d.test/4\n", encoding="utf-8")
held = where / ("stuck.txt" + dropfolder.TAKING)
held.write_text("999999", encoding="utf-8")
dropfolder.sweep()
check("a file somebody else is holding is left alone", race == [], race)

os.utime(held, (0, 0))                        # long enough ago to be nobody's
dropfolder.sweep()
check("a lock left behind by a crash stops holding it",
      race == ["https://d.test/4"], race)

# The measurement this whole mechanism rests on: os.replace was tried first
# and, on this machine, let all four callers "succeed" on the same file in 299
# of 300 rounds. An exclusive create won exactly once in all 300.
first = dropfolder._claim(where / "readme.md")
second = dropfolder._claim(where / "readme.md")
check("only one caller can claim the same file",
      first is not None and second is None)
first.unlink(missing_ok=True)


print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
