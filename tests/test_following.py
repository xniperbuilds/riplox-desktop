"""
Following: the published feed, the floor that still applies without one, and
the two switches that stand between a followed channel and a download.

No network. The feed is a saved fixture in the shape YouTube actually answers
with, and the queue is a stand-in that records what it was asked for - what is
being checked here is the decisions, not anyone else's server.
"""

import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="riplox-follow-"))
os.environ["LOCALAPPDATA"] = str(SANDBOX)

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import engine                                               # noqa: E402
import watch                                                # noqa: E402

assert str(SANDBOX) in str(engine.data_dir()), "sandbox not in effect - stop"

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (" | " + str(detail)[:90] if detail else ""))


CHANNEL = "UC_x5XG1OV2P6uZZ5FSM9Ttw"

print("\n-- working out the feed ---------------------------------------------")

check("a channel address carries its own id",
      watch.feed_for(f"https://www.youtube.com/channel/{CHANNEL}", {})
      == f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}")

check("a handle needs the id the engine read",
      watch.feed_for("https://www.youtube.com/@someone/videos",
                     {"channel_id": CHANNEL})
      == f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL}")

check("and without one it keeps the engine path",
      watch.feed_for("https://www.youtube.com/@someone/videos", {}) == "")

check("a playlist link is read straight out of the address",
      watch.feed_for("https://www.youtube.com/playlist?list=PLabcdefghij", {})
      == "https://www.youtube.com/feeds/videos.xml?playlist_id=PLabcdefghij")

check("an uploads playlist works the same way",
      watch.feed_for("https://www.youtube.com/x", {"id": "UUabcdefghij"})
      == "https://www.youtube.com/feeds/videos.xml?playlist_id=UUabcdefghij")

# The channel feed carries the uploads and nothing else, so pointing Shorts at
# it would report the wrong videos while looking like it was working.
for tab in ("shorts", "streams", "live", "community"):
    check(f"the {tab} tab does not borrow the channel feed",
          watch.feed_for(f"https://www.youtube.com/channel/{CHANNEL}/{tab}",
                         {"channel_id": CHANNEL}) == "")

check("another site has no feed to read",
      watch.feed_for("https://vimeo.com/12345", {"id": "12345"}) == "")
check("and a video id is not a playlist id",
      watch.feed_for("https://www.youtube.com/watch?v=abc", {"id": "abc"}) == "")


print("\n-- reading it -------------------------------------------------------")

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Uploads</title>
  <entry>
    <id>yt:video:aaaaaaaaaaa</id>
    <yt:videoId>aaaaaaaaaaa</yt:videoId>
    <title>First video</title>
    <published>2026-09-03T10:00:00+00:00</published>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/aaaaaaaaaaa/hq.jpg"/>
    </media:group>
  </entry>
  <entry>
    <id>yt:video:bbbbbbbbbbb</id>
    <yt:videoId>bbbbbbbbbbb</yt:videoId>
    <title>Second video</title>
    <published>2026-09-02T10:00:00+00:00</published>
  </entry>
  <entry>
    <title>Something with no video id at all</title>
  </entry>
</feed>
"""


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self, _n=None):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


watch.urllib.request.urlopen = lambda *a, **k: FakeResponse(FEED)

entries = watch.read_feed("https://example.invalid/feed")
check("every entry with a video id is read", len(entries) == 2, entries)
check("in the order the feed gives them",
      [e["id"] for e in entries] == ["aaaaaaaaaaa", "bbbbbbbbbbb"])
check("with a watchable address built from the id",
      entries[0]["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa")
check("and the thumbnail where there is one",
      entries[0]["thumbnail"].endswith("hq.jpg"))
check("an entry with no thumbnail is still read", entries[1]["thumbnail"] == "")
# The feed does not carry a duration. Inventing one would be worse than the
# gap, because every filter built on it afterwards would inherit the lie.
check("duration is left empty rather than guessed",
      all(e["duration"] is None for e in entries))


print("\n-- how often ---------------------------------------------------------")

engine.save_settings({"watch_minutes": 15})
check("a followed thing with a feed can be checked every 15 minutes",
      watch.interval({"feed": "https://example.invalid/f"}) == 900)
# The engine path is the one a site can object to, so the setting cannot lower
# it. This is the guard that lets the fast choices exist at all.
check("one without a feed keeps the six-hour floor",
      watch.interval({"feed": ""}) == watch.SLOW_FLOOR * 60)

engine.save_settings({"watch_minutes": 1440})
check("a slower choice applies to both",
      watch.interval({"feed": "x"}) == 86400 and watch.interval({"feed": ""}) == 86400)

# Older installs said it in hours, and the file is what has to be asked -
# load_settings has already put the new key into the merged copy, so checking
# that copy would never see the key missing. The settings file is written by
# hand here for exactly that reason.
import json                                                 # noqa: E402


def as_saved(**fields):
    with open(engine.settings_file(), "w", encoding="utf-8") as fh:
        json.dump(fields, fh)


as_saved(watch_hours=6)
check("an older file's hours are read as minutes", watch.minutes() == 360)

as_saved(watch_hours=24)
check("and a slow choice stays slow", watch.minutes() == 1440)

# 48 hours is not on the list any more. The nearest thing we still offer that
# is no faster than what they asked for is 24 - never something faster.
as_saved(watch_hours=48)
check("an interval we dropped lands on the closest one we kept",
      watch.minutes() == 1440)

as_saved(watch_hours=6, watch_minutes=15)
check("but a file that already says minutes is left alone",
      watch.minutes() == 15)

as_saved(watch_minutes=7)
check("and a value we do not offer still falls back to the default",
      watch.minutes() == watch.DEFAULT_MINUTES)


print("\n-- what gets checked next --------------------------------------------")

now = 1_800_000_000.0
old_full = now - watch.FULL_EVERY - 1

due, full = watch._due([{"id": "a", "feed": "f", "checked": now,
                         "full_checked": old_full}], now)
check("a feed that has not had a full check in a week gets one",
      due is not None and full)

due, full = watch._due([{"id": "a", "feed": "f", "checked": now,
                         "full_checked": now}], now)
check("and nothing is due when nothing is due", due is None)

due, full = watch._due([{"id": "a", "feed": "f", "checked": 0,
                         "full_checked": now, "paused": True}], now)
check("a paused item is never due", due is None)

due, full = watch._due([{"id": "a", "feed": "f", "checked": 0,
                         "full_checked": now}], now)
check("an ordinary check is not a full one",
      due is not None and not full)


print("\n-- downloading on its own --------------------------------------------")

class FakeManager:
    def __init__(self):
        self.added = []

    def add(self, **kwargs):
        self.added.append(kwargs)
        return types.SimpleNamespace(id=str(len(self.added)), status="queued")


fake = FakeManager()
sys.modules["app"] = types.SimpleNamespace(manager=fake, tray_app=None)

FOUND = [{"id": str(n), "url": f"https://www.youtube.com/watch?v={n}",
          "title": f"video {n}", "thumbnail": ""} for n in range(10)]

check("a followed channel downloads nothing by default",
      watch._auto_download({"auto": False}, FOUND, {"watch_auto": True}) == [])
check("and nothing when only the channel is switched on",
      watch._auto_download({"auto": True}, FOUND, {"watch_auto": False}) == [])

taken = watch._auto_download({"auto": True}, FOUND, {"watch_auto": True,
                                                     "default_quality": "1080"})
check("both switches on, and it downloads", len(taken) > 0)
check("but never more than the cap in one check",
      len(taken) == watch.AUTO_MAX_PER_CHECK, len(taken))
check("taking the newest first",
      [t["id"] for t in taken] == ["0", "1", "2"])
check("at the quality that was set",
      all(a["quality"] == "1080" for a in fake.added))


print("\n-- one channel's own settings ----------------------------------------")

kept = watch.clean_options({"every": 15, "quality": "1080",
                            "dest_dir": "D:\\Clips", "after": "2026-01-01",
                            "title_has": "review", "title_not": "trailer"})
check("everything usable is kept", len(kept) == 6, kept)

check("an interval we do not offer is dropped",
      "every" not in watch.clean_options({"every": 7}))
check("a quality that does not exist is dropped",
      "quality" not in watch.clean_options({"quality": "9000"}))
check("a date that is not a date is dropped",
      "after" not in watch.clean_options({"after": "last tuesday"}))
check("an empty value is not stored as an empty value",
      watch.clean_options({"dest_dir": "  ", "title_has": ""}) == {})
check("and a key nobody recognises never gets in",
      "sudo" not in watch.clean_options({"sudo": "yes"}))

# The per-channel interval beats the setting, but the floor still wins over
# both - that floor is the reason the fast choices can exist at all.
engine.save_settings({"watch_minutes": 1440})
check("a channel can be checked more often than the setting",
      watch.interval({"feed": "f", "opts": {"every": 15}}) == 900)
check("but not one the engine has to fetch",
      watch.interval({"feed": "", "opts": {"every": 15}}) == watch.SLOW_FLOOR * 60)


print("\n-- rules about which videos count -------------------------------------")

def entry(title, published=0):
    return {"id": "x", "title": title, "published": published}


plain = {"opts": {}}
check("with no rules everything counts", watch.wanted(plain, entry("anything")))

has = {"opts": {"title_has": "review, tested"}}
check("a word rule lets a matching title through",
      watch.wanted(has, entry("Laptop review 2026")))
check("and turns away one that matches none of them",
      not watch.wanted(has, entry("Vlog number nine")))
check("matching is not case sensitive",
      watch.wanted(has, entry("LAPTOP REVIEW")))

nope = {"opts": {"title_not": "trailer, #shorts"}}
check("a banned word turns a video away",
      not watch.wanted(nope, entry("Official trailer")))
check("and leaves the rest alone", watch.wanted(nope, entry("Full episode")))

JAN = 1_767_225_600.0          # 2026-01-01, near enough for a comparison
dated = {"opts": {"after": "2026-01-01"}}
check("something older than the date is turned away",
      not watch.wanted(dated, entry("old", JAN - 86400 * 30)))
check("something newer is not", watch.wanted(dated, entry("new", JAN + 86400 * 30)))
# The engine path has no date at all. Dropping everything a rule cannot judge
# would hide videos rather than filter them.
check("and something with no date at all still counts",
      watch.wanted(dated, entry("undated", 0)))


print("\n-- a filtered video does not come back --------------------------------")

# This is the whole reason `known` and `fresh` are separate lists: a video a
# rule turned away has still been looked at.
src_check = (SRC / "watch.py").read_text(encoding="utf-8")
check("everything unseen is written down before the rules run",
      "seen.append(entry[\"id\"])" in src_check
      and src_check.index("seen.append") < src_check.index("if not wanted("))


print("\n-- the feed's date ----------------------------------------------------")

check("the published date is read out of the feed",
      entries[0]["published"] > 0)
check("for every entry that has one", entries[1]["published"] > 0)

ODD = FEED.replace(b"2026-09-03T10:00:00+00:00", b"the third of September")
watch.urllib.request.urlopen = lambda *a, **k: FakeResponse(ODD)
odd = watch.read_feed("https://example.invalid/feed")
# Zero means unknown, not 1970 - every date rule has to be able to tell the
# difference, or an unreadable date would look like the oldest video there is.
check("a date that cannot be read becomes unknown, not ancient",
      odd[0]["published"] == 0.0)
check("and the entry is still read", odd[0]["id"] == "aaaaaaaaaaa")
watch.urllib.request.urlopen = lambda *a, **k: FakeResponse(FEED)


print("\n-- taking the list somewhere else -------------------------------------")

watch.save({"items": [watch._migrate({
    "id": "one", "url": "https://www.youtube.com/channel/" + CHANNEL,
    "title": "A channel", "auto": True, "known": ["a"] * 400,
    "opts": {"quality": "1080"}})]})

rows = watch.export_items()
check("the list exports one row per followed thing", len(rows) == 1)
check("with its address, its options and its download switch",
      rows[0]["url"].endswith(CHANNEL) and rows[0]["opts"] == {"quality": "1080"}
      and rows[0]["auto"] is True)
# Four hundred video ids mean nothing on another machine, where the first
# check writes its own baseline anyway.
check("and without the seen list, which would not travel",
      "known" not in rows[0])


print("\n-- the panel and the reader agree ------------------------------------")

# The panel is authored in JavaScript and read in Python, and nothing makes
# the two agree by itself. A field named on one side and not the other fails
# in the quietest possible way: the box is there, it accepts a value, and the
# value is dropped on the way in.
APP_JS = (SRC / "static" / "js" / "app.js").read_text(encoding="utf-8")
import re as _re                                            # noqa: E402

block = _re.search(r"var WATCH_OPTS = \[(.*?)\];", APP_JS, _re.S)
check("the panel's field list is where this expects it", bool(block))
authored = set(_re.findall(r'key: "([a-z_]+)"', block.group(1) if block else ""))
check("the panel offers at least one field", authored, authored)
check("every field the panel offers is one the reader keeps",
      authored <= set(watch.OPTION_KEYS),
      sorted(authored - set(watch.OPTION_KEYS)))
check("and every option the reader keeps has a field",
      set(watch.OPTION_KEYS) <= authored,
      sorted(set(watch.OPTION_KEYS) - authored))

# The same trap one level up: a class used in the script with no rule in the
# stylesheet renders as an unstyled block. The repo already has a test for
# that across the whole file; this is the one this feature added.
CSS = (SRC / "static" / "css" / "app.css").read_text(encoding="utf-8")
check("the panel's own class is styled", ".watch-opts" in CSS)


print("\n-- the promise -------------------------------------------------------")

src = (SRC / "watch.py").read_text(encoding="utf-8")
page = (SRC / "templates" / "index.html").read_text(encoding="utf-8")

# The old text said downloading could not happen at all. Now it can, if it is
# asked for twice - so any sentence still claiming otherwise is a lie the code
# would be telling on the screen.
check("the file no longer claims it never downloads",
      "never downloads anything by itself" not in src)
check("and neither does the screen",
      "It never downloads anything on its own" not in page)
check("what the screen says instead is the true version",
      "unless you turn that on" in page)
check("and the warning shown before it is switched on says it too",
      "Nothing downloads unless you say so" in page)


print("\n-- an older watch.json still opens ------------------------------------")

old = watch._migrate({"id": "x", "url": "u", "checked": 123})
check("the new fields are filled in rather than missing",
      old["feed"] == "" and old["feed_fail"] == 0 and old["auto"] is False)
check("and a file with no full check yet inherits its last one",
      old["full_checked"] == 123)


print("\n" + "=" * 68)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
for name in FAIL:
    print("   FAILED: " + name)
print("=" * 68)

shutil.rmtree(SANDBOX, ignore_errors=True)
sys.exit(1 if FAIL else 0)
