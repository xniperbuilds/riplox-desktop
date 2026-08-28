"""What "find on a page" leaves out, it says.

grab() reads a page and lists what can be downloaded from it. It also drops
things: a link it has already seen, a site it has no extractor for, and
everything past three hundred. All three used to be a bare `continue` or
`break` - a page with sixty links could come back with twelve and no account of
the other forty-eight.

That is the failure this project treats as its worst: not being wrong, but
breaking quietly. So the three are counted, they are counted separately because
they mean different things, and this checks the numbers against a page whose
answer is known by construction.
"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import engine

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + detail if detail else ""))


class FakePage:
    """Stands in for the one request grab() makes."""

    def __init__(self, body):
        self.body = body.encode("utf-8")

    def read(self, *_):
        return self.body

    def geturl(self):
        return "https://example.com/post"

    @property
    def headers(self):
        class H:
            @staticmethod
            def get_content_type():
                return "text/html"

            @staticmethod
            def get_content_charset():
                return "utf-8"
        return H()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def grab(html):
    with mock.patch.object(engine.urllib.request, "urlopen",
                           lambda *a, **k: FakePage(html)):
        return engine.grab("https://example.com/post", {})


# A page built so the answer is known: three distinct YouTube links, one of
# them repeated twice, and two links to a site nothing can read.
LINKS = """
<html><head><title>A post with videos</title></head><body>
  <a href="https://www.youtube.com/watch?v=aaaaaaaaaaa">one</a>
  <a href="https://www.youtube.com/watch?v=bbbbbbbbbbb">two</a>
  <a href="https://www.youtube.com/watch?v=ccccccccccc">three</a>
  <a href="https://www.youtube.com/watch?v=aaaaaaaaaaa">one again</a>
  <a href="https://www.youtube.com/watch?v=bbbbbbbbbbb/">two again, trailing slash</a>
  <a href="https://not-a-known-site.example/thing">unreadable</a>
  <a href="https://also-unknown.example/other">unreadable too</a>
  <a href="/relative/page.html">the page's own navigation - never a candidate</a>
</body></html>
"""

print("\n-- a page whose answer is known by construction ---------------------")
info = grab(LINKS)
s = info.get("skipped") or {}

check("the three it can read are the three it lists", info["count"] == 3,
      "count=" + str(info["count"]))
check("both repeats are counted, not dropped in silence",
      s.get("duplicates") == 2, "duplicates=" + str(s.get("duplicates")))
check("both unreadable sites are counted",
      s.get("unsupported") == 2, "unsupported=" + str(s.get("unsupported")))
check("but the page's own navigation is not, or the line would be noise",
      s.get("unsupported") == 2, "a relative link is on the page and must not count")
check("nothing claims the cap was hit", not s.get("capped"),
      "capped=" + str(s.get("capped")))

print("\n-- and every row says where it came from ---------------------------")
sites = [e.get("site") for e in info["entries"]]
check("each entry carries its site", all(sites), ", ".join(str(x) for x in sites))
check("...and it is the site the link is actually on",
      set(sites) == {"YouTube"}, str(set(sites)))

print("\n-- the tally is not there when there is nothing to say --------------")
clean = grab("""<html><title>t</title><body>
  <a href="https://www.youtube.com/watch?v=ddddddddddd">only one</a>
</body></html>""")
c = clean.get("skipped") or {}
check("a page with nothing left out reports nothing left out",
      c.get("duplicates") == 0 and c.get("unsupported") == 0 and not c.get("capped"),
      str(c))

print("\n-- and the cap reports itself --------------------------------------")
many = "<html><title>t</title><body>" + "".join(
    '<a href="https://www.youtube.com/watch?v=%011d">v</a>' % i
    for i in range(engine._GRAB_CAP + 25)) + "</body></html>"
big = grab(many)
check("stopping at the cap is stated rather than silent",
      (big.get("skipped") or {}).get("capped") == 1,
      "count=" + str(big["count"]))

print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
